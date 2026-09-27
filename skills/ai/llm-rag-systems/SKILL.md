---
name: llm-rag-systems
description: Build and debug retrieval-augmented generation - chunking that preserves meaning, embedding and dimensionality tradeoffs, hybrid BM25 plus vector search, reranking, query rewriting, citation grounding, and diagnosing whether a bad answer was a retrieval miss or a generation miss. Use when a RAG or chat-with-docs feature returns wrong, outdated, or ungrounded answers, when asked about chunk size, embeddings, vector search, pgvector, RAGAS, "it says the doc has it but the bot doesn't", or "the answer is hallucinated".
compatibility: Provider-agnostic. Vector store, embedding model, and reranker APIs change often; check current docs and benchmark on your own corpus.
metadata:
  version: "1.0"
---

# LLM RAG Systems

RAG is a **retrieval** system with a language model bolted on at the end. Almost
every "the AI got it wrong" bug is in the retrieval half, and you cannot find out
which half failed without measuring the two halves separately.

## Triage first: retrieval miss or generation miss?

Run this before changing anything. Take 20 questions where you already know
the right answer, and check whether the gold document was in the top-k.

- **Not in top-k → retrieval problem.** Chunking, embeddings, query, filters,
  or reranking. No amount of prompt work fixes this.
- **In top-k, answer still wrong → generation problem.** The context was
  present and the model ignored it, was drowned out, or the question was
  ambiguous. Now prompt, context ordering, and citation forcing apply.
- **Answer is right but ungrounded** → the model answered from parametric
  memory. Force citation and check that every claim maps to a retrieved span.

Read `references/retrieval-failure-taxonomy.md` for the full failure-mode
taxonomy with symptoms and fixes. It is the core diagnostic table; come back to
it whenever quality is wrong rather than merely mediocre.

## Pipeline

```
question
  -> query transformation (rewrite / decompose / expand with history)
  -> filters (tenant, ACL, date)
  -> hybrid retrieval: BM25 + dense vector, merged
  -> (optional) reranker over top-N
  -> context assembly: dedupe, reorder, budget to tokens
  -> generation with citations and an abstain path
```

Do not skip step 1 or step 5. The two most common RAG failures are a bad
query and a bad context window.

## Chunking

Fixed-size chunks with overlap are the default because they are trivial to
implement, not because they are good. They cut mid-sentence, mid-table, and
mid-argument, and an embedding of half a paragraph is close to meaningless.

Default recommendation, in order:

1. **Structure-aware chunking.** Split on real boundaries: markdown headings,
   HTML sections, PDF pages, code symbols, API endpoints, dialogue turns. Then
   apply a token budget on top. This is the house default.
2. **Recursive character splitting with a generous budget.** Use a real
   separator ladder (`\n\n`, `\n`, `. `, space) rather than a hard cut. ~500-800
   tokens with ~10-15% overlap.
3. **Sentence/paragraph packing with a parent-child index.** Embed small
   units for precision, return the larger parent section so the model has the
   full argument.

Rules:

- **Chunk size is a precision/recall dial.** Small chunks retrieve precisely and
  lose context; large chunks carry context and dilute the embedding. Tune it
  against a retrieval metric, not a vibe.
- **Overlap is insurance against boundary cuts, not a substitute for good
  boundaries.** 10-15% is plenty; heavy overlap inflates index size and
  duplicates results in context.
- **Prepend context to every chunk** before embedding. For a support corpus,
  embedding `"Acme Cloud — Billing FAQ\n\nTo reset your card..."` beats
  embedding the paragraph alone, because short chunks have almost no signal on
  their own. Metadata headers are cheap and reliably improve recall.
- **One table per chunk.** A chunk containing a table plus surrounding prose
  retrieves badly and confuses the model. Convert the table to text and
  describe it in the header.
- **Store the chunk's source coordinates** (doc id, heading path, page/line)
  at index time. You cannot produce citations you never recorded, and you
  cannot re-chunk later without a reindex.
- **Never re-chunk casually.** It invalidates the whole index. Re-chunking is a
  migration with a rebuild, an eval run, and a cutover.

## Embeddings and dimensionality

- **Match embed model to query language and domain**, and re-embed the *whole*
  corpus when you change it. Mixing embedding models in one index gives you
  nonsense comparisons.
- **Dimensionality is a storage/latency knob, not a quality knob in itself.**
  Smaller embeddings cut index size and search cost; quality is decided by the
  model. Compare quality at a couple of sizes rather than assuming smaller is
  worse.
- **Normalise when using cosine/IP inner-product indexes.** Mixing a normalised
  vector with a distance metric silently degrades results.
- **MTEB is a starting point, not an answer.** Embedding leaderboards transfer
  badly to your corpus. Run a 100-query internal relevance check: embed each
  query, retrieve, and have a human or judge mark relevant/not. That 100-query
  set is your real benchmark and it will contradict the leaderboard more often
  than you expect.
- **Document-side vs query-side prefixes**: some embedding families expect an
  instruction on the query only. Using them backwards degrades quality.

## Hybrid search and reranking

Dense vectors are semantic; they are weak at exact tokens — part numbers, error
codes, `ERR_AUTH_401`, surnames, negations, versions. BM25 is the opposite.
Real queries contain both, so run both and merge.

- **BM25**: any keyword engine (Postgres full-text, Elasticsearch, OpenSearch,
  SQLite FTS). Tunable `k1` and `b`; defaults are fine.
- **Merge with Reciprocal Rank Fusion (RRF)** rather than normalising scores
  and adding them — RRF is rank-based, so it does not need score calibration
  between two incomparable systems:

  ```
  rrf(d) = sum over rankers of  1 / (k + rank_r(d))      # k ~ 60
  ```

- **Rerank the top ~50-200 with a cross-encoder** (a model that reads the query
  and the passage *together*). This is the single highest-leverage accuracy win
  in most RAG systems and the most commonly skipped. Rerank cost is per
  document, so it applies to a shortlist, not the corpus.
- **Cut the shortlist to what fits the context budget.** Retrieving 50 chunks
  and passing 50 chunks to the model is worse than retrieving 50 and passing 6:
  recall@50 and precision@6 are different numbers, and the model is scored on
  the second.

## Query transformation

Users ask follow-ups. `"and what about the EU one?"` embeds nothing useful.

- **Condense** a question with conversation history into a standalone query
  before retrieval.
- **Rewrite** vague questions into concrete ones ("the latest" →
  `2026 policy`, and add a date filter).
- **Decompose** only for genuinely multi-hop questions, and only when you can
  fan the sub-queries out in parallel. Sequential decomposition multiplies latency
  and is usually worse than a single well-formed query.
- **Multi-query / HyDE** (generate a hypothetical answer, embed that) help when
  recall is the bottleneck. They cost one extra generation per query — measure
  before adopting.
- **Route first**: a classifier that decides "chitchat / this needs the docs /
  this needs the database" saves a retrieval call on most traffic.

## Context assembly

This is where a correct retrieval gets thrown away.

- **Budget to tokens, not to chunk count.** Model context is finite; leave room
  for the answer and the instructions.
- **Dedupe by document and near-duplicate text.** BM25 and dense retrieve the
  same paragraph twice constantly.
- **Put the strongest chunk where you said you would.** Models weight the
  beginning and the end of context most; the middle gets the least attention
  ("lost in the middle"). If you are cutting, cut the middle, not the end.
- **Label every chunk with its source** and instruct the model to cite. This is
  what makes hallucination detectable instead of merely suspected.
- **Interleave, don't concatenate by source**, when you have several documents,
  so one verbose document does not own the window.
- **Include a closing instruction** after the context: "If the answer is not in
  the context, say so and name what is missing."

## Citation grounding

Citations are the cheapest hallucination detector you have, and users will check
them. Requirements:

- Every claim in the answer maps to a specific retrieved chunk, cited by id.
- The cited chunk actually supports the claim — verify this, do not assume it.
  Models cite correctly-formatted but irrelevant chunks.
- Post-hoc: strip or annotate unsupported sentences, or refuse the answer if
  any sentence is unsupported. This is a *cheap deterministic filter* on top of
  a generative model, and it works.
- Show the source text in the UI. Citations the user cannot open are
  decoration.

## Evaluating a RAG system

Measure retrieval and generation separately. RAG-as-a-whole metrics hide which
half broke.

**Retrieval metrics** (deterministic, cheap, and the ones to optimise first):

- **Recall@k** against a labelled gold doc/chunk per question.
- **MRR / nDCG@k** for *ranked* quality — recall@5 is insensitive to whether the
  gold chunk was 1st or 5th, which matters a lot to the final answer.
- **Context precision**: what fraction of retrieved chunks were useful.
- Build the labelled set from real questions first (log mining), then add
  synthetic ones for coverage. 100-200 labelled questions is enough to steer.

**Generation metrics**:

- **Groundedness**: are claims supported by the given context?
- **Answer correctness** vs the known right answer.
- **Citation accuracy and completeness** — a metric most teams skip and users
  notice.
- **Abstention accuracy**: does it refuse when it should, and answer when it
  should? Measure both; a system that always refuses scores 0 on usefulness.
- **Format/schema validity** if the output is structured.

Deterministic checks first: exact match / F1 for short factual answers,
regex and schema validation, citation-id existence. Model-graded second
(`skills/ai/llm-evaluation/SKILL.md`). Judge-based metrics need calibration
against human labels or they are just vibes with extra steps.

## Operational reality

- **The index is a derived artefact.** Source of truth is the source documents.
  Rebuild must be a one-command, idempotent, incremental job with a full-rebuild
  path; test it.
- **Freshness is a product decision.** Embedding on write, a periodic
  incremental sync, or a nightly full rebuild. For anything users edit, on-write
  is the only one they will trust.
- **Deleting a document must delete its chunks.** Orphaned chunks in the index
  are how a "we deleted that doc" ticket becomes a security incident.
- **Access control belongs in retrieval.** Filter by tenant/ACL *before* the
  model sees anything, in the query itself. Post-filtering leaks: the top-k may
  contain rows the user cannot read and the model will quote them.
- **Vector search is a candidate generator, not a database.** Postgres is fine
  up to a few million vectors with a tuned index; past that, a dedicated
  engine. Benchmark your own filter selectivity — HNSW/IVF behaviour depends on
  it.

## Gotchas

- **Similarity scores are not probabilities and are not comparable across
  queries.** Never threshold on a raw score without calibrating on your data.
- **Your embedding model has a max input length.** Long chunks get truncated
  silently, and the tail is the part that answered the question.
- **Metadata filtering can destroy ANN recall** if it happens post-search, and
  can be very slow if it happens pre-search without an index. Test with your
  real filter cardinality.
- **Hybrid needs both sides.** Adding BM25 to a dense-only index and assuming
  improvement is how you end up with slower queries and no gain.
- **A reranker is a model in your latency path.** Measure it end to end.
- **Chunk-level dedupe by hash, not by embedding similarity**, if you need it to
  be exact and cheap.
- **Don't eval on questions you wrote from reading the docs.** They are easier
  and more lexical than real user phrasing.

## Checklist

- [ ] 100+ labelled questions with a gold document/chunk
- [ ] Recall@k and MRR measured before any optimisation
- [ ] Chunking follows document structure; source coordinates stored
- [ ] Hybrid retrieval with RRF merge, reranker over the shortlist
- [ ] Context deduped, ordered, and budgeted to tokens
- [ ] Citations required and verified; abstain path tested
- [ ] ACL filtering applied inside the retrieval query
- [ ] Reindex path is idempotent and tested

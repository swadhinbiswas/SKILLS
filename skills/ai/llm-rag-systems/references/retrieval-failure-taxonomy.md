# RAG failure-mode taxonomy

Every symptom maps to a layer. Diagnose by layer, because the fixes are not
interchangeable and a prompt change to a chunking problem wastes a week.

| # | Symptom | Layer | Root cause | Fix | Verify with |
|---|---|---|---|---|---|
| 1 | Bot says the doc does not contain it, but it does | Retrieval | Gold chunk below top-k | Raise k, improve chunking, add BM25, rerank | Recall@k / MRR |
| 2 | Bot answers about the wrong product/tenant | Retrieval | No tenant filter, or filter applied after search | Filter in-query, before ranking | Cross-tenant probe test |
| 3 | Exact IDs, error codes, versions are missed | Retrieval | Dense-only search on lexical tokens | Add BM25, merge with RRF | Query set of exact-token lookups |
| 4 | Answers are right but from stale content | Freshness | No reindex on write | Embed on write, or incremental sync | Time-travel query test |
| 5 | Deleted doc still quoted | Lifecycle | Orphaned chunks | Delete-by-source-id, reconcile job | Delete then re-query |
| 6 | Correct chunk retrieved, answer ignores it | Generation | Instruction buried; no citation duty; too many competing chunks | Output contract, citation requirement, shrink context | Cited-support rate |
| 7 | Answer is fluent and wrong | Generation | Context truncated / lost in the middle | Increase k modestly, order strongest first, budget tokens | Count chunks actually sent |
| 8 | Right answer, no trace of it | Retrieval | Embedding truncated long chunks | Respect embed max input; re-chunk and reindex | Compare max chunk len to limit |
| 9 | Semantically close but wrong doc | Chunking | Chunks are half-paragraphs | Structure-aware chunking, context header prefix | Relevance judgement on 100 queries |
| 10 | One document dominates all results | Assembly | No dedupe or per-doc cap | Dedup near-dupes, cap per source, diversify | Per-doc share of top-k |
| 11 | Follow-up questions return garbage | Query | Raw conversational query embedded | Condense/decompose with history | Multi-turn eval set |
| 12 | System answers every question | Abstention | No abstain path in prompt | Require `insufficient_context` field | Abstention accuracy on OOD set |
| 13 | Hallucinated citation id | Citations | Ids not validated | Validate ids post-hoc, drop unknown | Invalid-id rate |
| 14 | Citation exists but does not support claim | Citations | Model citing loosely | Post-hoc support check, filter unsupported sentences | Manual review of 50 answers |
| 15 | Great offline, poor in prod | Eval set | Evals on synthetic self-authored questions | Mine real logs for the eval set | Compare offline vs logged pass rate |
| 16 | Recall dropped after a filter change | Indexing | Filter plan forces exhaustive scan | Add index, check selectivity, re-benchmark | Latency at p95 with filter |
| 17 | Answers fine on English, bad on another language | Embedding | Multilingual model or cross-lingual mismatch | Multilingual embeddings, reindex | Per-language recall@k |
| 18 | Long documents never retrieved | Chunking | Chunk > embed max, truncated | Re-chunk; store and check token length | Chunk length histogram |

## Diagnosis order

1. Does the gold chunk appear in the candidate set at all? (Recall@100)
2. If yes, does it survive into the context sent to the model? (assembly)
3. If yes, does the answer cite it? (generation + citations)
4. If yes, is the citation supportive? (grounding)

Bugs are overwhelmingly at step 1 or 3. Do not start at step 4.

## Minimum instrumentation

Log per request: question, rewritten query, filters applied, candidate ids and
scores (both retrievers), post-rerank order, ids actually sent to the model,
token count, answer, cited ids, latency per stage, eval score when available.
Without the candidate list you cannot distinguish failure 1 from failure 9.

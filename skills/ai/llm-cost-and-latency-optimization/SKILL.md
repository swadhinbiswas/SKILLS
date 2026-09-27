---
name: llm-cost-and-latency-optimization
description: Cut LLM spend and time-to-first-token with a real cost model - token accounting, prompt caching and cache_control, batching, model routing by difficulty, streaming, parallel and speculative calls, and context reduction. Use when an LLM bill is too high, when a feature is too slow, when someone says "tokens are expensive", "rate limit", "429", "TTFT", "p95 latency", "cache the prompt", or asks to cut inference cost.
compatibility: Provider-agnostic. Pricing, caching, and batch APIs differ per vendor and change often - read current pricing pages before quoting numbers.
metadata:
  version: "1.0"
---

# LLM Cost and Latency Optimization

Optimize with a model, not with intuition. Token counts are the unit of cost and
latency; everything below is a way to change that number.

## The cost model

```
cost_per_call = (in_tokens * p_in + out_tokens * p_out + cached_in * p_cached) / 1e6

monthly = cost_per_call * calls_per_month
```

Then the number that actually matters:

```
monthly = tokens_per_call * calls_per_day * 30 * (p_in + p_out) / 1e6
```

**Output tokens cost several times more than input tokens** on essentially every
provider. `max_tokens` is therefore the single most under-used cost control: it
is a ceiling, not a target, and an unset or generous one lets a runaway
generation cost 10x what the task needs. Set it per task from your eval set's
observed output length (p95, with headroom), not from a round number.

Build a per-feature table before touching anything:

| Feature | calls/day | in_tok | out_tok | $/day | % of bill |
|---|---|---|---|---|---|
| summarize_ticket | 40,000 | 1,800 | 260 | | |
| chat_answer | 3,000 | 6,500 | 700 | | |

**Optimize the largest row, not the easiest one.** A 40x-call summarizer with a
tight prompt is worth more than heroics on a rare path.

**Latency decomposes as**: TTFT (queue + prefill) + decode (output tokens ×
per-token time). These are different problems with different fixes:
prefill-bound calls (long input, short output) want fewer input tokens and
prompt caching; decode-bound calls (long output) want fewer output tokens,
streaming, or a smaller/faster model.

## Levers, ordered by payoff

### 1. Stop sending what the model already knows

Often the biggest win, and it requires no vendor feature.

- **Delete the history that does not matter.** Last-20-turn transcripts sent
  wholesale is a common and enormous waste; summarise old turns instead
  (`skills/ai/llm-agents-and-tool-use/SKILL.md`).
- **Truncate retrieved context to the budget that actually helps.** Dropping
  from 20 chunks to 6 often *improves* accuracy and halves input tokens.
- **Don't put the system prompt in every user message.** Use the API's
  persistent system message / prompt template feature where one exists.

### 2. Prompt caching

Providers offer a cache for a **prefix** of the request (exact-match prefix
reuse; semantics and pricing differ). The rules are consistent enough to plan
around:

- **The cache is prefix-based, so order matters.** Put the big stable content
  *first*: system prompt, then the static instruction block, then the retrieved
  documents or tool definitions, and the volatile user text *last*.
- **Anything that changes breaks the prefix from the change onward.** A
  timestamp in the middle of the instructions, or a per-request ID, destroys the
  whole hit. Do not put them early.
- **You mark the cache boundary explicitly** in most SDKs (an ephemeral/cache
  control marker, or a TTL parameter). Verify the exact mechanism and minimum
  cacheable length for your provider - short prompts often fall below the
  minimum and silently never cache.
- **Measure actual hit rate**; do not assume. Log cached vs fresh input tokens
  per call. A cache you never check is a guess.
- Caching is a hit-rate × savings trade, so it pays off with high volume and
  high stability, and does nothing for a low-traffic feature with a per-request
  changing prefix.

### 3. Model routing

Split by difficulty and send simple work to a small, fast, cheap model.

```
classify_intent(user) -> small model      # cheap, fast, high volume
draft_reply(context)  -> large model      # only when confidence or stakes are high
```

- **Route on a cheap signal first**: a small classifier, a rule, a keyword, or
  the previous turn's complexity. Do not ask a large model "how hard is this?".
- **Have a small model do the work and a large model do the check** when the
  small model is fast and the check is cheap; escalate to the large model only
  on failure.
- **Measure the routing boundary.** Send a labelled set through both tiers and
  find the threshold where the small model's accuracy drop is acceptable. This
  is an eval, not a guess.
- **Small models degrade on long context and on strict formats.** If your small
  tier produces invalid JSON or ignores the schema, the routing is wrong.
- Keep a **fallback to the large model** on timeout, refusal, or schema-invalid
  output, so a routing miss degrades instead of failing.

### 4. Batching and async

- **Batch offline or async work.** Sentiment scoring, classification,
  summarisation of backlogs, and evaluations are batch jobs: submit many
  requests as one job with a completion window and collect later. Providers
  discount batch and give you a longer turnaround; check the current numbers.
- **Async at the application layer.** A request handler that blocks on a model
  call wastes the concurrency your whole runtime is paying for.
- **Queue anything that does not need to be in the request path.** Enqueue,
  return a job id, and process with a worker pool whose concurrency is tuned to
  your actual rate limit, not to your CPU count.
- **Connect to the model with pooled HTTP connections and keep-alive.** A new
  TLS handshake per call is real latency and real cost.

### 5. Streaming

Streaming does not make the total faster, but it fixes **time-to-first-token**,
which is what the user feels. For anything over ~1 second of generation, stream;
otherwise users read it as a hang.

- Stream from the server, buffer in your service, and forward chunks to the
  client (SSE or WebSocket) - do not wait for the full completion to start
  forwarding.
- **Stream aggressively only with strict schema modes** - partial JSON is not
  parseable by a client until complete. For machine consumers, buffer.
- **Send a cheap acknowledgement first** (a status line, a skeleton, a "searching
  your docs" step) so perceived latency is bounded even on slow paths.

### 6. Parallel and speculative work

- **Parallel independent calls** and take the first useful result, cancelling
  the rest - hedge the slow tier with the fast tier. Cheap when the fast tier is
  an order of magnitude cheaper.
- **Speculative decoding** (a small draft model proposes tokens, a large model
  verifies in one pass) is a real technique that can cut decode latency. It is
  exposed by some inference stacks and absent from most hosted APIs; if you run
  your own inference, look into it, otherwise skip this section.
- **Parallel tool calls in an agent** - see
  `skills/ai/llm-agents-and-tool-use/SKILL.md`.
- **Two-stage: cheap filter then expensive act.** Recall 200 candidates cheaply,
  score 200 expensively only where it changes a user-visible outcome.

### 7. Quantisation and self-hosting (only at scale)

- Below roughly a million calls a month, hosted APIs with a small model beat
  self-hosting on cost. Above it, or for data-residency reasons, serving an
  open-weights model (quantised weights, vLLM/SGLang/TensorRT-LLM) is worth the
  ops. Do not self-host "to save money" at small volume - the GPU and the
  on-call cost are larger than the tokens.

## Rate limits and 429s

- **Read the headers**, not the docs: providers return limit and remaining
  budget, and a queue token for the async APIs. Build adaptive concurrency from
  the live signal: on a `429`, drop concurrency and back off with full jitter;
  on sustained success, ramp back up slowly.
- **Queue for anything non-interactive** so user requests never compete with
  batch work.
- **Budget per tenant/feature**, not just globally. One viral feature should not
  spend everyone else's quota.

## Measurement

You cannot optimize what you do not attribute per call:

- Log per call: `model`, `in_tokens`, `cached_in_tokens`, `out_tokens`,
  `latency_ms` **and** `ttft_ms`, `prompt_version`, `retries`, `finish_reason`,
  `cached` flag, plus an eval score when available.
- Report **p50 and p95, not means**. Latency and token usage are heavy-tailed
  and a mean hides the users who are actually suffering.
- Track **cost per successful outcome** (per resolved ticket, per merged PR),
  not cost per call. A cheaper model that increases rework is not a saving.
- Watch `finish_reason: length` as a first-class metric: it means the task
  exceeded its output budget, which is both a quality bug and a cost signal.

## Gotchas

- **Cheap model + long prompt can cost more than an expensive model + short
  prompt.** Price the *combination*; input tokens dominate on retrieval-heavy
  paths.
- **Caching a prefix that varies in the first 100 tokens caches nothing.**
  Layout for cache locality or do not bother.
- **Streaming hides latency, it does not remove it** - and with a slow model the
  first token may still take seconds. Fix prefill, don't just stream.
- **Batch throughput numbers are best-case.** They assume a full batch; a
  partially filled batch wastes the discount.
- **A retry storm against a rate-limited API turns a soft limit into a hard
  outage.** Back off with full jitter and cap total retries by deadline
  (`skills/architecture/resilience-patterns/SKILL.md`).
- **Truncation changes behaviour, not just cost.** Cutting a prompt that was
  carrying an instruction silently changes the output. Re-run evals when you
  change the budget.
- **Provider "flex"/"batch"/"priority" tiers change pricing and limits
  frequently.** Verify current tiers before promising a number to anyone.

## Checklist

- [ ] Per-feature token and dollar table exists, largest row identified
- [ ] `max_tokens` set per task from p95 observed output length
- [ ] Longest stable prefix first; volatile user text last
- [ ] Cache hit rate measured, not assumed
- [ ] Routing threshold set from a measured eval, with fallback to the large model
- [ ] Non-interactive work batched or queued
- [ ] Streaming on for anything over ~1s; buffered for machine consumers
- [ ] Independent work parallel or hedged; deadline-bounded retries with jitter
- [ ] p50/p95 latency and TTFT logged per call, with `finish_reason`
- [ ] Cost and rate limits budgeted per feature, with a kill switch

---
name: llm-application-architecture
description: Design the shape of an LLM-backed product - sync vs async UX for long generations, streaming and partial results, semantic caching, fallbacks when the model is down, prompt versioning and rollback, and the observability to debug it (prompts, latency, tokens, evals). Use when designing or reviewing an LLM feature's backend, when generations take too long, when the model times out, when prompts need versioning or rollback, or when someone asks "how should this be architected", "streaming", "queue the request", "cache LLM responses", "prompt registry", "LLM observability".
compatibility: Provider-agnostic; streaming formats, cache, and deployment topology differ per vendor.
metadata:
  version: "1.0"
---

# LLM Application Architecture

A model call is a **slow, expensive, non-deterministic, non-repeatable
dependency**. Every architectural decision here follows from those four words:
budget for the slow, bound the expensive, contain the non-deterministic, and
make the unpredictable rollback-able.

## Pick the UX shape before writing the endpoint

- **< ~2s expected, user is waiting** → synchronous request/response. A spinner
  is acceptable. Set a hard timeout slightly above p95.
- **~2-15s** → stream, or run async with a progress indicator. Users read
  anything under ~10s as broken without feedback; a stream or a "thinking"
  acknowledgement fixes perception for free.
- **> ~15s** → **async by default**. Enqueue, return a job id, show progress,
  notify on completion (or a link). The request should not hold an HTTP
  connection open for minutes; proxies and load balancers will kill it.
- **Batch / background** → queue always. Never spend interactive capacity on
  work the user is not waiting for.

The decision is a product one, but the backend shape (sync endpoint vs
enqueue-and-poll vs stream) follows mechanically. Default: **anything that can
exceed the gateway timeout is async.**

## Streaming and partial results

- **Stream to the client from your server** (SSE or WebSocket). Buffering the
  full completion and then streaming it to the browser defeats the entire point
  - the user waits for the model, then for your buffer.
- **First acknowledgement early.** Send a status event ("searching your
  docs…", "drafting 3 options…") as soon as you know what stage you are in, so
  perceived latency is bounded even on the slow path.
- **Stream only what is streamable.** Partial JSON is not parseable; for
  machine-consumed output, stream *progress* and deliver the final structured
  object at once. Streaming text a schema then has to stitch is a complexity tax
  for nothing (`skills/ai/structured-output-and-json/SKILL.md`).
- **Idempotent delivery.** Streams reconnect; make the job id the key and let the
  client re-attach and resume from an offset rather than restarting the model
  call.

## Caching

- **Exact-match caching** (prompt+params → response) for repeated identical
  requests: fixtures, tests, re-runs, high-volume templated tasks. Key on the
  full normalised request including model, prompt version, and params - a key
  that omits the model returns stale answers after a model bump.
- **Semantic caching** (embed the query, match near-duplicates above a
  similarity threshold) for chat and support-style traffic with a high
  paraphrase rate. It is powerful and it is dangerous: threshold too low and it
  answers a different question confidently, and it leaks across users and
  tenants. Scope it per-tenant, log hits, and never use it where a stale answer
  causes harm.
- **Prefix caching** (provider-side) cuts cost on long stable prompts
  (`skills/ai/llm-cost-and-latency-optimization/SKILL.md`) - that is cost, not
  a response cache.
- **Cache negative results briefly and the success path longer.** Cache with
  TTLs; a prompt-bump invalidation (bust caches on prompt version) is mandatory.

## Fallbacks and degradation

The model will be slow, rate-limited, or down. Design the degraded path first.

- **Layered fallback**: model tier A (best) → tier B (cheaper/faster) →
  deterministic/legacy response (template, search results, rule-based answer,
  "we're having trouble, here's a link"). Each drop must still be a *worse
  answer, not an error*.
- **Timeouts with jittered retries** at the model call, bounded by the request
  deadline (`skills/architecture/resilience-patterns/SKILL.md`).
- **Circuit breaker** around the model: after N failures, stop calling for a
  cooldown and serve the fallback. Without it, an outage turns into a
  self-inflicted DoS through retries.
- **Bulkhead**: a separate concurrency pool for LLM calls so a slow model cannot
  starve the rest of your app.
- **Queue for backpressure**: when generation capacity is the constraint, the
  queue absorbs bursts instead of collapsing the service; interactive requests
  get priority over batch.

## Prompt versioning and rollback

- **Prompts are versioned source code in git** - never a database row someone
  edits in a dashboard without a review. Code review for a prompt is a real
  practice; apply it.
- **Log the prompt version (and model) on every call, trace, and eval result.**
  A bug report without a version is undebuggable.
- **The version is a first-class input** to your cache key, your eval key, and
  your rollout. Changing the prompt must be able to *roll back* by flipping one
  config/flag.
- **Ship prompts behind a flag with a staged rollout** (internal → small % →
  full) so a prompt change is a deploy like any other, not a big-bang edit.
- **Never edit a prompt in place while a rollout is running.** The system is
  then non-reproducible: two "identical" requests used different prompts.

## Observability: what to log per call

Log these on every model call, correlated by a trace id:

| Field | Why |
|---|---|
| `trace_id`, `user_id` (hashed or coarse), `feature` | Slice by feature and user |
| `model`, `model_params`, `prompt_version` | Reproducibility; the version is the most-missing field |
| `in_tokens`, `cached_in_tokens`, `out_tokens`, `stop_reason` | Cost; `length` stop = truncated = a bug |
| `latency_ms` **and** `ttft_ms`, `queue_ms` | Separates "waiting for the model" from "waiting for our queue" |
| `retries`, `fallback_tier_used`, `circuit_state` | Reliability and degradation |
| final response (PII-scrubbed) or a reference to it | Debugging |
| `eval_scores` when a cheap judge runs inline | Quality over time, per release |
| `error_class` | SLOs; raw stack traces are noise in the aggregate |

- **Never log raw PII** in prompts, responses, or traces - see
  `skills/ai/llm-privacy-and-safety/SKILL.md`.
- **Dashboards that matter**: cost/day by feature, p50/p95/p99 latency and TTFT,
  `stop_reason` distribution, fallback rate, retry rate, refusal/injection rate,
  and the eval score trend. Averages hide the users who are actually hurting;
  watch the percentiles.
- **Trace every agent step** (tool, args hash, result status, tokens) - an agent
  failure is only diagnosable from the trace (`skills/ai/llm-agents-and-tool-use/SKILL.md`).
- **Alert on the model, not just your service**: error rate, p95 latency, and
  cost anomaly per feature. A silent model degradation shows up as a fall-back
  spike, which is the signal.

## Reference architecture (default shape)

```
client
  -> api: validate, rate-limit, check cache
       -> fast path: cache hit -> return
       -> feature decides: sync (stream) or enqueue
            -> queue (interactive priority > batch)
                 -> worker: check cache -> model client
                      -> tier A (best) | tier B (cheap) | deterministic fallback
                      -> retries w/ jitter, circuit breaker, budget caps
                      -> post-validate / sanitise output
                 -> store result (versioned cache), emit event/stream
       -> response (sync stream or job id)
```

Everything stateful is outside the model call: rate limits, budgets, retries,
and validation are your code, not the model's good behaviour.

## Gotchas

- **A gateway or proxy will kill long HTTP requests** before your app does.
  Sync endpoints past ~30s are a portability trap; go async.
- **Streaming through a buffer anywhere in the path** kills TTFT. Check proxies,
  middleware, and your own serialisation.
- **Cache keys that omit the model or prompt version** serve stale answers after
  a change - the single most common cache bug here.
- **Semantic cache across tenants leaks one user's answer to another.** Scope it.
- **Retries on a streaming request after partial output** duplicate content in
  the client. Buffer until first byte, or make the client idempotent by job id.
- **A fallback that returns a *different kind* of answer** (a template where a
  personalised answer was promised) reads as a bug to users. Make the degraded
  path honest about being degraded.
- **Model upgrades are your breaking change**, not the provider's. Pin versions,
  re-run the full eval + safety suite on bump, and stage the rollout.
- **Async jobs need their own lifecycle**: idempotency key, cancel, expiry,
  result storage, and a user-visible status. A job id with no way to poll or
  cancel is a support ticket.

## Checklist

- [ ] UX shape chosen; anything over the gateway timeout is async
- [ ] Streaming (or a progress ack) on for slow paths; no buffering in the chain
- [ ] Cache keyed by prompt+model+params+version; TTLs and bust-on-change in place
- [ ] Fallback ladder down to a deterministic answer; circuit breaker; bulkhead
- [ ] Retries deadline-bounded with jitter
- [ ] Prompts in git, versioned, flagged, staged, one-flag rollback
- [ ] `prompt_version` + `model` on every log line, trace, cache key, and eval
- [ ] Per-call cost, tokens, `stop_reason`, latency, TTFT, retry/fallback logged
- [ ] PII excluded from logs/traces; debug store access-controlled
- [ ] Dashboards + alerts on error rate, p95, TTFT, fallback rate, cost, eval trend
- [ ] Async jobs: idempotency, cancel, expiry, user-visible status

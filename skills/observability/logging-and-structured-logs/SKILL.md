---
name: logging-and-structured-logs
description: Design a structured JSON logging scheme and use it to debug production — required fields, correlation and request IDs propagated across services, level discipline, sampling, redaction of secrets and PII, and a grep-during-an-incident workflow. Use when writing or reviewing log statements, when logs are unsearchable or unparseable, when a log line leaked a token, or when log volume needs cutting.
compatibility: Stdlib Python only for the bundled validator; the log-field conventions apply to any language.
metadata:
  version: "1.0"
---

# Logging and Structured Logs

Logs are for explaining *individual* events. Metrics tell you something is
wrong; logs tell you what happened. A log line must be machine-filterable
without a human reading it, because during an incident nobody is.

The single most common failure is a log format that is half-structured —
`"ERROR checkout failed for order 8831: timeout after 5s"` — which forces every
query into a `LIKE` and cannot be aggregated.

## The workflow: grep the logs during an incident

This is the job. Everything below serves it.

1. **Start from a symptom and a time window.** "Checkout 5xx rate > 1% since
   14:02 UTC."
2. **Narrow by structure, not by text.** `level=ERROR and service="checkout" and
   trace_id=4bf92f...` — this is only possible if `trace_id` is a field.
3. **Collect every line of one request.** `trace_id` groups them across
   services, in order, even though the services write to different log stores.
4. **Read the first ERROR, not the last.** The first error usually caused the
   rest.
5. **Pivot to traces/metrics for the population** ("how many requests have
   this trace_id's shape?"), back to logs for the individual.

If step 3 is impossible, you are not structured-logging, and you will spend the
incident reading pages of interleaved output from three services.

## The line format

One JSON object per line (JSONL) — greppable, tail-able, line-delimited for
shippers. No multi-line pretty printing, no log prefix, no colour codes.

```json
{"ts":"2026-03-04T14:02:11.418Z","level":"error","service":"checkout-api","version":"4f2a9c",
 "env":"prod","msg":"payment authorization failed",
 "trace_id":"4bf92f3577b34da6a3ce929d0e0e4736","span_id":"00f067aa0ba902b7",
 "http":{"method":"POST","route":"/orders/{id}/pay","status":502},
 "order":{"id":"ord_88123","amount_minor":4200,"currency":"EUR"},
 "error":{"type":"UpstreamTimeout","message":"POST /authorize timed out after 2000ms"},
 "duration_ms":2071,"attempt":2}
```

Rules that make this work:

- **`ts` as RFC3339 with an explicit `Z` or offset.** Local time without a zone
  is a bug that will cost you an hour at the next DST boundary.
- **`level` lowercase, machine-enumerated.** Uppercase means your log parser's
  `level` filter is now a case-sensitive `OR`.
- **`msg` is a stable, low-cardinality event name** — `"payment authorization
  failed"`, not `"failed to authorize payment for order ord_88123"`. Variable
  data belongs in fields. You want to count occurrences of `msg`.
- **`service` and `version` on every line.** When three services write to one
  store, "which one was this" must be a filter, not an inference.
- **Nest related fields** (`error.*`, `order.*`, `http.*`) so queries read like
  the domain and so you can drop a whole subtree at the sink.
- **Types are consistent across events** — `duration_ms` is always a number,
  `order.id` is always a string. Mixed types on the same key break most
  backends' field indexes.
- **Timestamps of *events* go in `ts`; business timestamps in their own
  field** (`order.created_at`). Don't reuse `ts` for two meanings.

## Correlation IDs: the one thing to get right

Generate at the edge (ingress/load balancer), or if that's not yours, on the
first line of the process that has no incoming trace. Propagate on every
outbound call.

- If the service is already OTel-instrumented, the trace context (`traceparent`
  header) is the correlation ID and the SDK injects and extracts it. Don't
  invent a parallel scheme.
- Otherwise: a `X-Request-Id` header, accepted if present and well-formed,
  generated (UUIDv4 or a 16-byte random hex) if not, and **forwarded on every
  outbound request** — including to queues, as a message attribute.
- Log it as a top-level field named consistently across every service in the
  org. `request_id` in one and `x-request-id` in the next defeats the purpose.
- **Validate the inbound value.** An attacker-supplied header becomes a log
  injection vector and a query-injection vector if you interpolate it into a
  query; reject anything over 64 chars or not matching `^[A-Za-z0-9_-]{8,64}$`.

Distributed trace: `trace_id` + `span_id` are the OTel terms. If you use only
one ID, make it the one your tracing backend knows about, or you will have two
identifiers for the same request and no way to join them.

## Levels: decision table, not vibes

| Level | Use for | During an incident you want |
|---|---|---|
| `error` | The operation failed and a human will look, or an automatic recovery failed | all of them, with `error.type`/`error.message` |
| `warn` | Degraded but handled: retry succeeded, degraded mode, near a threshold, unexpected-but-tolerated input | the spike, summarised |
| `info` | Business-significant state transitions: order placed, user signed up, job completed, deploy finished | a handful of context lines |
| `debug` | Everything needed to reconstruct a specific request's decisions | only when sampling/filtering keeps it off by default |
| `trace` | Per-item detail, function entries | effectively never in production |

- **`error` means "unhandled"**, not "logged a message". If you catch it,
  handle it, and continue, it is a `warn` or `info` with `error.*` fields.
  Error-level alerting on a handled condition is how you train a team to ignore
  pages.
- **Never log-and-rethrow the same error** — you get duplicate pages for one
  fault. Log at the boundary that decides not to retry, or where the context is
  added, not at every layer.
- **No `info` in a request loop.** A log per DB query, per iteration, or per
  item is a `debug`, and it is what turns an incident into a cost incident.
- **Exceptions carry stack traces; handled domain errors do not.** A validation
  failure with a 25-line traceback is noise that hides the one real traceback.

## Sampling and volume control

Order of operations, cheapest first:

1. **Do not log it.** The most effective filter is deciding not to emit.
2. **Raise the level / drop to `debug`.** Not gated by environment, so the
   code says what it is.
3. **Sample.** Keep **all** `error`, and sample `info`/`debug` by trace:
   keep if `hash(trace_id) % 100 < N` — deterministic, so all services in a
   request agree and you never end up with half a trace. Use the OTel collector's
   `tail_sampling` processor for this rather than doing it in each service.
4. **Aggregate.** N identical errors → one error plus a counter:
   ```json
   {"level":"error","msg":"payment authorization failed","error":{"type":"UpstreamTimeout"},
    "suppressed":1847,"first_seen":"2026-03-04T14:02:11Z","last_seen":"2026-03-04T14:05:44Z"}
   ```
   The counter keeps the signal in your metrics; the line keeps the example.
5. **Head-based keep-the-first-N** is nearly always the wrong choice: it keeps
   the *start* of the incident and throws away the interesting part, because
   "interesting" arrives after the flood.

## Redaction: assume it will leak

A log line is a copy in a system with weaker access control than your database.
Treat it as a public document.

- **Redact at emission, not at the sink.** Anything downstream of the app — a
  collector regex, an ingest-side masker — will miss something and will be
  bypassed by one debug endpoint. A value that is never formatted is never
  leaked.
- **Deny-list approach, not allow-list**, for logging arbitrary request bodies:
  refuse the body entirely, or pass through an explicit allow-list of safe keys.
  A deny-list of `password` will not catch `passwd`, `pwd`, `user_password`,
  `authorization`, `set-cookie`, `card_number`, `cvv`.
- **Normalize before logging.** Truncate strings (a 2 MB error string in a log
  line is a real cost), collapse newlines in user-controlled values (they break
  line-delimited parsing and can forge a fake log line).
- **Never log**: passwords, tokens/bearer/API keys, session cookies, connection
  strings (they contain the password), full card numbers/CVV, private keys,
  `Authorization` headers, or a full email/phone/address where an id suffices.
- **Hash rather than omit** when you need to correlate: `user_id` is not
  sensitive; an email address is. Log a keyed hash (`hmac(sha256, salt, email)`)
  if you genuinely need "same person" without the person.
- **The leak already happened** when a token reaches the store. Redaction
  reduces blast radius, not exposure — rotate anything that was logged and
  state the incident.

## Gotchas

- **Multi-line exception text breaks JSONL.** A traceback contains newlines.
  Serialize it as an escaped string (`json.dumps` does this) — never
  `logger.exception("...\n" + str(e))` by hand.
- **`%`-style vs f-string vs lazy formatting**: in structured logging,
  `logger.info("processed %s orders", n)` is safest (no formatting when the
  level is off), but the message must stay low-cardinality; the value goes in
  a field.
- **`extra=` collides silently.** A key that already exists on the record
  (`name`, `msg`, `args`, `module`, `filename`, `levelname`) is either
  overwritten or raises depending on the library. Prefix your keys.
- **The stdlib `logging` default is *not* JSON**, and its field names differ
  from everyone else's. If you are on stdlib, configure a `JSONFormatter`
  explicitly; do not assume the output shape.
- **Sampling before or after enrichment matters.** Sample on `trace_id` at the
  *collector*, after the app has added fields, or you cannot sample at all.
- **Sampling is not a security control.** A sampler is a probabilistic filter
  and a determined value will be logged eventually. Redact regardless.
- **Clock skew across hosts** means log order is not event order. Order by the
  causal `span_id`/parent-child, or the timeline you reconstruct is wrong.
- **A logger with no handler prints a bare `No handlers could be found`-
  style line, or nothing at all** — verify the app actually emits what you
  think, with a curl and a tail, before trusting that a missing log line means
  the code did not run.
- **`log.info(f"...{expensive()}")` evaluates `expensive()` even when the level
  is off.** Use lazy args, and precompute outside hot loops.
- **Debug logging left on in production is both cost and a security risk**;
  gate on an explicit level, never on "is this a dev machine" hostname checks.

## Verify before you merge

Run the bundled validator over real output — it is stdlib-only and takes a
file or stdin:

```bash
python3 scripts/validate_log_schema.py app.log --level error
python3 scripts/validate_log_schema.py app.log --required service,version,trace_id --json
```

It checks required fields, level values, timestamp format, and flags likely
sensitive keys and unstructured lines. Exit codes: `0` clean, `1` findings, `2`
usage error. In CI, pipe your test suite's captured logs through it.

- [ ] Every line is one JSON object; a traceback does not split it.
- [ ] `ts`, `level`, `service`, `version`, `msg`, and a correlation id are on
      every line.
- [ ] `msg` values are a closed, countable set.
- [ ] Every outbound call propagates the correlation id; a two-service
      round-trip can be reconstructed by id alone.
- [ ] Inbound correlation headers are validated.
- [ ] A test asserts that a password/token/email never appears in captured
      output.
- [ ] `error` is only emitted for genuinely unhandled failures.
- [ ] Volume estimate at peak (`lines/req × req/s`) is under the log-pipeline
      budget; sampling is on for `info`/`debug` in production.

## Files

- `scripts/validate_log_schema.py` — validate JSON log lines against required
  fields and flag sensitive-key leakage. Run `--help` for options.
- Read `references/log-schemas.md` when you are defining the canonical field
  set for a service, or when two services' logs cannot be joined in one query.

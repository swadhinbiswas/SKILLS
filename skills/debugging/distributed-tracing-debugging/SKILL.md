---
name: distributed-tracing-debugging
description: Use OpenTelemetry and Jaeger-style distributed traces to find where latency and errors actually live across service boundaries - reading a trace, interpreting span status and attributes, understanding W3C traceparent propagation, and finding an N+1 that crosses services. Use when a request is slow end to end, when a call spans several services, when you need to correlate logs with a request, or when someone pastes a trace ID or a Jaeger screenshot. Triggers on "distributed trace", "tracing", "OpenTelemetry", "Jaeger", "traceparent", "span", "trace ID", "where is the latency", "N+1 across services", "request tracing", "tail latency", "sampling".
compatibility: Assumes OpenTelemetry SDK/instrumentation and a trace backend (Jaeger, Tempo, Honeycomb, Datadog, or an OTLP-compatible collector). Exact UI and env-var names vary by vendor; verify against the deployed configuration.
metadata:
  version: "1.0"
---

# Distributed Tracing Debugging

A trace answers one question precisely: **where did the time go, across every
process this request touched.** It is the fastest tool for latency in a system
with more than one service, because it removes the guesswork about which hop
is slow. It is also nearly useless for some problems, and knowing which
problems those are saves hours.

## Workflow

- [ ] 1. Confirm the trace is complete: every hop has a span, with the same
      trace id and correct parent/child links
- [ ] 2. Read the tree top-down: find the widest *self* time, not the deepest
      span
- [ ] 3. Quantify: critical path vs total, and which single hop dominates
- [ ] 4. Check span status, events, and attributes before theorising
- [ ] 5. Find the N+1: a repeated child span, or a large count of a small
      span
- [ ] 6. If the trace is incomplete, fix propagation before trusting anything
- [ ] 7. Confirm with a metric, a log line, or a direct measurement

## The model

- **Trace** = one end-to-end operation. Identified by a **trace id** (32 hex
  chars, shared by every span in it).
- **Span** = one timed operation inside the trace. Has a **span id**, a
  **parent span id**, a name, a start and end time, a **status**, attributes,
  and events.
- **Parent/child** is a *causal* relationship, not a temporal one. A child
  span is entirely inside its parent's time window. Two spans overlapping
  without a parent link means propagation is broken somewhere.
- **Self time** = span duration minus the time in its direct children. This
  is the number that tells you where the work is.
- **Critical path** = the longest chain of spans that each contain the next.
  The request cannot be faster than that chain.

## Step 1 — Verify the trace is complete before you read it

Half of "tracing told me the wrong thing" is a broken trace. Check first:

- **One trace id throughout**, and no orphan spans. Jaeger's UI shows the
  service graph; a gap means a hop is uninstrumented.
- **Children inside parents.** Overlapping siblings with no parent link
  usually means the `traceparent` header was not forwarded (see Step 5).
- **Clock skew.** A child span starting before its parent, or a negative
  duration, is a clock problem, not a latency problem — usually a VM with an
  unsynchronised clock. This makes the *ordering* wrong even when the
  durations are right.
- **The root span is the entry point you expect** (an ingress span, not an
  internal job). If it is not, you are looking at a sub-trace.
- **Sampling.** A head-based sampler decides at the root whether to keep the
  trace; everything downstream follows. So the traces you *have* are
  systematically the slow/interesting ones, and the absence of traces for
  other requests is not evidence of anything. Tail-based sampling (decide
  after the fact) is the fix for "I cannot see the slow ones" and for
  keeping a representative sample.

## Step 2 — Read the tree

In Jaeger, Tempo, Honeycomb, or the OTel Collector UI:

1. **Start at the root and go wide, not deep.** The trace waterfall is
   arranged by time. The spans that are *visually wide* are where time went.
2. **Sort by self time.** Most UIs can sort spans by duration; the single
   widest self-time span is your first target.
3. **Watch the ratio: duration vs children.** A span that is 4 seconds with
   3.9s in one child is a pass-through; the child is the answer. A span that
   is 4 seconds with 200ms of children is doing the work itself.
4. **Look for the gap.** A span with a long duration and no children and no
   obvious reason is: a synchronous call not instrumented, a `sleep`, a lock,
   or a serial loop doing uninstrumented work. This is the most common and
   most useful observation in a trace.
5. **Compare the same span across a healthy and an unhealthy trace.** A
   differential view (`compare` in Jaeger, or query both in a tool that
   supports it) shows exactly which hop changed. This is the single most
   efficient way to find a regression.
6. **Check for retries.** Repeated identical child spans for the same
   operation mean a retry policy is amplifying load and latency. Three 900ms
   retries is 2.7s of a 3s request, and the "slow service" is not the one
   that is slow — it is the one being retried.

## Step 3 — Quantify before you theorise

- **Total (critical path) vs sum of all spans.** The sum is much larger than
  the total whenever work runs in parallel. Only the critical path bounds your
  latency; optimising a parallel branch does nothing.
- **Per-hop breakdown** as a percentage of the critical path. The dominant
  hop is usually obvious and usually not where the intuition points.
- **Percentiles, not traces.** One trace is a story. Query the backend
  (Tempo/Honeycomb/Jaeger query) for p50/p95/p99 *per service* over the
  failing window, and look for a hop whose p99 moved. Tail latency often
  lives in one hop while the median is fine.
- **Attribute the error to a span, not to a service.** The span with
  `status = ERROR` is where the failure happened; the parent that merely
  propagated the exception is not the cause.

## Step 4 — Read span status, events, and attributes

**Status** is `OK`, `ERROR`, or `UNSET` on the OTel span (Jaeger shows it as
a red tag). Two important facts:

- **`UNSET` is not `OK`.** By default most instrumentation does not set a
  status unless something explicitly failed. An `UNSET` span tells you
  nothing; do not read it as "fine". A span that timed out because the
  deadline elapsed is not necessarily `ERROR` either.
- **HTTP status codes and gRPC status codes need explicit mapping.** A
  semantic-convention mapping that sets `ERROR` only on 5xx will not mark a
  404 or a gRPC `UNAVAILABLE` as an error. Verify that the mapping in use
  matches what you need.

**Events** (timestamped instants on a span) are where the interesting
non-timing things go: exception recorded, message sent/received, a retry
attempt, a sampling decision, a rate-limit rejection. The `exception` event
carries the type, message, and a stack trace. Look at events before adding
your own logging.

**Attributes** are the searchable, high-cardinality context. Useful ones:

| Attribute | Why it matters |
|---|---|
| `http.request.method`, `http.route` / `url.path` | the route (not the raw URL — raw URLs are high-cardinality and can contain secrets) |
| `http.response.status_code` | status |
| `rpc.system`, `rpc.service`, `rpc.method` | gRPC identity |
| `db.system`, `db.statement`, `db.operation` | the query; `db.statement` is where N+1 hides |
| `messaging.system`, `messaging.destination.name` | queue work |
| `net.peer.name`, `net.peer.port` | which instance answered |
| `peer.service` | the logical upstream, in client spans |
| `retry.attempt` / custom attempt counters | retry storms |
| queue/message identifiers | correlate with the consumer side |

**Never put PII, tokens, or raw request bodies in span attributes.** They are
indexed and retained, and readable by anyone with trace access. The
instrumentation's default `url.full` / `db.statement` capture can leak
parameters containing emails and tokens; redact or turn it off at the source
rather than relying on backend redaction.

## Step 5 — Propagation: how the trace crosses a boundary

For the trace to be continuous, the caller must put the current context in the
outgoing request, and the callee must read it.

**W3C Trace Context** (the standard; what OpenTelemetry emits by default):

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
              ^  ^                                ^          ^
              |  trace-id (32 hex)                parent-    flags
              |                                  span-id
              version (00)
```

- Flags: bit 0 (`01`) is `sampled`; bit 1 (`02`) is "random"/`Recorded` in
  newer revisions. Do not hand-construct this header — use the SDK's
  propagator.
- `tracestate` is the optional vendor key/value list, propagated alongside;
  it must be preserved on every hop or you break the other vendors'
  baggage.
- The callee's server span takes the extracted `parent span id`, so the link
  is automatic **if the header arrives**.

In the SDKs this is the propagator, and it is on by default with
W3C `tracecontext` (and usually `baggage`):

```
OTEL_PROPAGATORS=tracecontext,baggage
```

The failure modes, all of which produce a broken tree:

| Symptom | Cause |
|---|---|
| Two separate traces for one request | the header is not forwarded at a proxy, load balancer, or in a hand-rolled HTTP client |
| Child spans appear as separate roots | the callee is not extracting the context (propagator not registered, or a client built without the global propagator) |
| Spans from one service form a flat list | the callee extracted the context but the *caller*'s span was not the parent — usually a `Context` not being attached in the caller |
| Trace id changes at a queue | the producer's context was not serialised into the message headers; a messaging instrumentation must inject into and extract from the message, not the transport |
| Only the ingress span exists | the app is not instrumented, or the SDK is not started (no auto-instrumentation) |
| Everything in one huge span | the instrumented boundary is too coarse; add child spans around the real work |
| Traces from async work are disconnected | the task is not run with the captured context attached (Go `context.Context` propagation; Java/Reactor context; Python `contextvars`; JS AsyncLocalStorage) |

**Async is the one that surprises everyone.** Creating a span in a goroutine,
a thread pool task, a queue consumer, or a promise callback without attaching
the parent context produces a new root. In Go, pass the `context.Context` into
the goroutine (`go func(ctx context.Context) { ... }(ctx)`) and use
`tracer.Start(ctx, ...)`; in Python, `contextvars` propagate automatically
only across awaited coroutines and `asyncio` tasks, not across threads; in
Java, Reactor/`CompletableFuture` lose the context unless it is captured at
subscription time. Check this before concluding the propagation header is
wrong.

**Messaging and queues break the request/response shape.** A producer span and
a consumer span are in the *same trace* only if you explicitly link them
(OpenTelemetry spans support explicit links for exactly this). Without it,
the consumer starts a new trace and you cannot see the end-to-end latency of
a job. Configure messaging instrumentation to link producer and consumer
spans, and add the trace context to the message headers.

## Step 6 — Find the N+1 that crosses services

The in-process N+1 is a loop of queries. The distributed N+1 is worse: a
loop of *network calls*, each its own span, each paying full round-trip plus
serialisation. It is the single most common expensive pattern in a trace, and
it is easy to spot.

**The signature:** one parent span with a large count of nearly-identical
child spans, each short, in sequence (not in parallel).

- In a trace UI: the same child operation name repeated 40 times under one
  parent.
- In a query: group by span name and count.
  ```
  { resource.service.name = "orders" }
  | count() by name
  ```
  A span name with a count in the hundreds where you expect one is an N+1.

**Why it hurts:** 40 sequential 8ms calls is 320ms of pure round-trip. The
fix is batching, not making each call faster:

- Replace the loop with one **batch** endpoint (`GetItems(ids[])`). The
  instrumented `db.statement` will show `IN (?, ?, ?, …)`.
- Or fetch with a join/predicate (`where order_id in (...)`) — often one
  query.
- If the calls are independent, **parallelise them** — this shows in the trace
  as the child spans overlapping instead of stacking. Parallelising hides the
  count, not the problem, but it is often the right first step; batching is
  the right second.

**The diagnostic that distinguishes it from a legitimate loop:** check the
*self time* of the parent. If the parent is 300ms and its children sum to
290ms, the time is in the calls, not the loop logic. If the children sum to
50ms and the parent is 300ms, the loop body itself is slow.

## Step 7 — What traces cannot tell you

Knowing the blind spots prevents the most common misuse: concluding from a
trace that something did not happen.

- **Traces are samples.** Head-based sampling means a specific request you
  are complaining about may have no trace at all. "No traces" is not "no
  problem". If you cannot sample enough, use tail-based sampling on latency or
  error.
- **Traces show *where* time went, not *why* a value was wrong.** A span
  named `SELECT` taking 200ms tells you the query is slow; only the database's
  own plan tells you why. Take the `db.statement` and go to the database (see
  `postgres-query-tuning`).
- **Uninstrumented code is invisible.** A span that is 2s with no children
  might be a tight loop, a lock, a GC pause, or an uninstrumented call. The
  trace locates the span; you still need a profile or a log to see inside it
  (see `performance-profiling`).
- **Aggregates are not there.** A trace is one request. "The error rate is
  2%" requires a metric; a trace cannot show you a rate.
- **No causality, only timing.** Spans that overlap *might* be causally
  related (a missing context propagation, or a coincidence). Correlate with
  logs or metrics before concluding.
- **Async and batch work has no request.** Scheduled jobs, cron, and queue
  consumers may have no incoming trace context. A trace that starts at the
  consumer tells you nothing about the job's total latency including the wait
  in the queue. That is a metric, not a trace.
- **Sampling distorts aggregate statistics.** A head-based sampler keyed on
  nothing in particular gives a biased sample. Percentiles computed over
  traces are only as representative as the sampling.
- **Clock skew makes durations wrong** even when the topology is right. If
  durations look impossible, suspect NTP before the code.
- **A span is not a log line.** Attributes are indexed metadata; the
  unbounded, high-cardinality detail belongs in logs, correlated by trace id
  and span id.

## Correlating traces with logs

- Put the **trace id and span id into every log line** for the request. Then
  a log query for the trace id returns everything the trace does not capture:
  the actual values, the branch taken, the external response body.
- Most OTel log/trace integrations do this automatically with a processor;
  if not, add it to the logging formatter.
- Put the same ids in log-based alerts so a paged alert links straight to the
  trace.

## Instrumentation, in the order of value

1. **Auto-instrumentation** of the HTTP/gRPC server and client, plus the
   framework and database driver. This gets you the tree before any manual
   work.
2. **Manual spans around the operations you actually care about**: the
   business operation, the external call, the batch job iteration. Name them
   stably (`checkout.validate_payment`, not `step 3`) — span names are your
   primary query dimension, and generic names make the trace useless.
3. **Attributes on the spans that matter**: the domain identifiers (order id,
   tenant id) that let you query "all traces for this order".
4. **Links** for async and messaging causality.
5. **Events** for discrete state changes worth seeing on the timeline.

Span naming is the highest-leverage convention you set: a stable, low
cardinality name (`POST /orders/{id}/items`, not the raw path) is what makes
the traces queryable afterwards. Cardinality belongs in attributes.

Sampling configuration, roughly:

| Goal | Setting |
|---|---|
| Keep everything, short-lived | parent-based `AlwaysOn`, only in development |
| Keep a representative sample | `ParentBased(TraceIDRatioBased(x))` — a ratio like 0.01 to 0.1 |
| Keep all errors and all slow requests | **tail-based sampling** in the Collector: decide after the trace completes |
| Keep everything through a specific hop | `ParentBased` + a always-sample rule for a known critical path |

Tail-based sampling is the right answer for "I need every slow trace and
every error trace, and I cannot afford to keep all of them". It is a
Collector-level policy; see the OTel Collector `tail_sampling` processor
documentation for the current policy names and configuration shape rather
than assuming a syntax.

## Gotchas

- **A trace that is missing a hop is a propagation bug, not a fast service.**
  The most common cause is an uninstrumented intermediary: a proxy, a
  message queue, a Lambda/edge function, or a load balancer that does not
  forward headers. Check the hop's configuration before optimising the
  neighbours.
- **`UNSET` status does not mean OK.** It usually means nobody set it.
- **Never infer an error from a 4xx.** A 404 is often a correct response. Map
  only the statuses you treat as errors, and be explicit about it.
- **Do not put secrets in attributes.** `url.full`, `db.statement`, and
  custom "payload" attributes are stored and indexed; redact at the source.
- **`span.kind` matters for reading the tree.** A `SERVER` span and a
  `CLIENT` span for the same call are two spans; the client span is often a
  child of the server span and both show the same network time. Counting
  both double-counts. Server spans for outbound calls mean the client
  instrumentation is missing, so you cannot attribute time to the downstream
  service.
- **The trace's total time is not the sum of the spans.** Parallel work means
  the sum overstates it; use the critical path.
- **Traces from one instance are not a fleet.** Compare across instances
  (`net.peer.name`, or an instance attribute) — a single slow pod looks like
  a system problem until you see it is 1 of 20.
- **Adding a trace attribute can be expensive** if it computes something
  non-trivial on every call. Measure the instrumentation overhead, and keep
  it off the hottest paths.
- **Sampling a database statement is a data-governance issue** as well as a
  performance one. Check that your instrumentation's query capture is
  compliant before shipping it to a third-party backend.
- **A trace ID from a user is the single most useful thing you can ask for.**
  When a bug is hard to reproduce, one trace id from a real occurrence often
  beats an hour of reproduction attempts.

## Handoff template

```markdown
Trace:     <trace id>, <service>, sampled <ratio> (head-based)
Window:    <first/last timestamps>
Critical path (ms):
  ingress 1000
  ├─ orders.checkout 980
  │  ├─ inventory.get_stock × 42   self 3ms each   = 126ms sequential
  │  ├─ payments.authorize 610
  │  │  └─ psp.call 590  (upstream, not instrumented)
  │  └─ db.insert 12
  └─ response 3
Total 1003ms; sum of spans 1480ms (parallel work)
Findings:  42 sequential inventory calls = 126ms of round-trip; the 590ms
           PSP call dominates and has no client span, so the time is
           unattributed to a downstream service.
Actions:   batch GetStock(ids[]) -> 1 call; instrument the PSP client
           (client span missing); tail-sample errors + p99 so this trace
           would have been captured.
```

That template is the deliverable: the critical path with self times, the
conclusion, and the specific instrumentation gaps. Without the "not
instrumented" line, the next person repeats the same investigation.

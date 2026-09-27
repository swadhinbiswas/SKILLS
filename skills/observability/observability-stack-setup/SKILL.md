---
name: observability-stack-setup
description: Stand up or repair an OpenTelemetry-based observability stack — auto-instrumentation vs manual spans, collector deployment and tail sampling, backend choice, and correlating traces, metrics, and logs through trace IDs. Use when adding OTel to a service, deploying or configuring an OTel collector, choosing between vendors, when traces are too expensive or logs can't be joined to traces, or when "we have traces" but nothing is queryable.
compatibility: Examples use the OTel Collector contrib distribution and OTLP/HTTP on 4318; version-specific flags should be verified against the release notes.
metadata:
  version: "1.0"
---

# Observability Stack Setup

Three data types, one pipeline, one shared identifier. The stack is done when
an engineer can go from "checkout is failing" to the exact log line, in the
same tool, in a couple of queries.

The wiring order that works: **instrument the app → collector in each cluster →
gateway/central collector → backend.** The collector is the only place where
sampling, redaction, and routing decisions belong; never make them in the app.

## What auto-instrumentation gets you, and what it does not

Auto-instrumentation (Java agent, Python `opentelemetry-instrument`, Node
`--require`, .NET profiler) hooks the framework and produces, with zero code:

| Signal | You get | You do not get |
|---|---|---|
| **Traces** | Inbound server span, outbound client spans, DB spans, message publish/consume spans, with `http.route`, `db.statement`, `messaging.system` attributes | Spans for your own domain steps; a span around the "interesting" unit of work; the name of the business operation |
| **Metrics** | `http.server.request.duration`, `http.client.request.duration`, `db.client.operation.duration` histograms with low-cardinality labels | Every business metric; resource saturation (that's a different exporter); anything with `user_id` on it |
| **Logs** | Log records exported as OTLP **if** the app emits them through the OTel logging SDK or the bridge handler; trace context injected into the log record | The log *format* (that's your JSON formatter's job), and the fields that make logs searchable |

Rule: **auto-instrumentation is the floor, not the ceiling.** It tells you the
framework is slow; it never tells you *which discount rule* is slow. Add manual
spans around domain operations and business metrics by hand — but only after
auto-instrumentation is on and you can see the baseline.

Manual span, minimum viable:

```python
with tracer.start_as_current_span("checkout.apply_discount") as span:
    span.set_attribute("cart.item_count", len(cart))
    span.set_attribute("discount.rule_id", rule.id)   # bounded, not per-cart
    total = apply(cart, rule)
    span.set_attribute("discount.total_minor", total)
```

Per-item, per-user attributes belong on the span, not on a metric — spans are
sampled and indexed per request; metrics are not.

## The collector: what it is actually for

The collector is a config-driven pipeline. Five things it should be doing for
you:

1. **Receiving** OTLP (4317 gRPC, 4318 HTTP) from every workload, and
   health-check, and (optionally) scrape metrics.
2. **Normalising** — resource attributes, so `service.name` is consistent
   everywhere. This is the single highest-value thing it does.
3. **Sampling** — tail sampling, where the decision uses the *whole* trace.
4. **Batching and retrying** — `memory_limiter` first, then `batch`, with
   `send_queue_size` and a real `retry_on_failure`.
5. **Routing** — traces to one backend, metrics to another, logs to a third,
   or all three to one place.

Baseline config (contrib distribution), the shape to start from:

```yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: 0.0.0.0:4317 }
      http: { endpoint: 0.0.0.0:4318 }
processors:
  memory_limiter:
    check_interval: 1s
    limit_percentage: 80
    spike_limit_percentage: 20
  resource:
    attributes:
      - { key: deployment.environment, value: prod, action: upsert }
  tail_sampling:
    decision_wait: 10s
    num_traces: 100000
    policies:
      - name: errors-and-slow
        type: composite
        composite:
          max_total_spans_per_second: 10000
          policy_order: [errors, slow, baseline]
          composite_policy:
            - { policy: errors, max_percentages: { errors: 100 } }
            - { policy: slow,   max_percentages: { slow: 20 } }
      - name: errors
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: slow
        type: latency
        latency: { threshold_ms: 800 }
      - name: baseline
        type: probabilistic
        probabilistic: { sampling_percentage: 3 }
exporters:
  otlphttp:
    endpoint: https://backend.internal:4318
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, resource, tail_sampling], exporters: [otlphttp] }
    metrics: { receivers: [otlp], processors: [memory_limiter, resource, batch], exporters: [otlphttp] }
    logs:    { receivers: [otlp], processors: [memory_limiter, resource], exporters: [otlphttp] }
```

Deployment: **DaemonSet on every node** (agent mode) for per-host metrics and
anything the host must observe; **Deployment** for anything with state
(tail sampling, load-balancing exporter) — a sampling decision needs all the
spans of a trace in one place, so a stateful sampling collector must be behind
a load-balancing exporter or you get partial traces.

Gotchas in the collector that cost real hours:

- **`memory_limiter` must be the first processor.** With it last (or absent),
  the collector is OOM-killed under a load spike, silently dropping the data
  you most wanted.
- **Tail sampling buffers, so it adds `decision_wait` (10s typical) latency to
  every trace** and holds that much in memory. That's the cost of "I can decide
  based on the whole trace".
- **Client-side (head) sampling in the app breaks tail sampling**: the
  collector never sees the spans that were already dropped, so a 3% head
  sample of a bad trace can be 0% of it. If you do tail sampling in the
  collector, set the SDK sampler to `always_on` (ParentBased(AlwaysOn)) and let
  the collector decide.
- **The OTLP HTTP exporter needs a path suffix** (`/v1/traces`,
  `/v1/metrics`, `/v1/logs`) on most backends; the gRPC exporter does not.
  A 404 on `/v1/traces` is almost always a missing path, not a bad token.
- **`resourcedetection`** to pull k8s metadata exists but is slow to run on
  every scrape; put it in the agent, not the gateway.

## Choosing a backend

The honest summary: the SDK and collector are the portable part; the backend is
where you lock in. Pick for query latency and cost model, then accept the
lock-in — that is what every team does, and pretending otherwise is the mistake.

| Need | Pick | Watch out for |
|---|---|---|
| Full traces + metrics + logs, all-in-one, opinionated UX | a commercial APM (or Grafana Cloud / a similar hosted) | per-host pricing surprises; data residency |
| Metrics-first, PromQL, open, cheap | Prometheus + a long-term store (Thanos/Mimir/VictoriaMetrics) | you operate the storage and the retention maths |
| Logs-first, cheap, high volume | Loki (labels, not full-text) or a cloud log store | Loki's cost is *label cardinality* — `user_id` as a label will bankrupt you exactly as in metrics |
| Everything in one UI, SQL, priced on ingest | a ClickHouse-based log/trace backend | high ingest cost; the good answers need exemplars and trace-to-log links |
| Fully self-hosted, minimal | Grafana + Prometheus + Tempo + Loki + Jaeger | four things to operate; the Tempo→Loki→Grafana trace-to-logs link is the fragile part |

Non-negotiable features, whatever you pick:

- **Trace ↔ log correlation by trace ID** (a link from a span to its logs, and
  back). Without it you have three tools and no incident workflow.
- **Exemplars** on histograms, linking to a trace.
- **Tail sampling support** (or an equivalent server-side sampler) — you cannot
  afford 100% of traces and 1% loses exactly the rare failures you need.
- **A PromQL-compatible metrics API** if you have existing dashboards, or a
  documented migration path.

## Correlating the three signals

The identifier is the OTel `trace_id`, present in all three. Make it
unbroken:

**App → collector.** The SDK generates or extracts `traceparent` on the
inbound request. Nothing to do.

**Span → log.** Wire the OTel logging bridge so the log record carries
`trace_id`/`span_id`:

```python
from opentelemetry.sdk._logs import LoggingHandler
logging.getLogger().addHandler(LoggingHandler())
# or, for structlog/loguru, add a processor that copies
# span.get_span_context().trace_id into the record's fields
```

If a log line in a request handler has no `trace_id` field, you have an
unlinked record and the incident workflow breaks. Check one request, not the
average.

**Span → metric.** Exemplars, carried automatically by the OTel SDK on
histogram observations that fall outside a percentile. Needs the backend to
support exemplars (Prometheus-compatible backends do via
`OpenMetrics` scrape; OTLP-native backends support them in the SDK path).

**Backend-side link.** In Grafana, a Tempo/Jaeger datasource with
`tracesToLogs` (by trace ID) and `logsToTraces` gives you the click-through in
both directions. If you are on another stack, the same two links are a couple
of queries — but you must know the exact query, so write it in the runbook.

**Manual fallback when correlation is broken:**

```promql
# 1. find the bad window
sum(rate(http_server_requests_total{status=~"5.."}[5m])) by (route)
# 2. get a trace id from the exemplar on the latency histogram, or from logs:
#    {"level":"error","trace_id":"..."}  ->  open the trace backend, search that id
# 3. in the log backend, filter by that trace_id
```

## Verify the stack, end to end

A stack is not working until this passes. Do it after every change, not just
at setup:

- [ ] `curl` a real request; a trace appears in the backend within
      `decision_wait + batch` seconds, with `service.name` correct (not
      `unknown_service:python`).
- [ ] The trace has a server span **and** a child client span for the
      database call — proving child spans are wired, not just the entry point.
- [ ] A log line from the handler has `trace_id` set, and searching that id in
      the log backend returns the line.
- [ ] A latency histogram on the endpoint shows a **viewable** p99 in the UI
      and an exemplar link on an outlier.
- [ ] Sampling is on: a load test at 100 rps for 5 minutes produces a trace
      count proportional to `sampling_percentage`, not to request count.
- [ ] The collector's own `/metrics` shows no `otelcol_exporter_send_failed`
      climbing, and the Kubernetes container has a memory limit with
      `memory_limiter` sized to it (`limit_percentage: 80`, not 100).
- [ ] The error budget of the pipeline is explicit: how many GB/day of logs,
      how many spans/sec of traces, what retention, and what it costs per month.
      Put the number in the service's README; an unpriced collector is an
      incident in three months.

## Gotchas

- **Two collectors, one trace** (agent + gateway both exporting) double-counts
  every span. Pick one export path.
- **Resampling a trace after the fact is not possible** in most backends — the
  sampler is the only chance. Decide sampling policy deliberately.
- **`service.name` defaults to the executable name** in several runtimes
  (`python3`, `node`) when the SDK isn't configured. Set it explicitly; the
  alternative is every service called `unknown_service`.
- **A collector with no `exporters` configured drops everything silently** —
  it starts fine, shows no errors, and exports nothing. If the backend is
  empty, check the `service.pipelines` first, then `processors` for a
  misordered `memory_limiter`.
- **Metrics and traces have opposite sampling needs.** Metrics need a
  consistent series set (don't tail-sample metrics by outcome — it changes the
  denominators); traces need per-trace decisions. Use `tail_sampling` on the
  traces pipeline only.
- **Do not put the collector in the request path** without a memory limit and a
  `memory_limiter`; an overloaded collector shows up as application latency,
  which is a deeply confusing incident.
- **Node.js auto-instrumentation with `--require` conflicts with a bundled
  transpiler** (bundlers that inline the OTel API). Set
  `OTEL_RESOURCE_ATTRIBUTES` and use the distro's init hook, or traces will be
  dropped at startup with a module-not-found error in the first 200 lines of the
  log.
- **Config drift between environments** is the usual cause of "works locally,
  silent in prod": keep the collector config in the same repo as the manifests,
  and version it.

## Files

- Read `references/collector-configs.md` when you need a full working config
  for a specific shape (agent DaemonSet + gateway, logs-only, or a full
  traces+metrics+logs pipeline with redaction).

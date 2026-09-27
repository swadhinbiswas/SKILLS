---
name: metrics-and-instrumentation
description: Decide what to measure and add it deliberately — RED vs USE signals, counter vs gauge vs histogram, label cardinality, units, exemplars, and instrumenting a codebase without spraying metrics. Use when adding metrics or tracing instrumentation, when a dashboard or alert needs a number, when choosing a Prometheus/OpenTelemetry metric name and labels, or when a metrics backend is running out of memory after a deploy.
compatibility: Metric types and naming match OpenTelemetry/Prometheus conventions; backend-specific queries are named where they differ.
metadata:
  version: "1.0"
---

# Metrics and Instrumentation

Metrics answer "how is it going" continuously. The failure mode is not too few
metrics — it is *too many, too high-cardinality, and too close to the CPU*, so
nobody trusts the number that mattered.

Two questions decide everything: **would I page someone if this changed, or
would I only look at it?** Only the first kind earns a metric.

## Start from the user-visible signal, not the internals

For a request-serving system, instrument the journey before the machinery.

- **RED** — Rate, Errors, Duration. For every endpoint and every synchronous
  dependency. Tells you *whether* something is wrong.
- **USE** — Utilisation, Saturation, Errors. For every finite resource: CPU,
  memory, disk, connection pools, thread pools, queue depth. Tells you
  *what is about to run out*.
- **Four golden signals** (Google SRE) — latency, traffic, errors, saturation.
  The portable core of RED + USE.

Both, applied to the right layer:

```
HTTP layer        RED      http_server_request_duration_seconds{route,method,status}
DB client         RED      db_client_query_duration_seconds{operation,outcome}
Postgres          USE      pg_stat_database_numbackends / max_connections
Connection pool   USE      http_client_pool_active / pool_max
Worker queue      USE      jobs_queue_depth, jobs_oldest_item_age_seconds
Business          —        orders_completed_total (the one that predicts revenue)
```

Nothing internal gets instrumented until the user-visible path is covered.

## Pick the instrument

| Need | Type | Semantics | Example |
|---|---|---|---|
| How many times did X happen | **Counter** | Monotonic, only `+delta` | `http_requests_total{...}` |
| What is the level right now | **Gauge** | Can go up and down | `pool_active_connections` |
| How long things take / how big | **Histogram** | Bucketed; gives percentiles | `http_request_duration_seconds` |
| A share of a total | **Ratio / count** | Two counters, compute the rate | `errors_total / requests_total` |
| A value sampled now | (avoid) | Aggregation loses the distribution | — |

Errors:

- Counters **must** end in `_total` (Prometheus convention) and are `float`,
  not `int` — you will need to add a synthetic failure rate later.
- Gauges must have an explicit resource bound in the name or labels
  (`pool_active` alone is unreadable; `pool_active` + `pool_limit` is).
- A histogram's bucket boundaries are a design decision made once and baked into
  stored series. Prometheus's default buckets are tuned for RPC, not for
  everything. **A metric that only ever records 3ms and 12s is not a histogram
  you can query well.**

Latency and size are always histograms, never averages. Averaging hides the
p99 entirely: two request classes, 99 at 5 ms and 1 at 20 s, average to 205 ms
and tell you nothing. (PromQL `rate(x_sum[5m]) / rate(x_count[5m])` gives the
mean of a histogram — useful for SLO *averages*, useless for tail latency.)

## Cardinality: the metric that kills your billing

Series count = the product of label value counts. Budget the total.

| Labels | Per-metric series (rough) |
|---|---|
| `status` (5 values) | 5 |
| `method` (5) | 25 |
| `route` (80) | 2,000 |
| + `user_id` (2M) | **4 × 10¹⁰** — instant outage |

**`user_id`, `email`, `request_id`, `session_id`, `url` with an id in it,
`error_message`, `stack trace`, `raw path`, `order_id` — never labels.** They
are unbounded, they are attacker-controlled, and they are exactly what the
metric backend ingests per scrape.

What that looks like in practice: a deploy adds `?user_id=12345` to a path
label, Prometheus memory goes 4 GB → 90 GB, the node OOMs, **it restarts and
loses the TSDB head**, and the restart re-reads the persisted blocks, so now
every query is slow too. The alert that caused the outage is the one that
stopped firing. This is not hypothetical: any free-software metrics backend
has a "too many samples" or "query timed out" error message in its docs, and
you will see it in the logs at exactly the wrong time.

Rules:

- Labels are a **closed, enumerated set you wrote down**. If a label value is
  not in the list, drop the series, do not invent a bucket.
- Unknown / unhandled goes to a literal `other`, not to the raw value.
- `route` is the *route template* (`/users/{id}/orders`), never the concrete
  path. Rewrite at the middleware layer where the router matched.
- User identity goes in **traces** (span attributes), which are sampled and
  indexed, not in metrics, which are not.
- Prefixes like `id:`, `trace_id:`, or `__`-doubling label names are the
  Prometheus escape hatches for exactly this case. Use them deliberately.

Budget before you ship: `series_total = Σ over metrics of (product of label
value counts)`. If the total exceeds a few hundred thousand, cut before
deploying, not after the OOM.

## Names, units, labels

Follow OpenTelemetry semantic conventions where they exist; Prometheus naming
where they don't. Consistency is what makes dashboards and alerts portable.

```
<namespace>_<subsystem>_<unit>_<type>
http_server_request_duration_seconds        histogram   unit in the name
orders_completed_total                      counter
pool_connections_active                    gauge
```

- **Units in the name, base units only**: seconds (`_seconds`), bytes
  (`_bytes`), not `milliseconds` or `_kb`. Convert at query time, never in the
  instrument.
- **Dots in names are banned** in Prometheus; use `_`.
- **Sum, not average, across instances.** Counters are only meaningful
  aggregated; `sum by (route) (rate(http_server_request_duration_seconds_count[5m]))`.
- Label names are constant across the whole metric. Per-instance values go in
  the *value* or an `instance`/`pod` label added by the backend.
- The same logical quantity must carry the same label set everywhere, or
  `sum by` silently splits series and every number is wrong.

## Exemplars: link the histogram to the trace

A percentile tells you *that* something is slow, never *what*. Attach an
exemplar — a pointer to a trace — to the histogram bucket of an outlier
observation. Then "p99 of checkout is 3 s" is one click from the actual spans,
SQL, and log lines behind that 3 s.

Exemplars carry only a trace/span ID, so they add no cardinality. Emit them
only for the slow tail (say, observations above p99 or above an absolute
threshold), not for every sample.

```python
# OTel: only the observation bucket receives an exemplar; it is sampled internally.
meter.create_histogram(
    "http.server.request.duration",
    unit="s",
    advice=Advice(explicit_bucket_boundaries_advisory=[.005, .025, .1, .3, 1, 3, 10]),
)
```

## Instrument deliberately, not by spraying

1. **Enumerate the user-visible journeys** first. Checkout, login, page load,
   webhook delivery. One metric family per journey, per step.
2. **Decide the fate of each metric before writing it**: page on it, graph it,
   or keep it for debugging. Anything you cannot name a consumer for is
   deleted at review.
3. **Prefer the framework's default instrumentation**, then add only the
   domain metrics the framework cannot know about (`orders_completed_total`,
   `cart_abandoned_total`). Code-level metrics are a fallback, not a starting
   point.
4. **Annotate releases** (`build_info`, deployment markers, `start_time`) so
   every graph has a vertical line for the deploy. This is the single cheapest
   correlation you can add.
5. **Write the alert before the metric.** If no query consumes it, no alert can.

Anti-pattern: instrumenting every function ("how many times is `parse()`")
because it is free. It is not free — it is cardinality, cardinality is what
kills the backend, and a metric nobody queries is pure cost.

## Verifying what you shipped

Before merging instrumentation:

- [ ] Every new metric's series count computed and written down.
- [ ] No unbounded label: grep your own diff for `user_id`, `id=`, `email`,
      `request_id` inside a label map.
- [ ] Route labels are templates, verified by hitting a real URL with an id and
      checking no new series appeared.
- [ ] Buckets cover the actual observed range (check p50/p99 first, then fix).
- [ ] A dashboard or alert consumes it, or it should not exist.
- [ ] Cost: at the current request rate, this is N series per metric — do the
      arithmetic against your backend's known series limit.

## Gotchas

- **Counters reset on restart.** That is correct, not a bug; use
  `rate()`/`increase()` which handle it, never `sum()` over a raw value. The
  classic wrong graph is "requests in the last hour" as a raw counter.
- **A missing series is a valid reading.** Gauges stop being scraped when the
  target is down; a flat zero and no data look identical in a bad dashboard
  design.
- **Histogram storage is proportional to bucket count × series count.** 20
  buckets × 20,000 series × 2,000 instances is 800M stored values. Sizing
  histograms by series count matters more than bucket tuning.
- **Microservice metrics double-count.** Edge proxies, service meshes
  (`ENVOY`-style sidecars), and ingress all measure the same request. Decide
  once, per metric, whether it is *client-side* or *server-side*, and never
  sum across both layers.
- **Averages hide the thing you are trying to find.** Use histograms and
  `histogram_quantile(0.99, ...)`. And remember histogram quantiles are
  *interpolated across buckets* — they are wrong if your bucket boundaries do
  not match the latency you care about.
- **Process metrics are not request metrics.** `node_cpu_seconds_total` on
  every pod tells you nothing about the pod you are on. Always scope or
  instance-join.
- **Instrumenting a hot path changes the hot path.** Timing code with
  `time.time()` in a per-row loop is itself a measurable slowdown; use a
  library's instrumentation and batch.
- **`start_time`/`process_start_time` gauges let you drop dead instances**
  from a graph. Without them, stopped pods keep their last value forever.
- **Never histogram a value that is always the same** (a version string, a
  boolean). That is a gauge or a label.
- **Client libraries report client-side latency including DNS, connect, and
  retry time.** A metric that silently includes 3 retries is a different
  number than the one your service dashboard shows; that mismatch is hours of
  confusion during an incident.

## Files

- Read `references/naming-and-labels.md` when a team is standardising names
  across services, or when two metrics "mean the same thing" and the numbers
  disagree.
- Read `references/instrumenting-python-services.md` when you need concrete
  OTel Python wiring for a service you are adding instrumentation to.

# Collector configuration shapes

Verify flags against the release notes for your distribution — the OTel
Collector moves fast and some processor names have changed. The configs below
use the contrib distribution.

## 1. Agent (DaemonSet) + gateway (Deployment)

The standard production shape. The agent runs per node with no state; the
gateway holds the sampling decision and the load balancer.

**agent-config.yaml** (DaemonSet, per node):

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
  k8sattributes: {}
  resourcedetection:
    detectors: [env, system]
    timeout: 2s
  batch: { send_batch_size: 512, timeout: 5s }
exporters:
  otlphttp/gateway:
    endpoint: http://otel-gateway.observability.svc:4318
    # no /v1/... path: the signal is appended per-signal by the exporter
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, k8sattributes, resourcedetection, batch], exporters: [otlphttp/gateway] }
    metrics: { receivers: [otlp], processors: [memory_limiter, k8sattributes, resourcedetection, batch], exporters: [otlphttp/gateway] }
    logs:    { receivers: [otlp], processors: [memory_limiter, k8sattributes, resourcedetection], exporters: [otlphttp/gateway] }
```

**gateway-config.yaml** (Deployment, with tail sampling and redaction):

```yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: 0.0.0.0:4317 }
      http: { endpoint: 0.0.0.0:4318 }
processors:
  memory_limiter:
    check_interval: 1s
    limit_percentage: 75
    spike_limit_percentage: 20
  tail_sampling:
    decision_wait: 10s
    num_traces: 200000
    expected_new_traces_per_sec: 2000
    policies:
      - { name: errors, type: status_code, status_code: { status_codes: [ERROR] } }
      - { name: slow, type: latency, latency: { threshold_ms: 1000 } }
      - { name: baseline, type: probabilistic, probabilistic: { sampling_percentage: 2 } }
  transform/redact:
    error_mode: ignore
    trace_statements:
      - context: span
        statements:
          - replace_keys(attributes, ["http.request.header.authorization", "db.statement"]) with nil
  batch: { send_batch_size: 1024, timeout: 10s }
exporters:
  otlphttp:
    endpoint: https://ingest.backend.internal
    headers: { authorization: "${env:OTLP_AUTH}" }
    sending_queue: { enabled: true, num_consumers: 8, queue_size: 10000 }
    retry_on_failure: { enabled: true, initial_interval: 1s, max_interval: 30s }
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, tail_sampling, transform/redact, batch], exporters: [otlphttp] }
    metrics: { receivers: [otlp], processors: [memory_limiter, batch], exporters: [otlphttp] }
    logs:    { receivers: [otlp], processors: [memory_limiter, batch], exporters: [otlphttp] }
```

Why two deployments: tail sampling is **stateful**. An agent that samples half
a trace and forwards it to two different gateways produces two half-traces. The
gateway also has a memory limit to size against `num_traces ×
expected_new_traces_per_sec × decision_wait`.

Validate before deploying:

```bash
otelcol-contrib validate --config=./gateway-config.yaml
```

## 2. Logs-only, high volume

When the bill is in logs, add aggregation and a transform before the batch, and
set an explicit ingestion budget.

```yaml
processors:
  memory_limiter: { check_interval: 1s, limit_percentage: 80 }
  transform/drop_debug:
    error_mode: ignore
    log_statements:
      - set(attributes["level"], "info") where attributes["level"] == "debug"
      - drop(attributes) where attributes["level"] == "trace"
  filter/exclude_health:
    error_mode: ignore
    log_statements:
      - drop(attributes) where IsMatch(attributes["http.route"], "^/(healthz|readyz|metrics)$")
  attributes/redact:
    actions:
      - { key: http.request.header.authorization, action: delete }
      - { key: user.email, action: delete }
      - { key: http.response.body, action: delete }
service:
  pipelines:
    logs: { receivers: [otlp], processors: [memory_limiter, attributes/redact, filter/exclude_health, batch], exporters: [otlphttp] }
```

Redact in the collector **as well as** in the app, and treat it as
defence-in-depth, not the primary control: the app-level redaction is the one
that must never be bypassed.

## 3. Single-container (all-in-one, small deployments)

One collector, no tail sampling, head sampling in the SDK. Accepts less, ships
in an afternoon.

```yaml
receivers:
  otlp:
    protocols: { grpc: {}, http: {} }
processors:
  memory_limiter: { limit_percentage: 80 }
  batch: {}
exporters:
  debug: { verbosity: basic }   # remove once the pipeline is verified
service:
  pipelines:
    traces:  { receivers: [otlp], processors: [memory_limiter, batch], exporters: [debug] }
    metrics: { receivers: [otlp], processors: [memory_limiter, batch], exporters: [debug] }
    logs:    { receivers: [otlp], processors: [memory_limiter, batch], exporters: [debug] }
```

Then, in the SDK:

```python
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
trace.set_tracer_provider(TracerProvider(sampler=ParentBased(TraceIdRatioBased(0.05))))
```

Head sampling drops the traces you will need. Move to the two-tier shape as soon
as one incident has been lost to a 5% sample.

## Verifying the exporter is actually sending

`debug` exporter with `verbosity: detailed` prints spans and metrics locally —
use it for 60 seconds to prove the pipeline, then remove it. For a real
backend:

```bash
# otlphttp exporter: check the collector's own error counters
curl -s localhost:8888/metrics | grep -E 'otelcol_exporter_(send_failed|sent_spans)'

# nothing arriving? confirm the path the exporter is hitting
# otlphttp defaults to <endpoint>/v1/traces ; a 404 here means a missing path
```

## Resource attributes: normalise at the collector

```yaml
processors:
  resource:
    attributes:
      - { key: service.namespace, value: shop, action: upsert }
      - { key: deployment.environment, from_attribute: env, action: insert }
      - { key: host.name, from_attribute: k8s.node.name, action: insert }
```

Every service inheriting these gets the same grouping in the backend, which is
what makes a cross-service query ("all errors in `shop`, prod") work without
each team configuring it.

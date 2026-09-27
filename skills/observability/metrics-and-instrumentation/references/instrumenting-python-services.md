# Instrumenting Python services with OpenTelemetry

House default for new Python instrumentation: **OpenTelemetry API + SDK, with
the FastAPI/Flask/Django/psycopg/requests instrumentors as the base layer**,
and hand-written metrics only for domain facts the framework cannot see.
Never add a second vendor SDK on top.

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install opentelemetry-api opentelemetry-sdk \
            opentelemetry-instrumentation-fastapi \
            opentelemetry-instrumentation-psycopg \
            opentelemetry-instrumentation-requests \
            opentelemetry-exporter-otlp-proto-http
```

Note the fast path: `pip install opentelemetry-distro opentelemetry-instrument`
plus `opentelemetry-instrument <package>` auto-instruments a process with no
code change. It is the fastest way to get a baseline, and the config file
(`otel-collector-config` env vars) is easier to review than code.

## Application setup

```python
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.resource import ResourceAttributes

resource = Resource.create({
    ResourceAttributes.SERVICE_NAME: "checkout-api",   # never the hostname
    ResourceAttributes.DEPLOYMENT_ENVIRONMENT: "prod",
    ResourceAttributes.SERVICE_VERSION: __version__,    # ties metrics to a release
})

trace.set_tracer_provider(TracerProvider(
    resource=resource,
    span_processor=BatchSpanProcessor(OTLPSpanExporter(endpoint="http://collector:4318/v1/traces")),
))
metrics.set_meter_provider(MeterProvider(
    resource=resource,
    metric_readers=[PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint="http://collector:4318/v1/metrics"), export_interval_millis=15000
    )],
    views=[View(instrument_name="http.server.request.duration",
                aggregation=explicit_bucket_histogram_advisory(
                    [0.005, 0.025, 0.1, 0.3, 1.0, 3.0, 10.0]))],
))
```

`SERVICE_VERSION` is what lets a graph show "this got worse after 14:02, and
that is when build 4f2a9c shipped". Without it you are guessing.

## Hand-written metrics: only the domain facts

```python
m = metrics.get_meter("checkout")

orders_completed = m.create_counter(
    "checkout_orders_completed_total", unit="{order}", description="Orders successfully paid for"
)
refunds_total = m.create_counter("checkout_refunds_total", unit="{refund}")
cart_size = m.create_histogram("checkout_cart_items", unit="{item}")
queue_age = m.create_gauge("checkout_queue_oldest_item_seconds", unit="s")

def record_order(order_id: str, amount_minor: int, currency: str) -> None:
    # No order_id. It is not a label. Put it on the span if you need it.
    orders_completed.add(1, {"currency": currency, "channel": "web"})
    trace.get_current_span().set_attribute("order.id", order_id)
```

Cardinality worksheet before shipping any of this: `currency(3) × channel(4)
= 12 series per instance`. Write the label list in the PR.

## Async gotchas in Python

- Use the **async** `add`/`record` on async instruments (`create_counter` with
  an async exemplar callback), and `add` with the `Context` argument only if
  you are sure you are in the same context as the request. Passing a
  `context=` from another task attaches the wrong trace.
- `PeriodicExportingMetricReader` buffers in memory. On a hard kill (SIGKILL,
  OOM) the last interval is lost — that is why the interval should be short
  (10–30 s) for error metrics you page on.
- Instrumenting inside a `for` loop over rows multiplies metric calls. Hoist to
  one call per request with a histogram of the batch size instead.
- `time.perf_counter()` around the whole request is fine; around every function
  call is not.
- If you use `loguru` or `structlog`, wire the OTel `LoggingHandler` (or
  `structlog` processor) so `trace_id` and `span_id` land in every log record —
  that is the entire log↔trace correlation story, and it is four lines.

## Verify the instrumentation you just added

```bash
# 1. a request produces a trace and the right metric names
curl -s -o /dev/null -XPOST localhost:8000/orders -d '{"sku":"A1"}'

# 2. the collector sees them (v1 has no rich API; query the backend or the OTLP receiver log)
curl -s localhost:4318/metrics 2>/dev/null || tail -f /tmp/otelcol.log | grep -i export

# 3. sanity-check the series count
curl -s -G localhost:9090/api/v1/label/__name__/values | tr ',' '\n' | grep checkout
```

Expected: `checkout_orders_completed_total` with `currency`/`channel` labels
only, one span per request with `http.route=/orders`, and no series containing
an order id.

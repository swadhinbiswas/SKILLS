# Naming and label conventions

## Canonical form

```
<namespace>_<subsystem>_<subject>_<unit>[_<type>]
```

| Part | Rule | Example |
|---|---|---|
| namespace | owning team or product, not the language | `checkout`, `platform` |
| subsystem | where the measurement happens | `http_server`, `db_client` |
| subject | the thing measured | `request_duration` |
| unit | base unit, singular, in the name | `_seconds`, `_bytes`, `_ratio` |
| type | `_total` for counters only | `_total` |

Examples:

```
http_server_request_duration_seconds      histogram  route, method, status
http_server_requests_total                counter    route, method, status
checkout_orders_completed_total           counter    currency
checkout_orders_completed_total           counter    (only ONE of the above is right —
                                                 pick one and use it everywhere)
db_client_pool_connections_active          gauge      pool
db_client_pool_connections_limit           gauge      pool
```

Pick one spelling of a concept for the whole org and enforce it in review. A
dashboard that says `requests_total` in one panel and `http_requests_count` in
the next is a dashboard nobody trusts.

## Label rules

1. **Closed enumeration.** Every label's value set is written down in the
   service's instrumentation doc. Unknown values collapse to `other`.
2. **Same label set across all series of a metric.** A label present on some
   series and absent on others creates a second, invisible population and
   breaks `sum by`.
3. **Never a value you did not choose.** If a client can influence it, it is
   either enumerated or it is not a label.
4. **One low-cardinality "scope" label** at most — `service`, `environment`.
   Everything per-instance is the backend's job (`instance`, `pod`, `job`).
5. **Version/build labels go on `build_info`**, not on the metric itself, so
   rolling deploys do not create a new series per release.

## Values that are never labels

| Never a label | Put it in |
|---|---|
| `user_id`, `account_id`, `email` | trace/span attribute |
| `request_id`, `trace_id`, `session_id` | log field / trace link |
| `error_message`, `stack` | trace span, log field |
| concrete URL with an id in it | `route` template + span attribute |
| `duration_ms` | the value of a histogram |
| anything the user typed | nowhere |

## Route labelling

Normalise at the HTTP middleware, using the router's matched pattern. Verify:

```bash
# If this creates a new series, your route label is not a template.
curl -s -o /dev/null "http://localhost:8080/users/999999/orders"
curl -s localhost:9090/api/v1/series | grep 'route="\/users'
# -> expect only route="/users/{user_id}/orders"
```

A handful of backends will show this as a metric named
`http_server_request_duration_seconds` gaining a new `route` value; newer
Prometheus-compatible backends return an error like
`error ingesting samples: error on ingesting samples that are too long` or a
"label values limit" message when cardinality explodes — the backends that
*silently* drop the series are worse, because the graph just goes flat.

## Unit and conversion

- Emit base units: seconds, bytes, seconds of CPU.
- Never `_ms` or `_kb`. If you are certain you need milliseconds for
  human-readable dashboards, convert in the query: `histogram_quantile(0.99,
  ...) * 1000`.
- Ratios must be `0.0`–`1.0`, not percentages. A metric called `_ratio` that
  occasionally reports `104.0` is a bug in the instrumentation.
- Timestamps belong in the event, not in a gauge. A `last_updated` gauge is
  never more than one scrape behind, and is wrong whenever the process is
  frozen — the exact moment you are trying to diagnose.

## Cardinality worksheet

Fill this in for every new metric, and put it in the PR description:

```
metric:            checkout_orders_completed_total
labels:            currency(3) status(2) channel(4)
series:            3 × 2 × 4 = 24
instances:         40
total series:      24 × 40 = 960
new series per req: 0 (values are enumerated)
```

If the total across all metrics on a service approaches your backend's series
limit, the answer is a recording rule (pre-aggregation) or dropping a label —
not a bigger instance.

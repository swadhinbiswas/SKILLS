# Burn-rate thresholds

## Where the numbers come from

Everything below is arithmetic from the SLO target `A` and the window `W`.
Nothing here needs to be memorised.

```
budget_fraction      B = 1 - A                      (0.001 for 99.9%)
burn_rate            r = observed_error_ratio / B   (r = 1 -> spending evenly)
alert error ratio    = r * B                         (14.4 * 0.001 = 1.44%)
time to exhaust      = W / r                         (r = 14.4, W = 28d -> 46.7h)
```

So for a 99.9% SLO: `B = 0.001`, the page threshold is an error ratio above
**1.44%** in both the short and the long window, and at that error rate the
28-day budget is gone in **~47 hours**.

The two burn rates have a meaning:

- **`r = 14.4`** — the whole 28-day budget is spent in ~2 days. Fast enough to
  need a human, slow enough that one bad deploy does not page four times.
- **`r = 6`** — the budget is spent in ~4.7 days. Real damage, not an
  emergency: a ticket.

The long window being several times the short one is what supplies "this is
sustained, not a blip": a 90-second total outage shows a 3% error ratio over a
30-minute window, comfortably below 1.44%, so it does not page.

The window/burn-rate **pairings** below are the ones in wide circulation from
the Google SRE Workbook (Ch. 14) and the SRE site. The error-ratio columns are
computed from the formula above — check them against your own SLO rather than
copying them.

## Window and threshold table

### 99.9% availability, 28-day window (budget 0.1% = 40 min of downtime)

| Severity | Long window | Short window | Burn rate | Alert error ratio | Time to exhaust | Action |
|---|---|---|---|---|---|---|
| Page | 1 h | 5 min | **14.4** | > 1.44% | 46.7 h | Page |
| Page | 6 h | 30 min | **14.4** | > 1.44% | 46.7 h | Page |
| Ticket | 3 d | 6 h | **6** | > 0.6% | 112 h | Ticket |
| Ticket | 6 d | 3 d | **3** | > 0.3% | 224 h | Ticket, plan work |

Keep the **5 min / 1 h** pair *in addition to* **30 min / 6 h**: a short window
is the only way to catch an outage that is 100% errors but too brief to fill a
30-minute window. The longer pair catches sustained degradation without
flapping.

### 99.95% availability, 28-day window (budget 0.05% = 20 min)

| Severity | Long window | Short window | Burn rate | Alert error ratio | Time to exhaust |
|---|---|---|---|---|---|
| Page | 1 h | 5 min | **14.4** | > 0.72% | 46.7 h |
| Page | 6 h | 30 min | **14.4** | > 0.72% | 46.7 h |
| Ticket | 3 d | 6 h | **6** | > 0.3% | 112 h |

### 99.5% availability, 7-day window (budget 5% = 8.4 h)

| Severity | Long window | Short window | Burn rate | Alert error ratio | Time to exhaust |
|---|---|---|---|---|---|
| Page | 6 h | 30 min | **6** | > 3% | 28 h |
| Ticket | 3 d | 6 h | **3** | > 1.5% | 56 h |

## Worked PromQL

Given a service with:

- `http_server_requests_total{route, status}` — counter of all requests
- `http_server_request_duration_seconds_count{route}` — counter of *successful*
  (non-5xx) requests, if the instrument records only good events

**Parenthesise the whole ratio.** PromQL binds `/` tighter than `>`, so
`a / b > 0.001` parses as `a / (b > 0.001)` and returns an empty result — an
alert that never fires and looks healthy.

```promql
# error_ratio(route) over an arbitrary window, on one job
(
  sum by (route) (rate(http_server_requests_total{job="checkout"}[$window]))
  - on (route) sum by (route) (
      rate(http_server_request_duration_seconds_count{job="checkout"}[$window])
    )
)
/
sum by (route) (rate(http_server_requests_total{job="checkout"}[$window]))
```

Better: a recording rule, so the alert is cheap and the expression is not
recomputed on every evaluation. Record each window you alert on.

```yaml
# recording rules: checkout:errors:ratio5m, :ratio1h, :ratio6h
- record: checkout:errors:ratio5m
  expr: |
    (
      sum by (route) (rate(http_server_requests_total{job="checkout"}[5m]))
      - on (route) sum by (route) (
          rate(http_server_request_duration_seconds_count{job="checkout"}[5m])
        )
    )
    / sum by (route) (rate(http_server_requests_total{job="checkout"}[5m]))

# alerting rule: fast burn
- alert: CheckoutErrorBudgetFastBurn
  expr: |
    (checkout:errors:ratio5m > (14.4 * 0.001))
    and
    (checkout:errors:ratio1h > (14.4 * 0.001))
  for: 2m
  labels:
    severity: page
    team: checkout
  annotations:
    summary: "Checkout is burning its error budget at more than 14.4x"
    runbook_url: "https://runbooks.internal/checkout/burn"
```

`for: 2m` on top of the two-window condition is belt-and-braces against scrape
gaps and a single bad scrape; it is **not** a substitute for the long window.

## Low-traffic services

Ratios are meaningless when the denominator is small. One failure in a 5-minute
window at 1 rpm is a 100% error ratio and a guaranteed page.

Rule of thumb: if the expected request count in the short window is **< 10**,
switch to absolute counts.

```
page   when:  failed_requests_5m  >= 2   AND  failed_requests_1h >= 5
ticket when:  failed_requests_1h  >= 10
```

Or lengthen the window until the expected count is at least ~10. Document which
variant the alert uses, because "2%" and "3 requests" lead responders to
disagree about whether the alert is correct.

## Window selection summary

| SLO window | Page long | Page short | Ticket long | Ticket short |
|---|---|---|---|---|
| 28 d | 6 h | 30 min | 3 d | 6 h |
| 30 d | 1 d | 1 h | 7 d | 1 d |
| 7 d | 6 h | 30 min | 3 d | 6 h |
| 1 d | 1 h | 15 min | 6 h | 30 min |

Keep the long window at roughly 12x the short one. The exact factor is not
sacred; the purpose of the long window is "this is sustained", and a long
window several times the short one achieves that at every scale.

## What the thresholds are *not*

- Not a promise that the responder will fix it in time. 14.4x buys ~47 hours of
  budget; a human on call at 3am will not fix a 2-day problem in 2 hours. The
  point is to burn the budget slowly enough that a human can act, not to prove
  anyone can meet the SLO after the fact.
- Not a substitute for a total-outage detector. Keep a separate
  "≥N% of the last 5 minutes" fast page so a 100%-failure event is never
  missed by a window that is too long to fill.
- Not applicable to non-availability SLIs without adjustment. Latency and
  freshness SLIs have the same burn-rate structure, but the "bad event" is
  "the SLI threshold was exceeded", and a good/total counter pair does not
  exist by default — you need a synthetic or a request-count-per-success
  proxy. Build that before applying the table blindly.
- Not a substitute for reading your own traffic shape. Check the error ratio
  on a normal Tuesday; if it idles at 0.8%, a 1.44% page threshold has almost
  no headroom and you need to fix the SLI, not the alert.

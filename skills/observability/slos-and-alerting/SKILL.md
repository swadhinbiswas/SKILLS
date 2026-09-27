---
name: slos-and-alerting
description: Define SLOs from user-visible indicators, track error budgets, and page on multi-window burn rate instead of on causes. Covers SLI vs SLO vs SLA, choosing indicators, the multi-window burn-rate formula, error budget as a release gate, and deleting noisy alerts. Use when writing or reviewing an on-call alert, when deciding what to page on, when SLO/error budget is undefined, or when the team is suffering alert fatigue and paging on causes.
compatibility: Examples use Prometheus-style PromQL and Google SRE multi-window burn rates; the method is vendor-neutral.
metadata:
  version: "1.0"
---

# SLOs and Alerting

An SLO is a number you can be wrong about. An alert is a rule that tells a
human to do something. If an alert does not lead to an action, it is a
dashboard, and it belongs on a dashboard.

The method in one line: **measure what users experience, set a target,
alert only when you are burning the budget fast enough that a human can still
do something about it.**

## SLI, SLO, SLA

| | Definition | Who it binds | Example |
|---|---|---|---|
| **SLI** | A *measurement*: the good events / valid events over a window | nobody | "requests that returned 2xx in 60s" |
| **SLO** | A *target* for an SLI over a window | the engineering team | "≥ 99.9% of valid requests succeed, measured over 28 days" |
| **SLA** | A *contract* with consequences (credits, penalties) | the business, legally | "99.95% monthly uptime or a 10% service credit" |

The gap between SLO and SLA is deliberate: the SLO is stricter than what you
promise customers, so you have budget to spend before you're actually in
breach. An SLO set equal to the SLA has no margin and guarantees you breach it.

Set the window longer than your deploy cycle and your busiest seasonal dip.
**28 rolling days** is the Google SRE default and a good house default.

## Choosing SLIs that reflect user experience

Start from the journey, not the component. For each user-visible operation,
decide what "good" means, then find the events that prove it.

| Journey | SLI | Why this one |
|---|---|---|
| Page load | % of page views where the browser received the main content in < 2.5 s | matches Core Web Vitals, not server time |
| Checkout | % of orders that complete (not just API calls that return 200) | a 200 from `/pay` followed by a 500 from `/capture` is a failed order |
| API | % of *valid* requests returning non-5xx | invalid requests are the client's problem, not in the denominator |
| Batch | % of jobs completing within their freshness SLA | late-but-present data is usually a bigger problem than an error page |
| Infra | % of regions with a healthy replica set | a dependency of a dependency |

Rules that keep an SLI honest:

- **Denominator is what the user attempted, not what you served.** If you drop
  a request at the edge, or a client times out and never reaches you, it must
  still count as a bad event. Otherwise a total outage that produces zero
  requests produces a 100% success rate — a real and common bug.
- **Exclusions must be narrow and enumerable**: your own synthetic probes, a
  named customer's scheduled maintenance, health checks. "Traffic from the load
  balancer" is not an exclusion; it's the traffic.
- **Probes measure the probe, not the user.** A green synthetic every 30 s
  says nothing about a request that fails only for real sessions.
- **One SLI per component is a technical metric, not a user SLI.** Keep both,
  but only user SLIs get SLOs and pages. Technical metrics go on the dashboard
  that the responder opens.

## Target selection

| Availability | Error budget / 28d | Budget / day | Downtime equivalent per 28d | Typical use |
|---|---|---|---|---|
| 99% | 10% | 3.4 h | 6.7 days | internal tool, batch |
| 99.5% | 5% | 1.7 h | 3.3 days | low-traffic internal API |
| 99.9% | 0.1% | 4.3 min | 40 min | most SaaS request paths |
| 99.95% | 0.05% | 2.1 min | 20 min | paid API with an SLA behind it |
| 99.99% | 0.01% | 8.6 s | 4 min | requires multi-region; do not promise it from one region + one AZ |

Rules of thumb:

- **Below 99.9% you do not need to page; you need a weekly review.** Budgets
  over 1% cannot be defended by on-call response.
- **Above 99.95% you must be able to shed load and fail over automatically.**
  Otherwise the numbers are fictional and everyone knows it.
- **The cost of a fast page is real.** Assume 2 minutes of responder time per
  page and 8,760 hours/year. A single always-on page at 3/week costs ~43 hours
  of engineer time per year, plus the trust you spend. Budget against that.

Compute the budget to make the SLO concrete, then say it out loud:
"99.9% over 28 days = 0.1% of 28 × 86,400 = 24.2 minutes of non-success in
the window."

## Error budget as a release gate

The budget is the mechanism that makes the SLO real. Choose one, in writing:

1. **Freeze policy** — when the budget is exhausted, all releases require an
   explicit sign-off from the service owner. Cheapest, most effective, most
   often forgotten.
2. **Progressive slowdown** — a pipeline that, as the budget depletes, adds
   manual QA, then a canary hold, then a mandatory two-person review.
3. **Feature flags** — risky changes ship behind flags, rolled out on a
   percentage while the burn rate is watched. Most flexible, most work.

Whatever you pick, the gate must be *automated and visible*, or nobody will
honour it. A budget burned to 100% with no consequence teaches the team the SLO
is decorative, and after that no SLO survives.

Track burn as a rate, not just a balance: "budget 40% consumed at day 9 of 28"
is the leading indicator that gets burned *today*, not the one discovered at the
end of the window.

## Alerting: symptom-based, multi-window burn rate

**Cause-based alerts** ("CPU > 80%", "queue depth > 1000") fire for conditions
that are normal, or irrelevant, or both. **Symptom-based alerts** fire when
users are actually hurt.

Never page on a cause alone. Causes belong on the dashboard. Page on:

> The burn rate of an error budget is so high that, at this rate, the whole
> 28-day budget is consumed in less time than it takes to fix it.

The multi-window rule, from the Google SRE workbook. With a 28-day window:

```
burn_rate = (error_ratio_in_window) / (1 - SLO_target)

page when:  (error_ratio_30m / budget) >= 14.4  AND  (error_ratio_6h / budget) >= 14.4
```

Short window = 30 minutes, long window = 6 hours (a 12x ratio), both conditions
required. The short window gives you speed; the long window gives you the
"still burning" confidence that suppresses flapping and single-spike noise.
With a 99.9% target the budget is 0.001, so the alert fires at an error ratio
above 1.44% in both windows.

Where the numbers come from — compute them, do not copy them from a table:

```
budget_fraction   B = 1 - availability_target      (0.001 for 99.9%)
burn_rate         r = error_ratio / B              (r = 1 -> spending evenly)
alert threshold      = 14.4 * B                    (1.44% for 99.9%)
time to exhaust   = SLO_window / r                (r = 14.4, 28d -> 46.7h)
page burn rate        = 14.4      ticket burn rate = 6
```

The 14.4x factor means the whole 28-day budget is consumed in about two days:
fast enough that it needs a human, slow enough that a single bad deploy does
not page four times. 6x spends it in about 4.7 days, which is a ticket, not a
page.

| SLO | Budget | Page: long / short | Page error ratio | Ticket: long / short | Ticket error ratio |
|---|---|---|---|---|---|
| 99.9% (28d) | 0.1% | 6 h / 30 min | > 1.44% | 3 d / 6 h | > 0.6% |
| 99.9% (28d), high volume | 0.1% | 1 h / 5 min | > 1.44% | 1 d / 2 h | > 0.6% |
| 99.95% (28d) | 0.05% | 6 h / 30 min | > 0.72% | 3 d / 6 h | > 0.3% |
| 99.9% (30d) | 0.1% | 1 d / 2 h | > 1.44% | 7 d / 1 d | > 0.6% |
| 99.5% (7d) | 5% | 6 h / 30 min, at 6x | > 3% | 3 d / 6 h, at 3x | > 1.5% |

Keep the **1 h / 5 min** pair *as well as* **6 h / 30 min*. The short window is
the only way to catch an outage that is 100% errors but too brief to fill a
30-minute window; the long one prevents flapping.

Read `references/burn-rate-thresholds.md` for the derivation, the full table
with time-to-exhaust, the recording-rule PromQL, and the count-based variant
for low-traffic services.

PromQL for the 99.9% / 28-day case, using `http_server_request_duration_seconds_count`
(the good-event counter) against `http_server_requests_total` (all requests).
**Parenthesise the whole ratio** — PromQL binds `/` tighter than `>`, so
`a / b > 0.001` parses as `a / (b > 0.001)` and silently returns nothing.

```promql
# error_ratio_30m > (14.4 * 0.001)   AND   error_ratio_6h > (14.4 * 0.001)
(
  (
    sum(rate(http_requests_total{route="/checkout"}[30m])) by (route)
    - sum(rate(http_server_request_duration_seconds_count{route="/checkout"}[30m])) by (route)
  )
  /
  sum(rate(http_requests_total{route="/checkout"}[30m])) by (route)
) > (14.4 * 0.001)
and
(
  (
    sum(rate(http_requests_total{route="/checkout"}[6h])) by (route)
    - sum(rate(http_server_request_duration_seconds_count{route="/checkout"}[6h])) by (route)
  )
  /
  sum(rate(http_requests_total{route="/checkout"}[6h])) by (route)
) > (14.4 * 0.001)
```

Low-traffic services make ratios jumpy: at 1 request/min, one failure is a 100%
ratio. Below roughly 10 requests in the short window, switch to a **count of
bad events** instead of a ratio ("more than 3 failed checkouts in 30 min **and**
5 in 6 h") and accept a higher false-positive rate. Document which variant the
alert uses, because "2%" and "3 requests" lead responders to argue about
whether the alert is correct.

## Avoiding alert fatigue

The failure mode is a page every few hours that nobody can act on. Causes:

- **Cause-based alerts** (`CPU > 80%`) — page on nothing, and tune them until
  the threshold is meaningless or they're muted.
- **Unrouted alerts** — an alert with no `service` or `owner` label reaches
  everyone and is ignored by all of them.
- **Threshold-on-a-gauge** — 500 consecutive errors is fine for a low-traffic
  service and catastrophic for a high-traffic one. Use rates.
- **No runbook** — an alert without a runbook link is a question, and questions
  go to Slack, not to a pager.

Rules for a pageable alert:

- [ ] It names a **user-visible symptom** (or a resource that will make one
      imminent within one page window).
- [ ] It has an owner (team, not a person) and a runbook link, both in the
      alert payload.
- [ ] It has been **fired in staging or in a drill** in the last 90 days.
- [ ] It is **multi-window**, so a 90-second blip does not page.
- [ ] Someone can name the first two commands to run. If not, it's a
      dashboard widget.
- [ ] Below-traffic guard: it's a rate, and it is expressed in user-visible
      units ("≥2% of checkouts failing"), not in log counts.

Run the numbers on your own alert history: every page a responder cannot act
on should be deleted or demoted to a ticket. A team that is not willing to
delete alerts will end up ignoring the pager entirely, and the day the
real outage comes, the alert that mattered is one of the ignored ones.

### Ticket, don't page

Slow burns (6×) and single-component degradations are **tickets** with an SLO
on the ticket ("resolve within 3 business days"). They go to the backlog, not
the pager. The distinction is where most of the noise disappears.

## Gotchas

- **An SLO with no one looking at it is a dashboard.** Assign the SLO to a
  named team in the same file.
- **Alert on the error budget, not the raw error rate.** A 2% error rate on a
  99.99% SLO is a catastrophe; on a 99% SLO it is a Tuesday.
- **Averaging the error ratio over a window hides correlated failures** (one
  dependency down = 100% of its routes). Compute the ratio over the aggregate
  events, not per-route then averaged, or one flapping route masks a total
  outage.
- **`for:` in a Prometheus alert rule is not the same as the long window.**
  `for: 5m` just requires the condition to hold; it does not require *sustained*
  burn. Use the explicit two-window expression.
- **Restarts reset counters**; a `rate()` over a window that spans a pod
  restart still works, but a window *shorter* than your scrape interval plus
  pod lifetime produces gaps and spurious zeroes.
- **The burn-rate rule needs enough events in the window to be statistically
  meaningful.** The 1-hour short window at low traffic is mostly noise; this is
  why the low-traffic variant switches to counts.
- **Don't page on "no data".** A missing series is a telemetry problem and must
  be a separate, differently-routed alert — otherwise a broken collector looks
  like a healthy service.
- **Multi-window alerting hides a very fast outage** if the short window is long
  (a 5-minute total outage may never fill a 30m window above 14.4×). Keep a
  separate fast "≥N% of the last 5 minutes" page for total outages.
- **SLOs on internal dependencies double-count.** If both the caller and the
  callee have "request success" SLOs, one dependency failure burns two budgets.
  Roll up: the top-level journey SLO is the one you manage.
- **Every alert you delete should be replaced by something**, even a dashboard
  panel. Otherwise you have removed a signal, not noise.

## When to write it down

Keep the SLO definitions in the repo (a `slo/` directory, one YAML per SLO
with the PromQL or the equivalent query, the target, the window, the owner, the
page rule, and the runbook link). An SLO only in a slide deck is an SLO nobody
implements and nobody debugs against.

## Files

- Read `references/burn-rate-thresholds.md` when you are choosing windows and
  thresholds for a specific SLO target, or porting the Google multi-window
  table to a different window length.
- Read `references/alert-review.md` when a team is drowning in pages and you
  need a concrete triage procedure to cut the count in half.

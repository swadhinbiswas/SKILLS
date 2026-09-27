# Alert review and triage

The goal of an alert review is to **cut the page count in half** without losing
a single alert that would have saved an incident. Do it with data, not taste.

## Step 1 — Export the history

Every mainstream alerting system can dump alerts with their firing duration.
Grafana: Alerting → History → Export CSV. Alertmanager: `amtool alert query`.
Prometheus: `/api/v1/alerts`. What you need per alert: name, state
(`firing`/`resolved`), start, end, labels (severity, team, runbook).

Compute, per alert:

```
page_count            = number of distinct episodes
paged_hours           = total time in firing state, at page severity
mttr                  = time from first fire to acknowledge
episodes_per_oncall   = total episodes / number of on-call rotations
wasted_pct            = 100 * (episodes resolved without action) / total episodes
```

## Step 2 — Classify every paging alert

Put each into exactly one bucket. No third bucket, no judgement calls.

| Bucket | Definition | Action |
|---|---|---|
| **A — actionable, keep** | Fires only when a user is or will soon be hurt, and the runbook has a real first step | Keep, add `for:`, multi-window, runbook link |
| **B — actionable, mistimed** | Real symptom, but fires too often for the action (or too rarely) | Retune window/threshold, or move to ticket |
| **C — cause-based** | Fires on a resource or config state, not a user symptom | Delete as a page. Keep the panel on a dashboard the responder opens. |
| **D — not actionable** | Real condition, but the responder cannot do anything about it right now (a dependency's problem, a known upstream bug) | Downgrade to ticket, or route to a different team |
| **E — noise / duplicate** | Duplicates A, or fires on test traffic, deploys, or low-traffic artefacts | Delete |

Rules for the review meeting, one hour, all six on-call engineers in the room:

- The **author of each alert argues for its bucket**. If nobody remembers
  writing it and nobody can name its runbook, it is bucket C or E.
- **Every bucket C and E alert is deleted in the meeting**, not ticketed for
  later. A backlog of "alerts to clean up" is how you get to 400 alerts.
- **Every bucket A alert gets a runbook link before it survives.** An alert with
  a `runbook_url` annotation that 404s is bucket D.
- **Deploy suppression is not a fix.** Silencing alerts during deploys hides
  the deploy-induced outage, which is exactly when you need the page. Fix the
  alerts; use `for:` plus multi-window instead.

## Step 3 — The common re-writes

| Symptom | Re-write as |
|---|---|
| `CPU > 80%` | Burn-rate page on the user-facing SLO. CPU stays a dashboard panel. |
| `pod restart count > 3` | Symptom alert on the request path through that pod group; a crash loop with no traffic is a ticket. |
| `disk > 85%` | Ticket at 85%, page at 95% **and** projected to fill within 24 h (rate of change, not absolute level). |
| `queue depth > 1000` | Page on **oldest item age** (a symptom: staleness), not depth (a cause: throughput). Depth is a dashboard. |
| `error rate > 1%` | Multi-window burn rate at your SLO's threshold. 1% is arbitrary; 14.4× the budget is not. |
| `certificate expires in 30d` | Ticket at 30/14/7 days, page at 72 h, and route to the team that owns the cert. |
| `5xx count > 10` | Rate, expressed as a fraction of traffic, or you will page constantly at 100 rps and never at 0.1 rps. |
| `latency p99 > 1s` | SLO-burn-rate alert; a p99 threshold is meaningless without knowing what the user sees. |

## Step 4 — Verify the result

After the cleanup:

- **Fire every remaining page alert in staging** (or via a manual injection) and
  walk the runbook end-to-end. Record the time. That time is your true MTTA.
- **Re-measure the page count per on-call rotation** after 30 days. Target:
  fewer than one page per rotation outside a real incident. If you are above
  that, the remaining alerts are still bucket C.
- **Add the burn-rate dashboard to the on-call handover doc.** The responder's
  first move should be "open this dashboard", not "guess which panel matters".

## Anti-patterns to kill on sight

- **Vague alerts**: `Service unhealthy`, `Anomaly detected`. An alert that
  cannot be acted on by reading it is a notification, not an alert.
- **Rubber-stamped runbooks**: a link to a wiki page titled "Check the logs".
  If the first line of the runbook is not a command, it is not a runbook.
- **One mega-alert per team** with a label-based "sub-type". Fine for
  dashboards, terrible for paging: the responder has to triage at 3am what
  should have been separate pages with separate runbooks.
- **Alerting on the absence of data as if it were health.** Telemetry failing is
  a different failure with a different responder; route it separately.
- **Never acknowledging to silence.** A silent night of "we just let the pages
  auto-resolve" is a culture problem, not a config problem.

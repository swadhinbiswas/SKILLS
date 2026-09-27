---
name: data-observability-and-lineage
description: Know whether your data is right and where it came from - freshness/volume/distribution monitoring, schema contracts, column-level lineage, source-vs-warehouse reconciliation, and choosing what to alert on versus dashboard. Use when a table looks empty or stale, when a number looks wrong and nobody knows why, or when designing monitoring for a new pipeline. Triggers on "data freshness", "is the data stale", "lineage", "reconciliation", "schema contract", "monitoring for data pipelines", "table is empty", "column-level lineage", "data quality dashboard", "SLO for data".
compatibility: Framework-agnostic; dbt, Great Expectations, Deequ, and warehouse-native constraints are named where relevant. Alert syntax is generic.
metadata:
  version: "1.0"
---

# Data Observability and Lineage

The goal is a single question answered in seconds: **"can I trust this table,
and what do I do if not?"** That means three things — a **freshness SLO**,
**checks that alert (not just log)**, and **lineage** that points to the
upstream owner.

The governing rule from `data-pipeline-reliability` in this repo still holds:
**silent wrong data is worse than a crash.** A check that only writes to a log
row is not monitoring.

## The five things to monitor per table

The standard five (dbt's, and a fine default elsewhere). Run them every run;
alert on the ones marked.

| Check | Question | Severity |
|---|---|---|
| **Freshness** | `max(updated_at)` within the expected age? | **alert** (hard) |
| **Volume** | row count vs expected range? | alert (soft, with hysteresis) |
| **Schema** | columns and types as contracted? no surprise nulls? | **alert** (hard on drift) |
| **Uniqueness / grain** | key unique at the declared grain? | **alert** (hard) |
| **Distribution / business rules** | values in range, not-null where required? | alert (soft/hard per rule) |

Plus, at a lower cadence, **reconciliation** (source vs warehouse counts and
sums) and **schema-contract** checks. See the reliability skill for the check
SQL; this skill is about *what to alert on, and about lineage*.

## Freshness: the most valuable single check

Freshness catches the most common failure: the pipeline silently stopped, and
the dashboard shows stale data that looks fine.

- **Declare an SLO per table, not a global default.** "The daily orders table
  is fresh by 06:00 UTC" is a contract; "fresh within 24h" is not (it would
  pass all day while showing yesterday's data at 5pm).
- **Measure `max(<event or load timestamp>)`**, not `now() - last_run`. What
  matters to a user is how current the *data* is, not whether a process ran.
  A pipeline that runs on time but ingests two-day-old files is "fresh" by
  process time and stale by data.
- **A "0 rows in the latest partition" is a freshness failure** even though
  `max(ts)` is technically recent — a common "technically passing" hole.
- Freshness has *two* components: **is it arriving** (a load is happening) and
  **does it cover the period** (is the data current for the expected window).
  Check both.

## Volume: alert on change, not on absolute count

- Compare to a **baseline** (same weekday over the last 4 weeks, or a
  trailing median), not a hard-coded number. Weekday seasonality makes
  "yesterday had 1000 rows" meaningless.
- **Hysteresis**: alert only below e.g. 50% of baseline (or above 2x), so
  noise doesn't cry wolf. A ±3% threshold alerts constantly and gets muted,
  which is worse than no alert.
- Volume spikes matter as much as drops: an explosive spike is often a
  **duplicate load** (re-run without idempotency) and a silent correctness
  bug.

## Distribution and business rules

- Beyond non-null and range checks, watch **null-fraction per column over
  time** — a column that goes from 0% to 30% null is a schema/semantic
  change even though the column still "exists".
- Watch **cardinality** of key-ish columns: `COUNT(DISTINCT customer_id)`
  suddenly doubling is either a real event or a duplicate load.
- Business rules (revenue > 0, `status` in the allowed set, no future-dated
  rows) encode what "wrong" means for *your* metric. Generic bounds catch
  less; business rules catch the bugs that matter. See
  `data-pipeline-reliability` for the check queries.

## Schema contracts

- A **schema contract** is the agreed shape of a table or topic, enforced at
  the boundary. Fail the load on a contract violation rather than absorbing
  it silently.
- Enforce: required columns present, types unchanged, key columns still
  present. Tools: dbt `source`/contract tests, JSON Schema + a CI gate,
  Avro/Protobuf schemas on a topic, or warehouse `NOT NULL`/check constraints.
- The **producer** should validate against the contract in its CI, so drift
  fails the *producer's* build, not your 3am load. Catching it upstream is
  dramatically cheaper.
- **Semantic** changes (same name/type, new meaning) are invisible to schema
  tools and need a human-reviewed contract change. Log every contract change
  with a date; a silent meaning change is the most expensive kind of drift.

## Lineage: know where a field came from

Lineage answers: "if `fact_sales.net_amount` is wrong, what upstream is
responsible, and who owns it?"

- **Table-level lineage**: which tables feed this one. Usually automatic
  (orchestrator DAG, dbt, warehouse query history). Cheap to get; make sure it
  exists — it is the "blast radius" answer ("what else breaks if this table
  is wrong?").
- **Column-level lineage**: which source column maps to which target column.
  Harder; some tools infer it from SQL, but it is incomplete where there is
  transformation logic. Where a metric has a complex derivation, **write the
  definition down in the semantic layer** — that *is* lineage for the number
  people actually ask about.
- **Semantic layer as the practical answer**: for each important metric, store
  the definition (grain, formula, filters, owner). Most "why is this number
  different" questions are answered by the definition, not a graph.
- **Ownership**: every table and every important metric has a team/owner. This
  is what makes a lineage graph actionable — a blast radius without owners
  is just anxiety.

## Reconciliation: the ground-truth check

Compare source vs warehouse for the same period, on a schedule, for the
tables that matter. Compare **count, sum of a key measure, and distinct key
count** — a matching count can hide wrong values.

- Automate it as a scheduled, alerted job (not a one-off). It catches
  watermark skips, dropped joins, duplicate loads — the bugs nothing else
  sees.
- Give it a small tolerance for rounding/float. Exact float equality across
  systems will alert on nothing meaningful and get muted.
- Run it in tests against a fixture too, so a broken load is caught before it
  reaches production.
- SQL for the reconciliation query is in the `data-pipeline-reliability`
  skill's reliability-patterns reference.

## What to alert on vs what to dashboard

The distinction that keeps alerts useful:

- **Alert** (page a human, with an owner) on things that are **wrong or about
  to be wrong** and need action now:
  - Freshness breach (SLO).
  - Hard correctness failure: uniqueness/grain violation, reconciliation
    mismatch beyond tolerance, schema contract broken.
  - Volume anomaly beyond hysteresis (a drop = data loss; a spike = likely
    duplicate load).
  - A business rule that is a hard constraint (e.g. negative revenue where
    impossible).
- **Dashboard** (visible, not paging) on things that are **informative or
  trend-like**:
  - Volume/distribution over time (the trend *is* the signal; a single
    threshold loses it).
  - Null-fraction and cardinality trends.
  - Pipeline runtime, cost, success rate.
  - Reconciliation drift trends.

An alert must have: **a clear owner, the failing check, the period, the
observed vs expected value, and a link to the run or the fix.** An alert
without an owner is noise; an alert without the observed value is a mystery.

**Route by severity, not by pipeline.** A freshness breach on a dashboard
someone might act on can be a page; a nightly backfill's volume dip is a
ticket. One channel for everything means the important alert is buried.

## Building the monitoring (a practical order)

1. **Freshness + volume on the top 5 tables people actually use.** Highest
   value per hour spent. Set SLOs and alert.
2. **Reconciliation** (source vs warehouse) for the one or two tables behind
   the most important metric. This is the ground truth.
3. **Uniqueness/grain** on the fact tables (catches duplicate loads).
4. **Schema contracts** at producer boundaries.
5. **Distribution/null-fraction trends** on a dashboard.
6. **Column-level lineage** where a metric is genuinely complex — and a
   written definition regardless.

Don't build all six before alerting on freshness. Ship freshness alerts, then
add.

## Dashboards that earn their place

- **Pipeline overview**: per pipeline — last success, duration trend, rows
  loaded, freshness vs SLO. This is the "is anything broken" view.
- **Data quality overview**: per table — freshness, volume vs baseline,
  open check failures, last reconciliation status.
- **Trend views** (not just current status): volume and null-fraction over
  time, so you can see degradation building *before* it breaches.
- Put a link from every alert and every dashboard row back to the run/query,
  so a responder goes straight to evidence.

## Gotchas

- **Monitoring the process, not the data.** "The job succeeded" says nothing
  about whether the numbers are right. Monitor the *output* (freshness,
  volume, checks) — the job's green tick is not a data check.
- **Alerting on every check** trains people to ignore alerts. Tier them: a
  few hard pages, many soft tickets/dashboard. If everything pages, nothing
  is.
- **Noisy alerts get muted and then miss a real incident.** Build in
  hysteresis and a baseline from the start; a check that cried wolf in week
  one is a check nobody trusts in month three.
- **`max(updated_at)` is wrong when the source updates rows in place** — the
  max can be recent while most of the data is old. For CDC, freshness of the
  *stream/offset* is the right signal, not a column.
- **Reconciliation false alarms from timezone/date-boundary mismatches** are
  the most common "the check is broken" case. Pin one timezone for the
  partition key and use it in both the source and warehouse queries.
- **Float sums will never match exactly** across systems. Use a tolerance
  (or compare rounded/relative difference), or the check is useless and gets
  disabled.
- **Lineage graphs nobody maintain go stale.** Auto-generate table lineage;
  write metric definitions by hand (they are the ones people query); assign
  owners in a catalogue so the graph is actionable.

## Checklist

- [ ] Every user-facing table has a **freshness SLO** (specific time, specific
      table), monitored on the *data's* timestamp, alerting when breached.
- [ ] Volume monitored against a **baseline with hysteresis**, catching both
      drops (loss) and spikes (duplicate loads).
- [ ] Grain/uniqueness and the key business rules checked and **alerting**,
      not just logged.
- [ ] **Schema contracts** enforced at the producer, failing the producer's
      build on drift.
- [ ] **Reconciliation** (count + sum + distinct) run on a schedule and
      alerted, with a float tolerance.
- [ ] Table-level lineage exists (blast radius) and metrics have written
      definitions + owners.
- [ ] **Alerts** have an owner, the observed value, the period, and a link to
      the run. Trends are dashboards; wrong-now is a page.
- [ ] Monitored the *output*, not just the job's success.

## Files

- None — self-contained. For the check SQL, severity tiers, and the
  reconciliation query, see the `data-pipeline-reliability` skill in
  `data-eng` (its reliability-patterns reference). For warehouse
  modelling that makes these checks possible (grain, SCD), see
  `data-modeling-for-analytics` in `data-eng`.

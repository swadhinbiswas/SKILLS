---
name: data-pipeline-reliability
description: Build pipelines that fail loudly instead of quietly wrong - idempotency, late and out-of-order data, schema drift, safe backfills, checkpointing, and data quality checks with alerting. Use when a pipeline is flaky, when a re-run doubled the numbers, when a schema change broke ingestion, or when nobody trusts the dashboard. Triggers on "data quality", "duplicate rows", "late arriving data", "backfill", "idempotent", "schema drift", "pipeline failed", "reconciliation", "exactly once", "silent data corruption".
compatibility: Framework-agnostic; examples use SQL and Airflow/Prefect-style tasks. Partitioning syntax varies by warehouse — check yours before running DDL.
metadata:
  version: "1.0"
---

# Data Pipeline Reliability

The governing principle: **silent wrong data is worse than a crash.** A failed
pipeline gets fixed. A pipeline that reports 1,000 orders when there were
900 gets believed for a month.

Every pipeline needs: an idempotent write, a defined answer to "what about
late data?", a schema policy, and a quality check whose *failure* is loud.

## The reliability checklist (per pipeline)

- [ ] Re-running any run for any past date produces identical output.
- [ ] Late/out-of-order events are handled by a defined policy (see below).
- [ ] A schema change fails the load, or is absorbed deliberately — never
      silently.
- [ ] Every run records a **watermark / high-water mark** and a row count.
- [ ] Quality checks run every run and **alert on violation**, not just log.
- [ ] A backfill can be run over any date range, throttled, without
      clobbering live data.
- [ ] Source vs warehouse counts reconcile (see
      `data-observability-and-lineage` in this repo).
- [ ] Every metric/table has an owner and a definition of "correct".

## Idempotency: the property everything rests on

Retries, backfills, catch-up after downtime, and re-runs all mean **the same
code runs twice on the same data**. If that is not safe, none of the
guarantees hold.

**Make the write idempotent by construction:**

- **Overwrite a partition, don't append.** Writing `WHERE event_date = '2026-01-01'`
  and replacing that partition means a re-run is a no-op. `INSERT` on re-run
  duplicates.
- **MERGE / upsert on a deterministic key.** The key must be derived from the
  data, not generated per run. `uuid4()` generated at write time makes a
  retry a duplicate. Use the source's id, or `uuid5`/a hash of the natural
  key (`user_id` + `event_id` + `version`).
- **Staging + swap.** Write to a temp partition/table, then atomically replace
  the target. On failure, the target is untouched.

```sql
-- idempotent by partition overwrite (run for a date, any number of times)
DELETE FROM fact_orders WHERE dt = '2026-01-01';
INSERT INTO fact_orders SELECT * FROM stg_orders WHERE dt = '2026-01-01';
-- better: write stg -> MERGE into fact keyed on order_id
```

- **External side effects are the hard case** — publishing to a queue, calling
  a payments API, emailing. Make the *target* idempotent (a dedupe key the
  consumer checks) or use a transactional outbox. Never "the retry will
  probably see it".
- **Test it:** run the pipeline twice for the same partition in a test
  environment and assert the target is byte-identical. If it is not, nothing
  else in this document matters yet.

## "Exactly once" is mostly an illusion

Nobody gets true exactly-once across a whole system. What you get:

- **Exactly-once processing within a bounded transaction** (a single
  `MERGE` against a warehouse, a Spark structured-streaming run with a
  checkpointed sink) — real, and useful.
- **At-least-once everywhere else**, and the *consumer* is responsible for
  deduplication.

So the design rule: assume duplicates and make them harmless (idempotent keys,
`DISTINCT ON`/dedupe-on-read, unique constraints), rather than assuming
exactly-once. See `streaming-vs-batch-decisions` in this repo for how
partitioning and offset management interact with this.

## Late-arriving and out-of-order data

Real feeds are late. Decide the policy **per source**, explicitly:

| Policy | When | Trade |
|---|---|---|
| **Ignore late data** (watermark = `max(timestamp)` seen) | Late data is rare/meaningless; you can accept loss | Simple; **loses data silently** — at least log the dropped count |
| **Lookback window** (re-read the last N hours/days every run) | Common, bounded lateness | Re-processes a little every run; needs idempotency above |
| **Revisit window per event** (a row's "current" version wins) | CDC / upsert semantics | Needs merge keys and versioning |
| **Buffer until complete** (declare a lateness SLA, close the window after it) | You control the producer | Data appears late for users; SLA must be published |

- **Watermark** = "I have seen all data up to this point." Store it per
  pipeline. A watermark set to `now()` rather than `max(source_ts)` will
  skip late rows silently.
- **A lookback window is the pragmatic default for batch**: every run
  re-reads the last `N` intervals (e.g. `max(event_date) - 3 days` to
  `max(event_date)`), merges, and is safe because it is idempotent. Choose `N`
  from the source's observed lateness, not a guess — measure the p99
  lateness first.
- **Out-of-order** events within a window are fine with a merge. Across a
  closed window, they are lost — so the lateness SLA is part of the
  contract.
- **"Corrected" source data** (an order cancelled after the fact) is not
  lateness, it is an update. It needs versioned upserts (keep the latest
  `updated_at` per key), not an append.

## Schema drift and evolution

- **Contract the schema with the producer.** A schema registry (or at
  minimum, a checked schema file the producer CI-validates against) turns
  silent drift into a failed build.
- **Fail on unknown fields, do not ignore them.** Auto-adding a column
  because it appeared is how `id` becomes `id2`. Quarantine unexpected
  fields/records to a `_quarantine` table and alert.
- **Type changes are breaking changes.** `int → string`, `null → not-null`,
  a field becoming optional — all require an expand/contract migration with
  both sides reading the old and new shape during the transition.
- **Nullability matters more than type.** A field that was always populated
  becoming nullable will quietly produce nulls downstream. Null-fraction
  checks per column catch this.
- **Semantic changes are invisible and the worst.** Same name, same type,
  different meaning (cents → dollars, `status` gaining a new value, a `tz`
  change). Only a human review of the contract catches these; do it.

## Backfills: run the same code, over a range, throttled

- **Backfill the production code path.** A one-off "backfill script" that
  differs from the DAG is where silent corruption comes from. The only
  difference should be the date range.
- **Throttle.** Cap concurrency, chunk the range. A 2-year backfill that
  re-runs 500 downstream tasks per day does not finish and takes the warehouse
  down trying.
- **Isolate or pause.** If the backfill writes where the live run also writes,
  they race. Either write to a separate target (a `backfill_` suffix) or pause
  the live pipeline for the duration. Decide *before* starting.
- **Use data intervals, not "today".** Pass the interval explicitly; never
  `now()` inside a task (see `data-pipeline-orchestration` in this repo).
- **Backfills are the main source of duplicates and clobbering.** Treat one
  as a production change: dry-run on one partition, diff the result, then
  scale up.

## Checkpointing and restart

- **Checkpoint = how far we got.** Store the watermark/high-water mark, or
  the Spark/Kafka offset, in durable storage (a table, a checkpoint file on
  shared storage, a broker offset). Never in process memory.
- **Make a partial run safe to abandon.** Write to temp/staging and swap at
  the end, or commit per-partition. A job killed halfway should restart from
  the last committed unit, not from zero and not from a half-written target.
- **Idempotency makes restart trivial**: if the write is idempotent, "just
  re-run from the last good watermark" is always safe. Prefer that to clever
  partial-resume logic.
- **Set a timeout and a retry policy per task** (see
  `data-pipeline-orchestration`).

## Data quality checks that are actually load-bearing

Run checks **every run** and **alert on violation** — a check that only logs is
not a check. The standard four (dbt's, and a fine default elsewhere):

1. **Freshness** — `max(updated_at)` in the target is within the expected age.
   The single most useful check: it catches a pipeline that stopped running.
2. **Volume** — row count for the period vs an expected range (e.g. vs the
   same period last week, or a hard min/max). Catches a truncated load or an
   explosive duplicate.
3. **Schema** — columns present, types as expected, no unexpected nulls in
   required fields.
4. **Uniqueness / referential** — grain is what you think (no duplicate keys
   where there should be none), foreign keys resolve, values in range.

Add business rules that encode what "wrong" means for *your* metric (revenue
> 0, `status` in the allowed set, no future-dated orders). Then:

- **Severity tiers:** a *hard* violation should fail the run (block
  publishing the bad partition). A *soft* one alerts and continues. Decide
  which per check — a volume anomaly should usually not block, a duplicate
  key in a fact table should.
- **Threshold with hysteresis** so it alerts on a real drop, not ±3% noise
  (e.g. alert if volume < 50% of the 4-week same-day average).
- **Alert on the check, route to an owner**, with the failing check, the
  period, the observed value, and the link to run it. See
  `data-observability-and-lineage` for the alerting and dashboard design.

## Reconcile source vs warehouse

The check that catches everything else. Periodically (and in tests), compare
counts and sums between source and warehouse for the same period:

```sql
-- orders: does the warehouse agree with the source for the day?
SELECT
  (SELECT count(*) FROM fact_orders WHERE dt = '2026-01-01')      AS wh_count,
  (SELECT count(*) FROM prod.orders WHERE created_at::date = '2026-01-01') AS src_count;
-- same for sum(amount), and distinct order_id
```

Reconcile **count, sum of a key measure, and distinct count** — a count alone
can match while the values are wrong. Automate it as a scheduled check, not a
one-off. See `data-observability-and-lineage` for building this as a
first-class, alerted job.

## Gotchas

- **A task that "succeeded but wrote nothing"** (empty source day) looks like
  success to the next task. Return an explicit empty marker and have
  downstream handle it — do not let "0 rows" masquerade as a normal load.
- **`INSERT` + a retry is the #1 source of double-counted revenue.** Any
  append-write pipeline needs either partition overwrite, a merge, or a
  dedupe step. Verify by running twice.
- **Joining to a dimension that is still loading** silently drops rows
  (inner join) or double-counts (fan-out on a many-to-many join). Load
  dimensions first and assert join cardinality.
- **`NOT NULL` added to a populated column, or a type change, without a
  backfill** fails mid-load and can leave a partial write. Expand/contract.
- **Timezones**: a pipeline that assumes UTC while the source is local shifts
  every boundary and quietly changes daily totals. Pin one timezone for the
  *partition key* and document it.
- **`now()` inside a task** makes runs non-reproducible and breaks
  incremental logic. Use the run's data interval.
- **Parallel writers to one partition** corrupt it. One writer per partition
  per run; enforce it.

## When to stop adding pipeline and fix the design

- The pipeline re-runs are not idempotent and cannot be made so → the write
  model is wrong (change to partition-overwrite or merge).
- Late data is unbounded and no window contains it → you need a real
  upsert/CDC model, not batch.
- Quality checks fire constantly and get ignored → the thresholds are noise;
  rebuild them around real business rules, not generic bounds.
- Every backfill takes down the warehouse → the jobs are too coarse; slice by
  partition and throttle properly.

## Files

- `references/reliability-patterns.md` — per-pattern detail: idempotent
  writes (partition overwrite, merge, outbox), watermark strategies,
  schema-drift handling (expand/contract, quarantine), and a dbt-style test
  library you can copy.
- For scheduling, DAG structure, and backfill mechanics across orchestrators,
  see `data-pipeline-orchestration` in this repo. For freshness/volume/
  lineage monitoring and reconciliation, see
  `data-observability-and-lineage`.

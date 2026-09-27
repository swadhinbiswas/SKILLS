# Reliability patterns, per pattern

Detail behind the patterns in SKILL.md. Copy the shapes; adapt table names.

## Idempotent write patterns

### Partition overwrite (batch, per-day/partition)

The default for batch fact loads. Any number of runs for the same partition
produce the same result.

```sql
-- pseudo-SQL; adapt to your warehouse's transaction/partition semantics
BEGIN;
CREATE OR REPLACE TABLE fact_orders PARTITION (dt = '2026-01-01') AS
  SELECT ... FROM stg_orders WHERE dt = '2026-01-01';
-- on error, the transaction aborts and the old partition is intact
COMMIT;
```

Key property: a crash mid-write cannot leave a half-loaded target, and a
re-run overwrites rather than appends. This is the strongest, simplest
idempotency guarantee in batch.

### MERGE / upsert (incremental, any arrival order)

Use when a partition may be updated after it was first written (late data,
corrections, CDC).

```sql
MERGE INTO dim_customer AS t
USING stg_customer AS s
  ON t.customer_sk = s.customer_sk       -- deterministic key
WHEN MATCHED AND s.updated_at > t.updated_at THEN
  UPDATE SET name = s.name, tier = s.tier, updated_at = s.updated_at
WHEN NOT MATCHED THEN
  INSERT (customer_sk, name, tier, updated_at) VALUES (s.customer_sk, s.name, s.tier, s.updated_at);
```

- The join key must be a **deterministic** business key (source id, or a
  deterministic surrogate). Never a per-run `uuid4()`.
- The `WHEN MATCHED ... WHERE` guards against an out-of-order update
  overwriting a newer version (keep the latest `updated_at`).
- MERGE performance degrades badly without a matching index on the ON clause
  and, on some engines, without a small, pre-deduped source. Dedup the source
  to one row per key *before* the merge.

### Transactional outbox (external side effects)

For "run this and also publish/notify" without either double-publishing or
losing the publish.

1. In the **same transaction** as the data write, insert a row into `outbox`
   (the intended external effect, with a dedupe key).
2. A separate process reads `outbox` and performs the effect, then marks it
   sent.
3. The consumer of the effect dedupes on the outbox id (idempotent target).

Result: the effect happens at-least-once, and the *target* dedupes, which is
effectively exactly-once from the target's perspective.

### Dedupe-on-read (last resort)

Keep append-only (fast, immutable) and dedupe at query time:

```sql
SELECT * EXCLUDE rn FROM (
  SELECT *, row_number() OVER (PARTITION BY event_id ORDER BY ingested_at DESC) rn
  FROM raw_events
) WHERE rn = 1
```

Cheap to write, but every consumer pays the dedupe and the raw table grows
forever. Prefer a merge for anything queried often.

## Watermark strategies

The watermark is "I have seen all data with `source_ts <= W`". It is the
basis of incremental reads and of deciding what is late.

```
                       <-- already processed -->|<-- lookback re-read -->|
source events:  ......|-----------|----L1----|----L2----|>
watermark W:                    ^
```

Strategies:

- **`max(source_ts)` watermark**: W = max timestamp seen. Simple, but a single
  out-of-order event *behind* the max is skipped. Add a lookback.
- **Lookback window (recommended for batch)**: every run reads
  `[W - lookback, W]`, merges idempotently. `lookback` >= p99 observed
  lateness. Costs re-processing a little every run; safe because of
  idempotency.
- **Watermark that advances by completeness, not by time**: advance W only
  when a source-side "all data up to T delivered" signal is seen (common with
  CDC). Exact, requires producer support.
- **Watermark that does not advance past a gap**: if events jump from T1 to T3
  with nothing in between, hold W at T1 until the gap is explained. Prevents
  "the pipeline skipped a window" bugs.

Where to store the watermark: a small table (`pipeline_name, watermark_ts,
updated_at`) or the orchestrator's own state. Never in process memory.

## Schema drift: expand / contract, and quarantine

**Producer adds an optional field:** no action if the loader tolerates unknown
fields; tighten the loader to quarantine them once you're ready to notice.

**Producer adds a required field, or widens a type (int→string):**

- **Expand**: add the new column (nullable), deploy readers that read the old
  OR new. Backfill old rows.
- **Contract**: once all readers/writers are on the new shape and backfilled,
  enforce (NOT NULL, the new type), drop the old.

**Producer removes/renames a field:** treat as a breaking change. Keep the old
field populated for at least one release cycle.

**Quarantine (absorb-with-visibility), when you cannot stop the producer:**

```sql
-- route rows with unexpected shape to a quarantine table, do not drop them
INSERT INTO ingest_quarantine (run_id, received_at, raw_payload, reason)
SELECT '{{ run_id }}', now(), payload, 'missing required field: customer_id'
FROM staging_raw
WHERE customer_id IS NULL;
-- and fail/alert if quarantine is non-empty
```

Quarantine beats both dropping (invisible loss) and loading (corrupt
downstream). The alert on non-empty quarantine is the important part.

## Data quality checks (a copyable library)

The dbt test names are the field standard; the same checks exist in any
framework. Adapt `ref()` to your table function.

**Uniqueness** (grain is what you think):
```sql
-- not_null + unique on the grain key
SELECT customer_sk, count(*) FROM dim_customer GROUP BY 1 HAVING count(*) > 1;
-- or as a dbt test: {{ unique('dim_customer', 'customer_sk') }}
```

**Referential integrity** (joins won't silently drop/duplicate):
```sql
SELECT f.order_id FROM fact_orders f
LEFT JOIN dim_customer d ON f.customer_sk = d.customer_sk
WHERE d.customer_sk IS NULL;    -- should be 0
```

**Accepted values / ranges** (business rules):
```sql
SELECT count(*) FROM fact_orders WHERE status NOT IN ('new','shipped','cancelled');
SELECT count(*) FROM fact_orders WHERE amount <= 0;
SELECT count(*) FROM fact_orders WHERE dt > current_date;   -- future-dated
```

**Not-null on required fields:**
```sql
SELECT count(*) FROM fact_orders WHERE customer_sk IS NULL;
```

**Row-count change (volume, with hysteresis):**
```sql
-- alert if today is < 50% of the same weekday over the last 4 weeks
WITH recent AS (
  SELECT dt, count(*) c FROM fact_orders WHERE dt >= current_date - 28 GROUP BY 1
), baseline AS (
  SELECT strftime(dt, '%w') dow, avg(c) avg_c FROM recent GROUP BY 1
)
SELECT 1 WHERE (SELECT c FROM recent WHERE dt = current_date) <
      0.5 * (SELECT avg_c FROM baseline WHERE dow = strftime(current_date, '%w'));
```

Severity guidance: uniqueness, not-null, and referential on the *grain* → hard
fail (block the load). Volume anomaly, business-range → soft fail (alert,
continue). A "0 rows" result is almost always worth a hard alert even if the
range check would let it pass.

## Reconciliation (source vs warehouse)

Run per period, compare three numbers — count, sum of a key measure, and
distinct key count. A matching count can hide wrong values.

```sql
-- orders, for one day
SELECT
  (SELECT count(*) FROM fact_orders WHERE dt = DATE '2026-01-01')                       AS wh_rows,
  (SELECT count(*) FROM prod.orders WHERE created_at::date = DATE '2026-01-01')          AS src_rows,
  (SELECT sum(amount) FROM fact_orders WHERE dt = DATE '2026-01-01')                    AS wh_sum,
  (SELECT sum(amount) FROM prod.orders WHERE created_at::date = DATE '2026-01-01')       AS src_sum,
  (SELECT count(DISTINCT order_id) FROM fact_orders WHERE dt = DATE '2026-01-01')       AS wh_ids,
  (SELECT count(DISTINCT order_id) FROM prod.orders WHERE created_at::date = DATE '2026-01-01') AS src_ids;
```

Alert when any warehouse number differs from source beyond a tiny tolerance
(rounding). This is the check that catches watermark skips, dropped joins,
and duplicate loads — the bugs that never crash anything.

Automate it as a scheduled job with an owner, and run it in tests against a
known fixture. See `data-observability-and-lineage` for how to wire this into
monitoring.

## Idempotency test (put this in CI)

The highest-value test in a data pipeline's suite:

```
1. Load partition P from fixture data. Snapshot the target (count, sum, hash).
2. Run the same load for P again.
3. Assert the target snapshot is IDENTICAL.
```

If it fails, the pipeline is not idempotent and no other guarantee holds. This
is cheap to write and catches the class of bug that costs the most.

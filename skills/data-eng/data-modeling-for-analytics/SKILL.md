---
name: data-modeling-for-analytics
description: Model a warehouse that analysts can query correctly and fast - fact/dimension tables, star vs snowflake, grain as the first decision, surrogate keys, slowly-changing dimensions (SCD 1/2/3) chosen deliberately, and wide denormalised tables for speed. Use when designing a warehouse, when two teams disagree on a number, when an SCD choice is being made, or when a query is too slow. Triggers on "dimensional model", "star schema", "fact table", "dimension table", "SCD", "slowly changing dimension", "grain", "snowflake schema", "surrogate key", "denormalize", "wide table".
compatibility: Warehouse-agnostic (BigQuery, Snowflake, Redshift, Databricks). DDL syntax varies; the modelling pattern is the deliverable. Read the SCD table in references/scd-types.md before choosing a type.
metadata:
  version: "1.0"
---

# Data Modeling for Analytics

Analytics models succeed when a person can answer a question without a data
engineer and get the *same answer* as everyone else. That comes from getting
three things right: **grain**, **dimensions with history**, and **a clear
key**. Performance is the fourth thing, and denormalisation solves it
cheaply.

## Grain first: the decision that constrains everything

**Grain = what one row of the fact table represents.** Write it as a sentence
and put it in the table's comment/metadata. Examples: "one row per order
line item", "one row per customer per day", "one row per payment transaction".

Everything else follows from grain:

- A fact table at "one row per order line" can answer per-item, per-order, per-day,
  per-customer — by summing/aggregating up. At grain "one row per order" you
  **cannot** answer per-item (information was never stored). **Grain should
  be the finest (most atomic) level that the business actually records.**
- Storing two grains in one table ("transaction header amounts AND line
  counts") invites double-counting the moment someone sums the wrong column.
  Separate fact tables, or separate fact tables at different grains joined
  carefully.
- Every fact table carries its grain implicitly in its primary key. If you
  cannot name the grain, the model is not designed yet.

**The classic failure:** an "order totals" table and an "order lines" table
joined 1:many, then someone sums both. Build one fact at the atomic grain and
aggregate up. Fan-out on a many-to-many join is the other half of this bug
(see below).

## Star schema: facts and dimensions

The default warehouse model. A **star** = one (or few) fact tables + several
**denormalized** dimension tables, connected by surrogate keys.

- **Fact table**: the measurements (numbers you sum: amount, quantity, count)
  plus foreign keys to dimensions and a date/time key. Wide, tall, and it is
  the table that grows.
- **Dimension table**: the descriptive attributes you filter/group by
  (customer name and tier, product category, geography). Narrow, shared by
  many facts.
- **Denormalize dimensions** (repeat the name on the fact) — the defining
  property of a star. Joins stay small and fast, and the "dimension is
  denormalized" is what makes the star simple to query.

```
        dim_customer                dim_product
        (customer_sk PK)            (product_sk PK)
        name, tier, region, SCD...   name, category, brand, SCD...
              |                          |
              \                        /
               \______  fact_sales  ____/
                 customer_sk, product_sk,
                 date_sk, qty, amount
               (grain: one row per order line)
                     |
                  dim_date (date_sk PK, day, week, month, ...)
```

### Fact table types (a useful distinction)

| Type | Grain | Measures | Example |
|---|---|---|---|
| **Transaction fact** | one event/transaction | additive measures | each order line, each payment |
| **Periodic/period snapshot** | one per entity per period | semi-additive (not summable across periods) | account balance per day, inventory per day |
| **Accumulating snapshot** | one per process instance, updated over stages | mostly non-additive | an order's lifecycle, updated as it progresses |

Getting the type right pre-empts "why doesn't SUM(balance) make sense" — a
balance is a *snapshot*; you sum it over entities (across a day), never over
time.

## Star vs snowflake

- **Star**: dimensions fully denormalized (all attributes on the dimension
  table). Simple, fast joins, some redundancy. **Default.**
- **Snowflake**: dimensions split into sub-dimensions (a `dim_product` points
  to `dim_category`, `dim_brand`). Less redundancy, but adds a join and
  complicates BI tools. Only worth it when a huge dimension (millions of rows)
  makes denormalizing it genuinely expensive, or normalisation is mandated.
- **The trade is simplicity/speed vs redundancy.** For most warehouses the
  star wins; denormalized dimensions are small (thousands to low millions of
  rows) and redundancy is cheap. Snowflake a *sub-dimension* only when a
  single denormalized dimension would be too wide/large, not by default.

## Dimension tables and surrogate keys

- **Surrogate key** = a warehouse-generated, meaningless, unique ID
  (`customer_sk`) for a dimension row. It is the join key. Never use the
  source's business key (`customer_id`) as the dimension's PK *when that
  source key can change or be reused*.
- Why not just join on the business key? Because (a) the business key can
  **change** (a customer id reused, a product recoded), which breaks
  historical fact rows, and (b) surrogate keys let you keep **history** in an
  SCD (see below) while facts point at a specific version.
- **The natural key** (`customer_id`, the business id) is kept as an
  *attribute* on the dimension, with a **unique constraint**, for lineage back
  to the source and for lookups. Joins in the warehouse go on `_sk`.
- **Uniqueness of the natural key is enforced** (a unique index/constraint on
  `customer_id`) — otherwise "one dimension row per customer" is a hope, and
  a duplicate dimension row **fans out every fact join** (double-counting).

## Slowly-changing dimensions (SCD): history over time

A dimension attribute changes (a customer moves to a new tier, a product is
recategorised). Facts recorded *before* the change should keep the *old*
value; facts *after* should show the new one. The SCD **type** is the policy
for this.

| Type | History kept? | How | Use when | Cost |
|---|---|---|---|---|
| **SCD1** (overwrite) | No | Update the row in place | Current-state-only is fine (a product's current category) | Simplest; history is lost |
| **SCD2** (add row) | Yes, full | New row per change, with validity range + current flag | You must report "as it was" (point-in-time correctness) | Dimension grows; every fact joins to a version |
| **SCD3** (add columns) | Only the previous value | `prev_tier`, `tier` columns | You only ever need "current and previous" (e.g. churned-into tier) | Limited history; awkward beyond 1 step |

**Default: SCD2** for anything that could be reported historically. It is the
only type that answers "what was the customer's tier when they bought this?"
correctly. Full comparison, mechanics, and the validity-range pattern are in
`references/scd-types.md` — read it before choosing.

- **Type 2 needs validity columns**: `valid_from`, `valid_to` (or
  `is_current`), and a stable surrogate key. Facts reference the specific
  `customer_sk` version.
- **Join facts to the right SCD2 version** by the fact's event date falling in
  `[valid_from, valid_to)`. Getting this join wrong (joining to "current"
  only) is the classic point-in-time correctness bug.

## Point-in-time correctness

The thing SCD2 exists for, and the thing most models get wrong: joining a fact
to a dimension must use the version **as of the fact's date**, not the
current version.

```sql
-- correct: the customer version as of the sale date
SELECT f.amount, c.tier
FROM fact_sales f
JOIN dim_customer c
  ON f.customer_sk = c.customer_sk;   -- fact already points at the right version
-- OR, if joining on the business key, resolve by date:
-- JOIN dim_customer c ON c.customer_id = f.customer_id
--   AND f.sale_date >= c.valid_from AND f.sale_date < c.valid_to
```

If your fact stores `customer_id` (business key) rather than the
`customer_sk` version, you must do this date-resolved join or the report is
wrong for historical periods. Storing the version's `customer_sk` on the fact
at write time avoids the join entirely — a common and robust pattern.

## Wide denormalised tables for query speed

Warehouse fact queries are star-schema joins across large tables. **Denormalising
the hot attributes onto a wide fact** removes the joins:

- **Materalised view / pre-joined "flat" table**: the fact plus the few
  dimension attributes every dashboard filter needs (customer tier, product
  category, region, month). One scan, no joins, dramatically faster BI
  queries.
- Trade-off: **redundancy** (the attribute is copied, so a dimension change
  requires rebuilding the wide table) and **wider rows** (more storage). Pay
  it for hot, frequently-queried facts; don't flatten everything.
- This is a **read-optimised copy**, not the source of truth. Keep the star as
  the model; the wide table is a serving layer.
- Similar reasoning for **pre-aggregations**: a daily/weekly aggregate table
  (`sales_daily`) beside the transaction fact so dashboards don't scan
  billions of rows. Choose the aggregate grain your dashboards actually use.

## Avoiding the two classic double-count bugs

1. **Many-to-many join fan-out.** Joining a fact (order lines) to another
   fact (payments) or to a multi-valued dimension **multiplies rows**;
   `SUM` then double-counts. Fix: aggregate the "many" side to the join's
   grain *before* joining, or join to pre-aggregated sub-totals. Never sum
   across two facts joined at different grains.
2. **Duplicate dimension rows** (no unique constraint on the natural key) —
   same fan-out, silently. Enforce uniqueness.

## Partitioning facts

- **Partition large fact tables by date** (event date) — the single biggest
  performance lever. Queries filter by date range; pruning skips partitions.
- Cluster/sort within partitions on the common filter (e.g. `customer_id`)
  if the warehouse supports it. See `postgres-query-tuning` in this repo for
  the OLTP analogue; warehouse partitioning has the same logic at scale.
- Match the partition key to the **grain's date** and to the way queries
  filter. Partitioning by something nobody filters on is pure overhead.

## Gotchas

- **An accumulating snapshot violates the atomic-grain rule, and it is the
  standard fix anyway.** `fct_order_lifecycle` has one row per order that is
  `UPDATE`d as it progresses, so the fine grain you can answer — the shipped
  line — is not stored anywhere. Only use it for a process with genuinely
  fixed, non-joining keys; if the lifecycle's line-level details are ever
  needed, keep the transaction fact as well and treat the snapshot as
  process-state, not as the analytical fact.
- **A periodic snapshot is restated, not appended, and a late-arriving fact
  silently breaks it.** The row for 2024-03-01 is recomputed each run from
  state as of that day, so a fact that lands three days late either updates a
  window you already published (the number changes under the reader) or gets
  dropped. Bound the restatement window and say what happens outside it.
- **BigQuery's `PARTITION BY` on a fact effectively removes `GROUP BY`
  pruning.** Partition-pruning-then-aggregate against billions of rows is slow
  and bills per byte scanned; an aggregate table (`sales_daily`) beside the
  fact is the fix. "Partitioned" is not the same as "fast" on BigQuery. Two
  BigQuery-specific traps in the same area: `NULL` is not a join key, so
  `fact.customer_sk = dim.customer_sk` drops rows with a null key and makes the
  missing-dimension case invisible (use `IS NOT DISTINCT FROM` and count the
  unmatched facts in a test); and `STRUCT` equality is order-sensitive, so a dbt
  `generate_surrogate_key` over a list of columns and over a struct can produce
  different keys — pick one convention, because changing it re-keys the whole
  dimension.
- **Snowflake micro-partition pruning needs a *constant* predicate.**
  `WHERE date_col = to_date(:run_date)` prunes; `date_col = some_column` or
  `date_col BETWEEN (SELECT min(d) FROM t) AND (SELECT max(d) FROM t)` does
  not, and the full table is scanned. Substitute scalars, never subqueries, in
  a partition filter.
- **SCD2 history bloat comes from tracking the *row*, not the attribute.** The
  SCD policy is chosen per attribute, so a dimension that mixes a rarely-changing
  `country` with a weekly `segment` forks a new version on every segment change
  and rewrites `country` too. Either split the fast-moving attributes into their
  own dimension, or compare only the SCD2-tracked columns when deciding
  whether to close a version.
- **"Which version was current at time T" is a range join, and a range join
  fans out if the ranges overlap.** A gap (no row covering T) *drops* the fact;
  an overlap (two rows covering T) *duplicates* it, and the duplication is
  invisible unless you count rows. Test both directions:
  `count(*)` before vs after the join must be equal.
- **A fact that stores only `customer_id` turns every dashboard query into a
  range join**, because the version has to be resolved at query time — and BI
  tools will not reliably generate the date condition. Storing the resolved
  `customer_sk` at write time is the robust pattern; both are shown above, and
  the cost of switching later is a fact rebuild.
- **Materialising the flat/wide serving table freezes the attributes you
  copied.** A dashboard on `mart_sales_flat` keeps reporting the segment the
  customer had when the mart last rebuilt, not the one the SCD2 dimension
  says is current, and the gap widens silently because the mart looks healthy.
  Rebuild it on every dimension change, or expose its refresh time next to the
  numbers.
- **The unique constraint on a dimension's natural key is usually declared and
  never enforced.** In Snowflake and BigQuery, `unique` is not enforced by
  DDL (Snowflake needs a `hybrid table` or a separate `CREATE UNIQUE INDEX`/
  `CONSTRAINT` on a standard table; BigQuery enforces `PRIMARY KEY`/`FOREIGN
  KEY` only with `require_partition_filter` and `ENFORCED` options, and
  historically not at all). Enforce it as a dbt `unique` test, or the
  "one row per customer" guarantee is a hope.
- **`COUNT(DISTINCT id)` does not commute with a `GROUP BY` + `SUM` in most
  warehouses, and BigQuery's `APPROX_COUNT_DISTINCT` is an estimate.** An
  exact distinct count over a column that is not the group's key requires a
  subquery; a sum of per-group approximate counts is wrong. Pick the aggregate
  that matches the grain and label any approximate metric in its name. The
  related fan-out bug ("order totals + order lines joined 1:many, then someone
  sums both") survives review because the query returns plausible numbers —
  only a row-count check catches it, since `count(*)` of the joined result must
  equal `count(*)` of the driving fact, and a reconciliation that sums one side
  of the join cannot detect it.

## Checklist

- [ ] Wrote each fact table's **grain** as a sentence and chose the finest
      atomic level the business records.
- [ ] Every number has one defined grain; no table mixes two grains' measures.
- [ ] Star schema by default; snowflake only with a real reason.
- [ ] Dimensions have surrogate-key PKs + a **unique** natural key attribute.
- [ ] Chose the SCD type deliberately (default SCD2 for anything reported
      historically); read `references/scd-types.md` first.
- [ ] Facts point at the **point-in-time** dimension version (or the report
      joins by date), so historical reports are correct.
- [ ] Fact tables partitioned by event date.
- [ ] Considered a pre-joined / pre-aggregated serving table for hot
      dashboards; kept the star as the model of record.
- [ ] Can answer "why is this number different from that number?" — grain,
      filter, and SCD version are the first three questions.

## Files

- `references/scd-types.md` — full SCD1/2/3 comparison, the SCD2
  validity-range mechanics and the SCD2 join pattern, Type-1 overrides for
  errors, and hybrid approaches (which to pick).
- `references/star-schema-patterns.md` — concrete star schema designs (sales,
  SaaS subscription, ad/impression click attribution), grain statements,
  conformed dimensions, and the join/aggregation patterns that avoid
  double-counting.

---
name: data-warehouse-modeling
description: Design a dimensional warehouse - star vs snowflake, fact and dimension tables, grain, surrogate keys, and the three types of slowly changing dimension with the cases where each is right. Use when a user is designing a star schema, when a report query is a full scan or a self-join, when a historical report changed its numbers after a dimension update, or when choosing between type 1 and type 2 SCDs. Triggers on "star schema", "snowflake", "fact table", "dimension table", "slowly changing dimension", "SCD type 2", "grain", "surrogate key", "Kimball", "dimensional modeling".
compatibility: Applies to any columnar warehouse; dbt/Snowflake/BigQuery/Redshift/Trino examples are dialect-neutral.
metadata:
  version: "1.0"
---

# Data Warehouse Modelling

Start from the grain — "one row is one X" — and the access patterns, then
build outward. The output is a set of fact and dimension tables with a stated
grain for each, surrogate keys, and a documented choice of SCD type per
dimension.

## Grain first, everything else follows

**One fact table has exactly one grain, written in one sentence.** "One row
per order line item, at the state it was at order-completion time." If you
cannot write the sentence, the table does not exist yet.

Two grains in one table is the modelling mistake that makes queries
impossible later:

- An `fct_order_items` that also carries one row per *order* (a rolled-up
  flag) forces every consumer to `WHERE is_rollup` and makes `SUM(revenue)`
  wrong by an order of magnitude the first time someone forgets.
- A fact table keyed by `order_id` alone, with one row per item, cannot
  answer "revenue per order" without re-aggregating, and it *cannot* support
  a second measure at a different grain (invoice vs shipment).

Rule: **one table per grain.** A rolled-up aggregate is a *different table*
(`fct_orders_daily`), not a flag column.

## Star vs snowflake

| | Star (denormalized) | Snowflake (normalized) |
|---|---|---|
| Dimension tables | Flat, wide, one table per dimension | Split across many small tables |
| Fact table | Foreign keys to each dimension | Foreign keys to a hierarchy of tables |
| Query joins | 2–3 wide joins | 5+ joins through the hierarchy |
| Read cost | Redundant storage | More joins, smaller tables |
| Integrity | Enforced in the ETL | Enforced by the keys |

**Default: star.** Warehouses are read-optimised; the denormalization is
deliberate. Snowflake only when a dimension is genuinely huge and rarely
queried in full (e.g. a `dim_geo_country` below `dim_geo_city`), and even
then keep the join depth shallow. A 3NF warehouse in a columnar store is a
relational design with warehouse latency.

## Fact tables

- **Measures** are numeric, additive where possible, and **nullable at the
  fact level** (a `NULL` discount means "not applicable"; a `0` discount means
  "free" — they must stay distinguishable).
- **Degenerate dimensions**: an `order_id` that lives *in* the fact table with
  no dimension, because it is a transaction identifier, not a business
  attribute. This is standard Kimball and saves a pointless join.
- **Fact tables are usually the largest by far.** They are append-mostly,
  partitioned, and never updated except by a correction pass — updates to a
  fact table are a sign the grain or the process is wrong.
- **Additivity**: a measure is *fully additive* (sum across all dimensions),
  *semi-additive* (a snapshot balance is summable across entities but **not**
  across time — summing balances is meaningless), or *non-additive* (a ratio,
  a distinct count). Label semi-additive measures; the aggregation code must
  know to take the last value across time, not the sum.
- **Degenerate "fact-less fact" tables** (a fact table with only dimension
  keys and a count flag) record events that have no measures (a student
  attends a class on a date) — useful and easy to misuse as if it had
  measures.

## Dimension tables

- **Surrogate keys, always.** A warehouse dimension gets a meaningless integer
  or hash key as its primary key, and the *natural* key is an attribute. This
  is what makes SCD Type 2 possible and what keeps joins narrow (integers).
- **Attributes are stored at the same effective grain as the dimension.** A
  `dim_customer` row must not have a country field that changes independently
  of the row, or the fact table joins to an ambiguous attribute.
- Dimensions are **wide and denormalized** — put a customer's segment, region,
  and signup cohort *in* `dim_customer`, not in separate tables. A query that
  filters on the fact's `customer_key` should need one join.
- Junk dimensions: for a low-cardinality set of flags that appear on most
  facts (is_returned, is_online, tier), put them in a small
  `dim_flags(flag_key, is_returned, is_online, tier)` rather than four
  columns on every fact row.
- **Degenerate date/time is not a thing:** date and time always come from a
  `dim_date` (and `dim_time` for intraday facts), joined on the fact's
  `date_key`.

## Surrogate keys

`dim_customer` primary key is a surrogate; `customer_id` (the source system's
id) is a natural key attribute and is what you join to during the load.

- **Hash the natural key, don't let the DB assign a sequence.** The key must
  be stable across re-runs: `md5(customer_id)` (or a `uuid5`) gives the same
  key for the same customer every time, which is what makes the dimension load
  idempotent. A DB sequence is not reproducible, so a re-run creates a *new*
  key and silently doubles the dimension.
- **Do not use the source id as the fact's foreign key** if you ever want
  history — the fact points at the *version* of the customer (its surrogate
  key), and SCD2 changes that key.
- `dbt` users: surrogate keys in a `dim_` model built from a hash
  (`dbt_utils.generate_surrogate_key`) is the standard pattern; keep it
  deterministic.

## Slowly changing dimensions

The question SCD answers: a dimension attribute changed, and I need the fact
as it looked at the time. Pick per attribute, not per table.

| Type | How | Use when | Cost |
|---|---|---|---|
| **Type 1** | Overwrite the attribute in place | The change is an error/correction, or history genuinely does not matter | History is lost; a past report now shows the new value |
| **Type 2** | New dimension row with new surrogate key + validity dates; fact points at the version | You must reproduce history ("what segment was this customer in on 2024-03-01?") | Dimension grows; every fact must resolve to the right version; late-arriving facts need a "current" lookup |
| **Type 3** | Add columns for the previous value(s) (`prev_segment`, `segment_effective_from`) | You need exactly one "before" comparison; the report is small | Only one level of history; columns multiply; rarely the right answer for more than one change |

Default: **Type 1 for correction-ty attributes, Type 2 for genuinely
historical attributes** (segment, tier, price band, region), Type 3 only when
a single before/after is a hard product requirement.

### Type 2 mechanics (the part people get wrong)

```sql
-- dim_customer with SCD2:
-- customer_key  customer_id  segment   valid_from   valid_to   is_current
-- 1001          c-42         free      2023-01-01   2024-06-01  0
-- 1002          c-42         pro       2024-06-01   9999-12-31  1
```

- Close the old row (`valid_to = change_time`, `is_current = 0`) and insert
  the new one in the **same transaction** as the fact that triggered it,
  otherwise a query between the two sees two "current" rows.
- `valid_to` is exclusive or inclusive — pick one and be consistent;
  half-open `[valid_from, valid_to)` avoids the double-count at boundaries.
- The **fact's** `customer_key` is set at load time to the version that was
  current *at the fact's date*, not to the current one. Loading a late-arriving
  fact (an order from last week) means resolving the version as of *that* week,
  not "whatever is current now" — getting this wrong silently attributes
  history to today's segment.
- The uniqueness guarantee: exactly one `is_current = 1` row per
  `customer_id`. Enforce it with a test; a duplicate current row is a
  duplicate-join bug waiting to happen.
- **Type 2 makes every "current state" query an anti-join or a filtered
  `is_current = 1`**, and makes the dimension the biggest table in a
  frequently-changing attribute. If an attribute changes weekly for a large
  customer base, weigh the Type 2 cost against a `bridge table` (a
  customer × day table) which is more compact for history.

### SCD2 in a dbt model — the shape that works

```sql
-- dim_customer_scd2
select
  {{ dbt_utils.generate_surrogate_key(['customer_id', 'valid_from']) }} as customer_key,
  customer_id, segment, region,
  valid_from, valid_to, is_current
from (
  select
    customer_id, segment, region, change_ts as valid_from,
    lead(change_ts) over (partition by customer_id order by change_ts) as valid_to
  from source_customer_changes        -- a change log, not the current table
) versions
```

Model the dimension from a **change log** (or a full-history extract), not
by diffing the current source table — diffing loses history the moment it is
compressed.

## The mistakes that make queries impossible later

- **No stated grain.** Every future query becomes a guess about whether the
  table is safe to sum.
- **Facts joined to a Type 1 dimension for a historical report.** The report
  runs, looks right, and is wrong for every past period — the kind of bug that
  surfaces in a board deck.
- **A mutable natural key used as the fact's foreign key** instead of a
  surrogate. SCD2 and late-arriving facts become impossible.
- **Putting measures at two grains in one table** (see above).
- **A dimension attribute that varies within the dimension row** (a fact
  depends on which of three "channels" the order used, stored as three
  columns on one dimension row). That is three dimensions.
- **Snowflaking too deep.** A report that needs 7 joins is usually a fact
  that was denormalized wrongly.
- **Using a fact-table `id` as the join key** to a dimension (a fact id is
  unique per row, not per entity).
- **Storing dimensions "for today" only.** If there is any chance a report
  will be run over history, you needed SCD2 from day one, and you cannot
  recover it retroactively without a full history rebuild.
- **A fact table that is updated in place by a late-arrival job**, causing
  double counting. Late facts belong in a *separate correction transaction* or
  a re-load of the affected partition, both idempotent.

## Gotchas

- **`md5` surrogate keys truncate: 128 bits render as 32 hex characters, so
  `customer_id` and `…1` collapse to the same key.** That is a duplicate
  dimension row, which fans out every fact join and silently doubles revenue.
  Concatenate a delimiter (`md5(customer_id)` → `md5('c' || customer_id)`), or
  use `dbt_utils.generate_surrogate_key` / `uuid5`, which handle it.
- **A surrogate key built from only `customer_id` cannot be an SCD2 key.** Every
  version of a customer hashes to the same value, so a Type 2 insert collides on
  the primary key. Include the version: `generate_surrogate_key(['customer_id',
  'valid_from'])`, which the dbt model above already does.
- **The uniqueness guarantee you need is composite, not on `customer_id`
  alone.** "Exactly one `is_current = 1` row per `customer_id`" cannot be a
  plain unique constraint on `customer_id` — a Type 2 dimension has several
  rows per customer by design. Test it as
  `count(*) = count(distinct customer_id) where is_current = 1`.
- **A surrogate key is only reproducible if the natural key's *type* is stable
  too.** `md5(customer_id)` over a string `'42'` and over an int `42` are
  different keys, so a source-side type change silently forks the dimension
  into a second set of rows. Cast the natural key in the model, once.
- **Snowflake `CURRENT_DATE()` is evaluated per row, not per query**, so
  `CURRENT_DATE() = created_date::date` in a multi-day `WHERE` filter returns
  data from the *current* date, not the date the query is filtering for, and
  lookups for historic days silently miss. On Snowflake use
  `CURRENT_DATE()` for filters and `DATEADD(day, -1, CURRENT_DATE())` for
  "yesterday" inside a scheduled run; on Databricks and BigQuery it is query-level
  and behaves as expected.
- **`9999-12-31` is a real date, but `date '9999-12-31' + interval '1 day'`
  overflows.** Any half-open range query against a `valid_to = 9999-12-31`
  open row can error on the sentinel plus-one, and some BI tools filter to
  `valid_to <= <some far future date>` and drop the current row. Use `9999-12-30`
  (or a real far-future date) as the open sentinel, and test the boundary.
- **A full snapshot vs incremental load is a load-mode difference, not just a
  performance one.** Delete+insert (`truncate and load`) is required for a
  correct Type 1 overwrite, but it *destroys* SCD2 history that nothing
  rebuilt — pick the load mode per dimension and write it in the model comment,
  not in tribal knowledge. The same applies to a bridge table: `bridge_*` has
  one row per entity per day, its "measures" are weights, not money, and rolling
  it up across days double-counts. Name it `bridge_*` and give it a grain like
  any fact.
- **Snowflake `MERGE` matches on the target's `ON` clause and a hash key
  computed differently on each side (implicit vs explicit `NULL`, trailing
  spaces, case) matches nothing.** Symptom: the merge inserts instead of
  updating and the dimension doubles. Normalise with `TRIM`/`UPPER`/`COALESCE`
  on both sides, and check with a `select ... except select ...` between the
  ON-clause keys and the target.
- **A fact's `date_key` and the dimension version's `valid_from` disagreeing on
  timezone is the quietest SCD2 bug there is.** A timestamp stored in UTC and a
  `dim_date` built in local time put an 18:00–24:00 UTC event on the wrong day,
  which resolves to the wrong version of the customer and shows up as a single
  day of wrong segment attribution that nobody queries. Pin one timezone for the
  partition key, and convert at the extract.
- **A closed SCD2 row's `valid_from` is the source's change timestamp, not the
  pipeline's `current_timestamp`.** If the extract ran at 02:00 and the event
  carried a `changed_at` of `23:10` the previous day, a `valid_from` of 02:00
  makes a fact that arrived before 02:00 resolve to the new version. Keep the
  source event time for validity, the pipeline time only as an audit column.

## Checklist for a new warehouse table

- [ ] Grain written in one sentence, in a comment, at the top of the model.
- [ ] One grain per fact table; rollups are their own tables.
- [ ] Every dimension is a star (denormalized), joined by surrogate key.
- [ ] Surrogate keys are deterministic hashes, stable across re-runs.
- [ ] Every dimension attribute that can change is classified: Type 1, Type 2,
     or explicitly "history does not matter" with a reason.
- [ ] Type 2 tables have `valid_from`/`valid_to`/`is_current` and a uniqueness
      test.
- [ ] Late-arriving facts resolve their dimension key as of the *fact's* time.
- [ ] Semi-additive measures (balances, counts of state) are labelled so
      downstream aggregation does not sum them across time.
- [ ] Additive facts are summed; ratios and distinct counts are recomputed,
      not summed.
- [ ] Fact table is partitioned by date; dimension is small enough to
      broadcast/fit in cache (see `data-lakehouse-layouts`).
- [ ] Every table has a freshness expectation and a row-count check (see
      `data-quality-and-validation`).

## Files

- None — self-contained. For the physical layout (partitioning, small files,
  table formats) of these tables, see `data-lakehouse-layouts`.

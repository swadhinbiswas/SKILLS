# Star schema patterns and the double-count traps

Concrete designs and the join patterns that keep numbers correct.

## Conformed dimensions

The same `dim_date`, `dim_customer`, `dim_product` are **shared** across
multiple fact tables. This is what makes cross-domain reporting ("revenue by
region by month across all channels") a single join rather than a mess.

- `dim_date` is the backbone: `date_sk` (int `YYYYMMDD`), plus `day`,
  `week`, `month`, `quarter`, `year`, `day_of_week`, `is_weekend`, `fiscal_*`.
  Precompute these once; every fact joins to it and gets calendar attributes
  for free.
- Facts should **share** dimensions where the entity is shared (one `dim_customer`
  for sales and support, not one per fact). Mismatched customer dimensions
  are a top cause of "the two dashboards disagree".

## Pattern: retail sales (the canonical star)

```
dim_date(date_sk) ──┐
dim_store(store_sk)─┤
dim_product(product_sk) ── fact_sales ──┐
              grain: one row per order line
              (store_sk, product_sk, date_sk, qty, gross_amount, discount, net_amount)
```

- **Atomic grain** (order line) means you can roll up to order, day, store, or
  product. Never store order-level totals on a line-grain table (double-count
  on any `SUM`).
- `net_amount = gross - discount` as its own column lets analysts use either
  without recomputing (and without two rows to join).
- Filter helper columns (`is_returned`, `status`) on the fact for common
  dashboard filters.

## Pattern: SaaS subscriptions (snapshots, not transactions)

Subscription billing is a trap for people who model everything as a
transaction fact.

```
fact_subscription_snapshot (grain: one row per subscription per billing period)
  (customer_sk, plan_sk, date_sk, mrr, is_active)
dim_plan(plan_sk, plan_name, tier_rank, list_price)
dim_customer(customer_sk SCD2 ...)
```

- MRR is a **balance at a point in time** → a **periodic snapshot**, not
  additive across time. `SUM(mrr)` is valid *across customers within a
  period*, never across periods. Expanding a subscription across its life as
  a transaction fact (one row per day it was active) also works and *is*
  additive, but inflates the fact by ~30x — pick the snapshot and enforce
  "not summable over time" in the semantic layer.
- `tier_rank` on `dim_plan` lets you do "upgrade" (a customer whose current
  plan rank rose) via a self-join on `customer_sk` across consecutive
  snapshots. Churn is the reverse.

## Pattern: ads / impression-click attribution

```
fact_impression (grain: one row per impression)
  (campaign_sk, ad_sk, date_sk, impressions=1, spend, ...)
fact_click      (grain: one row per click)
  (campaign_sk, ad_sk, date_sk, clicks=1)
```

- **Different facts at different grains — never join them directly.**
  Joining impressions to clicks on `date_sk, campaign_sk` is a many-to-many
  join and `SUM(spend)` over it **multiplies spend by the number of clicks**.
  This is the canonical fan-out bug.
- Correct pattern: compute metrics **per fact independently** and combine at
  the same output grain:

```sql
-- WRONG (fan-out):
-- SELECT d.dt, SUM(i.spend) FROM fact_impression i JOIN fact_click c
--   ON i.date_sk=c.date_sk AND i.campaign_sk=c.campaign_sk GROUP BY 1

-- RIGHT: aggregate each fact to the output grain first, then combine
WITH imp AS (SELECT date_sk, SUM(spend) spend FROM fact_impression GROUP BY 1),
     clk AS (SELECT date_sk, SUM(clicks=1::int) clicks FROM fact_click GROUP BY 1)
SELECT COALESCE(i.date_sk, c.date_sk) dt, i.spend, c.clicks
FROM imp i FULL OUTER JOIN clk c ON i.date_sk = c.date_sk;
```

- **CTR** = `clicks / impressions` (from two facts, computed after
  aggregation), not an average of row-level ratios.

## The join-fan-out rule

Before any `SUM` across a join, verify the join is not many-to-many:

```sql
-- will this join multiply rows? count both sides per key
SELECT c.date_sk, COUNT(*) impression_rows, COUNT(DISTINCT i.impression_id) distinct_impressions
FROM fact_impression i JOIN fact_click c ON i.date_sk = c.date_sk
GROUP BY 1;
-- if row count != distinct count -> fan-out. Aggregate first.
```

Rules of thumb:
- Join fact-to-dimension is fine (dimension key is unique).
- Fact-to-fact is the danger unless pre-aggregated to a shared grain.
- Joining to a **versioned (SCD2) dimension on the natural key without a date
  predicate** fans out across versions — another source of silent
  double-counting.

## Pre-aggregation for dashboards

- `sales_daily(customer_sk, date_sk, net_amount)` beside `fact_sales`:
  dashboards scan millions of rows instead of billions.
- The **aggregate grain must be the dashboard's grain** — too fine and it does
  not help; too coarse and the dashboard needs detail it cannot get.
- Materialised view, `CREATE TABLE AS ... GROUP BY` refreshed by the pipeline,
  or the warehouse's own aggregate/clustering feature.
- Pre-aggregates are a **serving layer**, always rebuildable from the base
  fact. Document the refresh cadence so nobody reads a stale aggregate as
  current.

## A worked "why is this number different" triage

When two teams report different revenue, check in this order:

1. **Grain** — are they summing the same thing (order vs line vs shipped)?
2. **Time window** — is one including open/pending orders, the other not?
   (`status` filter). Is the timezone boundary the same (partition key timezone)?
3. **SCD version** — is one report using the customer's *current* tier/region
   instead of the tier as-of the sale date? (see `scd-types.md`)
4. **Fan-out** — did one report join a many-valued dimension and double the
   total?
5. **Currency/FX** — is one converting, the other not, at a different rate
   date?
6. **Refunds/returns** — is one netting them and the other grossing?
7. **Filter on `is_current`/soft-deletes** — did one include inactive entities?

The first three cause most disagreements, and all three are modelling
decisions, not query bugs. That is why grain and SCD choice come first in
`data-modeling-for-analytics`.

## Naming and metadata that make a model usable

- Consistent prefixes: `fact_` / `dim_` / `agg_` / `stg_` / `mart_`.
- Suffix a table with its grain when it isn't obvious: `fct_order_line`
  vs `agg_sales_daily`.
- **Store the grain sentence in the table comment** and make it visible in the
  BI catalogue. It is the first thing anyone should read.
- Mark non-additive measures (balances, snapshots) explicitly in the semantic
  layer, so the BI tool can warn "don't SUM this across time".
- One owner and one definition per metric; if a metric is computed in several
  places, it *will* diverge — centralise it in a semantic/marts layer.

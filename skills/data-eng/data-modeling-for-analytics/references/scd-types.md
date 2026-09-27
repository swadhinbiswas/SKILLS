# Slowly-changing dimensions: types, mechanics, and choosing

Read this before choosing how a dimension should track change. The default is
SCD2 (full history); the exceptions are the point of this document.

## The problem, precisely

A dimension attribute changes over time. Facts recorded at different times
should see different attribute values:

- Customer "ACME" was `tier = bronze` in January, upgraded to `gold` in March.
- A report of January's orders must show bronze; March's orders must show
  gold.

The **SCD type** is the policy for storing that history.

## Comparison

| | **SCD1 — Overwrite** | **SCD2 — Add row (versioned)** | **SCD3 — Add columns** |
|---|---|---|---|
| **History kept** | None (current only) | Full (all versions) | Only previous value |
| **Storage** | One row per entity | One row per entity *per change* | One row per entity (+ `prev_*` columns) |
| **Mechanism** | `UPDATE` the existing row | `INSERT` a new version row, close the old | `UPDATE` row: current cols + `prev_<col>` |
| **Answers "as it was"** | No | **Yes, exactly** | Only one step back |
| **Row count over time** | Constant | Grows with changes | Constant |
| **Join key stability** | `entity_id` is stable | `surrogate_key` changes per version; natural key repeats | `entity_id` stable |
| **Best for** | Current-state attributes with no historical reporting (a product's current category, a config value) | Anything reported by period — tier, segment, price band, address, status | Rare; "current + immediately previous" (e.g. did they churn into this tier) |
| **Downside** | History lost; past reports change when you look | Dimension grows; every fact must reference the right version; more complex | Clunky beyond one step; hard to extend to arbitrary history |

**Default: SCD2.** Use SCD1 only when you can state that the attribute will
never be reported historically. Use SCD3 rarely; it solves a narrow problem
and ages badly.

## SCD2 mechanics (the pattern)

Store a validity range on every version, plus a current flag:

```sql
CREATE TABLE dim_customer (
  customer_sk   BIGINT,        -- surrogate key: NEW value per version
  customer_id   VARCHAR,       -- natural/business key: REPEATS across versions
  name          VARCHAR,
  tier          VARCHAR,
  region        VARCHAR,
  valid_from    DATE NOT NULL, -- inclusive
  valid_to      DATE NOT NULL, -- exclusive; '9999-12-31' = current
  is_current    BOOLEAN NOT NULL,
  PRIMARY KEY (customer_sk)
);
-- one row per version:
-- 101 | C001 | ACME     | gold   | EU  | 2026-03-01 | 9999-12-31 | true
-- 100 | C001 | ACME     | bronze | EU  | 2020-01-01 | 2026-03-01 | false
```

Loading logic for an incoming change (the standard merge/upsert per natural
key):

1. If the incoming row's tracked attributes differ from the **current**
   version (`is_current`), close the current version (`valid_to = change_date`,
   `is_current = false`) and insert a new version row with a **new**
   `customer_sk` and `valid_from = change_date`.
2. If nothing changed, do nothing (this is what makes the load idempotent).

Constraints/indices to add:
- Unique on `customer_sk` (PK).
- Unique on `(customer_id, valid_from)` — no two versions start at the same
  time.
- Index on `(customer_id, is_current)` for the fast "current" lookup.

### SCD2 fact join (point-in-time correctness)

Facts store the `customer_sk` of the version **as of the fact's event date**:

```sql
-- fact_sales.customer_sk already points at the right version -> simple join
SELECT c.tier, SUM(f.amount)
FROM fact_sales f JOIN dim_customer c ON f.customer_sk = c.customer_sk
GROUP BY 1;
```

If instead you join on the **natural key** and resolve the version by date
(this is the flexible version, but slower and easy to get wrong):

```sql
SELECT c.tier, SUM(f.amount)
FROM fact_sales f
JOIN dim_customer c
  ON  c.customer_id = f.customer_id
  AND f.sale_date >= c.valid_from
  AND f.sale_date <  c.valid_to          -- exclusive end; prevents double-match
GROUP BY 1;
```

`[valid_from, valid_to)` with an **exclusive** `valid_to` is what makes this
join exact — a fact on a boundary date matches exactly one version. If two
versions match, the validity ranges overlap and the model is buggy (or the
join has no date predicate and you get double-counting).

**Getting this join wrong (joining only the current version) is the single
most common point-in-time bug** — historical reports quietly show today's tier
for last year's sales.

## SCD1 and SCD3 mechanics

**SCD1 (overwrite):** `UPDATE dim_product SET category = 'new' WHERE product_id = ...`.
The category is now "current". Any past report re-run today shows the new
category. Acceptable only for attributes that are genuinely timeless
descriptions of the entity (e.g. "material: cotton") or where you explicitly
don't care about history.

**SCD3 (prev columns):**

```sql
ALTER TABLE dim_customer ADD COLUMN prev_tier VARCHAR, ADD COLUMN prev_tier_valid_to DATE;
-- on change: prev_tier = tier; prev_tier_valid_to = today; tier = new_value;
```

Answers "current tier and the tier before it". Beyond one step back the
history is gone. Rarely worth it; prefer SCD2 and just select the version you
want.

## Type 1 overrides (a real hybrid)

Sometimes a value was **wrong** and needs fixing everywhere, not versioned
(typo'd name, bad data load). A **Type 1 override** corrects the dimension in
place, ignoring history:

```sql
UPDATE dim_customer SET name = 'ACME Corp' WHERE customer_id = 'C001';
```

Use for **data-quality corrections**, not for real changes over time. It
rewrites the past, so every historical report also changes — which is correct
if the old value was wrong, and wrong if the old value was real. Have a clear
policy for which is which.

## Hybrid: different attributes, different types in one dimension

You don't have to pick one type for a whole table. Track each attribute
independently:

- `tier`, `segment`, `status` → **SCD2** (reported by period).
- `email`, `phone`, `last_updated_note` → **SCD1** (current contact details;
  not reported historically).
- `country_of_birth` → **SCD1** (immutable anyway).

Implement SCD1 columns with a plain update and SCD2 columns with the
versioned merge, in the same load. The version row is only created when a
**tracked (SCD2) attribute actually changes** — an SCD1-only change updates
in place and does **not** spawn a new version.

Deciding which bucket each attribute goes in is the real work. Ask: "will
anyone ever report this attribute as it was at a past date?" Yes → SCD2.

## Choosing: a decision flow

```
For each dimension attribute, ask: "reported as-of a past date?"
├─ No, and it's a static description  → SCD1 (overwrite). Done.
├─ No, but "current vs one step back" → SCD3 (rare).
├─ Yes, full history needed          → SCD2 (default).
└─ It's a correction of a wrong value → Type-1 override, deliberately.
```

Default the dimension to SCD2 for business attributes you don't yet fully
understand; you can always stop tracking an attribute, but you cannot recover
history you never stored. SCD2 is cheap insurance; SCD1 is a decision you can
only un-make by backfilling from source history (if the source even kept it).

## Gotchas

- **SCD2 dimensions grow without bound** with frequent changes (a volatile
  status). Monitor size; if a dimension explodes, that attribute may not need
  full versioning (SCD1, or version it at a coarser cadence like monthly).
- **`is_current` plus a validity range is redundant but useful** — the range
  is authoritative for joins, the flag is a fast "give me current" lookup. Keep
  them consistent (a `is_current` row must have `valid_to = '9999-12-31'`).
- **Backdated changes** (an effective date earlier than the last version's
  `valid_from`) break a simple append-merge; you must insert the version in
  order and shift the affected ranges. Rare — decide a policy (reject
  backdated changes, or handle them explicitly).
- **Deleted/deactivated entities**: SCD2 keeps them as historical versions
  (facts still join); add a soft `is_active`/`status` rather than deleting, or
  past reports lose their foreign-key target.
- **Joining SCD2 dimensions in BI tools** is easy to get wrong (people drag
  `customer_id` and get fan-out across versions). Expose the **current** view
  (a `dim_customer_current` view or a semantic-layer measure filtered on
  `is_current`) for casual BI, and the versioned table for
  point-in-time reports.

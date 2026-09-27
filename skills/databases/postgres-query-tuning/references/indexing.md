# Indexing reference

## Choosing an index type

| Type | Use for | Notes |
|---|---|---|
| B-tree | Equality, ranges, `ORDER BY`, prefix `LIKE 'x%'` | Default. Most indexes. |
| Hash | Equality only, on wide values | Rarely worth it; B-tree is usually as fast. |
| GIN | Arrays, JSONB, full-text, `pg_trgm` contains-search | Slow to build, fast to update poorly. Consider `fastupdate`. |
| GiST | Geometric, range types, nearest-neighbour, `pg_trgm` | Lossy; needs a recheck. |
| BRIN | Very large, naturally ordered tables (append-only time series) | Tiny and fast, but only good when physical order matches logical order. |
| Hash indexes | Equality on very wide values, in-memory-only tables | No `UNIQUE`, no `ORDER BY` support. Rare. |

## Composite index column order

The rule that generates the most mistakes: **equality columns first, then the
range or sort column.**

Index `(a, b, c)`:

- Serves `a = 1`
- Serves `a = 1 AND b = 2`
- Serves `a = 1 AND b = 2 AND c = 3`
- Serves `a = 1 AND b = 2 ORDER BY c`
- Serves `a = 1 AND c = 3` — **yes**, via index skip scan in Postgres 18+; on
  older versions, only if the planner is lucky. Do not rely on it.
- Does **not** serve `b = 2` (no leading `a` bound)
- Does **not** efficiently serve `a = 1 AND c = 3 ORDER BY b`

`ORDER BY` columns must come *after* all equality columns, and in the same
direction as the sort — `DESC` in the index serves `ORDER BY col DESC`, and
`NULLS FIRST` in the index serves `ORDER BY col NULLS FIRST`. Mismatches force a
sort.

## Partial indexes

Index only the rows you query. Best when a filter is highly selective and
stable:

```sql
CREATE INDEX CONCURRENTLY jobs_pending_idx ON jobs (created_at)
WHERE status = 'pending';
```

Great for soft deletes: `WHERE deleted_at IS NULL` keeps the index small and
never needs updating for deleted rows.

Watch for the failure mode: if the planner stops using the partial index after a
data change, it's usually because the predicate no longer matches a large
fraction of the table — the index stopped being selective, not broken. Check
`pg_stat_user_indexes.idx_scan` over a long window before dropping it.

## Expression indexes

Required when you query a computed value. A plain index on the column does not
help.

```sql
-- Query
WHERE lower(email) = 'a@b.com'
-- Index
CREATE INDEX ON users (lower(email));
```

The expression must match syntactically. `lower(email)` indexed, but the query
writes `LOWER( email )` with different spacing is still fine (the parser
normalises), while `email || ''` is a different expression and misses.

## Covering indexes (INCLUDE)

`INCLUDE` adds columns to the leaf level without affecting the key order, which
enables index-only scans:

```sql
CREATE INDEX CONCURRENTLY orders_covering_idx
  ON orders (customer_id) INCLUDE (total, status);
```

- Widens the index: more disk, more write cost.
- Only useful if the planner can do an Index Only Scan, which needs a fresh
  visibility map. On a hot table you may never get one, and then the extra
  columns are pure cost. Verify with `Heap Fetches: 0` before keeping it.

## Partial + expression + covering combined

For a hot table with a skewed filter, combining is often the win:

```sql
CREATE INDEX CONCURRENTLY orders_active_covering_idx
  ON orders (customer_id) INCLUDE (total)
  WHERE status = 'pending';
```

## Operational rules

- **`CREATE INDEX CONCURRENTLY`** for any table users are writing to. It cannot
  run inside a transaction block (migrations that wrap each statement in a
  transaction must opt out), takes twice as long, and needs two table scans.
- **Check for failure residue.** If it fails, an `INVALID` index remains. It is
  not used, but it still costs on every write:
  ```sql
  SELECT indexrelid::regrelname FROM pg_index WHERE NOT indisvalid;
  ```
  Drop it before retrying.
- **Every index is a write cost**, including indexes only used by a monthly
  report. Adding ten indexes to speed up one dashboard can make every write
  measurably slower.
- **Unique indexes are constraints, not just performance.** They also enforce
  uniqueness, and a duplicate insert raises `duplicate key value violates unique
  constraint`.

## Finding redundant indexes

An index is redundant when another index with the same or a leading-column
prefix already covers its queries:

- `(a)` is redundant if `(a, b)` exists *and* nothing needs `a` alone in a
  different sort order.
- `(a, b)` is redundant if `(a, b, c)` exists.
- Duplicates on the same columns in a different order are a frequent accident:
  `(a, b)` and `(b, a)` — only one can serve a plain `WHERE a=.. AND b=..`
  efficiently, and the other is pure write cost.

Find candidates:

```sql
SELECT s.relname AS table_name,
       s.indexrelname AS index_name,
       s.idx_scan,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size
FROM pg_stat_user_indexes s
JOIN pg_index i ON i.indexrelid = s.indexrelid
ORDER BY s.idx_scan, pg_relation_size(s.indexrelid) DESC;
```

`idx_scan = 0` only means unused since `pg_stat_reset()`. Check
`pg_stat_get_db_stat_reset_time()` before believing it. Confirm by looking for
the columns in the actual query patterns before dropping.

## Indexes and long-running transactions

An open transaction pins `xmin`, which prevents vacuum from reclaiming dead
rows, which bloats the table, which makes every index scan read more pages —
which makes the index look like it "stopped working". Check:

```sql
SELECT pid, state, now() - xact_start AS age, left(query, 80)
FROM pg_stat_activity
WHERE xact_start IS NOT NULL ORDER BY xact_start LIMIT 10;
```

A connection pool with a leaked transaction is the usual cause.

## Global write-amplification check

Before adding an index, count what is already there:

```sql
SELECT count(*) AS index_count, sum(pg_relation_size(indexrelid)) AS total_index_bytes
FROM pg_stat_user_indexes;
```

If a 200-row table has 40 indexes, the problem is the schema, not the missing
41st.

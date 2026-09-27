---
name: postgres-query-tuning
description: Diagnose and fix slow or pathological PostgreSQL queries using real EXPLAIN output instead of guesses - covering missing and wrong indexes, bad join strategies, N+1 loops, work_mem spills, and lock waits. Use when a query is slow, when the database is under load, when someone pastes EXPLAIN (ANALYZE, BUFFERS) output, or when a migration is about to lock a table. Triggers on "slow query", "seq scan", "table bloat", "deadlock", "N+1", "index not used", "query timeout".
compatibility: Requires PostgreSQL 12+ and psql or any client that can run EXPLAIN.
metadata:
  version: "1.0"
---

# PostgreSQL Query Tuning

Find out what a query is actually doing before changing anything. Almost every
Postgres performance problem is visible in the plan, and almost every bad
"fix" makes a different query worse.

## Workflow

Progress:

- [ ] 1. Reproduce with the real query and real parameters
- [ ] 2. Get the plan: `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS)`
- [ ] 3. Read the plan for the *actual* cost driver (see the table below)
- [ ] 4. Apply one fix
- [ ] 5. Re-run and compare — keep it only if the plan and the numbers improved
- [ ] 6. If you added an index, verify it is used and is not redundant

## Step 1 — Reproduce honestly

Tuning `WHERE id = 42` tells you nothing about a query that scans. Get the
production-shaped query:

- Real parameter values. Postgres picks plans per parameter; a plan for
  `id = 1` can be wildly different from `id = 9999999`.
- The real row counts. Statistics lying about a table is the single most
  common cause of a bad plan — if the plan estimates are off by orders of
  magnitude, fix **that** first, before touching indexes.
- The real transaction. `EXPLAIN` in autocommit can produce a different plan
  than the same query inside a transaction that has already written.

`ANALYZE` the table first if you suspect stale stats. For a long-lived
database, autovacuum not keeping up is a real and common cause.

## Step 2 — Get the plan

```sql
EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS) SELECT ...;
```

Read the settings line first. It shows non-default settings *in effect* for
this session, which is often the whole answer (see Gotchas).

Note that `EXPLAIN ANALYZE` **executes** the query. For `INSERT`/`UPDATE`/
`DELETE`, wrap it in a transaction you roll back:

```sql
BEGIN;
EXPLAIN (ANALYZE, BUFFERS) UPDATE ...;
ROLLBACK;
```

For a helper that flags the usual suspects in a plan, run
`python scripts/plan_review.py` and paste the `EXPLAIN (ANALYZE, BUFFERS)`
output in. It is advisory, not a substitute for your own reading.

## Step 3 — Read the plan for the cost driver

Read top-down: the most expensive node is the one with the largest
`actual time` × `loops`, not the one that appears first.

| What the plan says | What it means | Fix |
|---|---|---|
| `Seq Scan` on a large table with low selectivity | No usable index, or stats say the filter is unselective | Index the filter columns; check selectivity first |
| `rows=10` estimated, `actual rows=900000` | Stats are wrong — every downstream decision is suspect | `ANALYZE`, raise `default_statistics_target`, or add extended stats |
| `Nested Loop` with `loops=500000` | The classic N+1 shape | Join once, or batch the lookups |
| `Sort Method: external merge Disk` | Sort spilled to disk | Raise `work_mem` for this session, or add an index that avoids the sort |
| `Hash Batches: 16` | Hash join spilled to disk | Raise `work_mem`, or reduce the join input |
| `Recheck Cond` + `Heap Blocks: lossy=…` | Bitmap index scan lost heap rows, re-checking many | Raise `work_mem` so the bitmap fits |
| `Rows Removed by Filter: 9000000` | Filter applied after reading everything | Index the filter column |
| `Heap Fetches: 12000` on an index-only scan | Visibility map is stale | `VACUUM` the table |
| Same query, different plan per parameter | Plan cache / generic vs custom plan | `plan_cache_mode=force_custom_plan` for that statement |
| `Sort` above a `Limit` that takes few rows | Sort could be index-ordered | Index matching `ORDER BY ... LIMIT` |

**`loops` is where N+1 hides.** A node with `actual time=0.05` and
`loops=200000` is 10 seconds. Always multiply.

## Step 4 — Apply one fix, then re-measure

One change at a time. If you change three things you cannot attribute the win,
and you cannot undo the bad part.

Order of preference:

1. **Fix the query or schema.** Cheapest, most durable, no write cost.
2. **Add the index.** Costs write throughput and storage; gets it back on read.
3. **Change a setting.** Session-level first (`SET`), then database, then
   `postgresql.conf` last.

### Index rules that matter

- Composite index column order is equality columns first, then range/sort
  columns. `(a, b)` serves `a = 1`; it does **not** serve `b = 2`.
- Every index is a write cost on the whole table, including ones only used for
  a nightly report.
- Check for redundancy before adding:
  ```sql
  SELECT indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) AS size
  FROM pg_stat_user_indexes ORDER BY idx_scan ASC;
  ```
  `idx_scan = 0` since the last stats reset is a candidate for removal — after
  confirming the reset was long enough ago to be meaningful.
- `CREATE INDEX CONCURRENTLY` avoids locking writes, but cannot run in a
  transaction block, can fail and leave an `INVALID` index, and requires two
  table scans. If a failure leaves one behind, drop it before retrying.

## Step 5 — Verify

A fix is real when the plan changed **and** the numbers moved:

- The expensive node is gone or much cheaper.
- Buffers dropped (`Buffers: shared hit=… read=…`) — this is the most honest
  measure, because it reflects I/O rather than planner estimates.
- p95 latency for the endpoint, not just the one query.

If the plan improved but latency did not, the query was not your bottleneck.
Go back to the caller — it may be issuing the query in a loop.

## Gotchas

- **`work_mem` is per node, per parallel worker, per sort/hash operation, not
  per query.** A query with 4 sorts can use 4× `work_mem`. Raising it globally
  to fix one query can OOM the machine. Use `SET work_mem` for the session or
  statement.
- **`EXPLAIN` without `ANALYZE` is a guess.** It uses estimates, so it will
  happily show a great plan for a query that is actually slow. Never tune from
  a plan without `ANALYZE`.
- **Sequential scans are not inherently bad.** For a query returning a large
  fraction of a table, a seq scan is *faster* than an index scan plus heap
  fetches. Chasing every `Seq Scan` means adding useless indexes.
- **`LIKE 'foo%'` uses a btree index; `LIKE '%foo'` does not.** For
  contains-search, use `pg_trgm` (GIN) — but it cannot help a plain btree.
- **Functions on the indexed column disable the index.**
  `WHERE date(created_at) = '2024-01-01'` ignores an index on `created_at`.
  Rewrite as a range: `created_at >= '2024-01-01' AND created_at < '2024-01-02'`.
- **`OFFSET 100000` scans and discards every skipped row.** It is O(offset).
  Use keyset pagination instead.
- **A transaction left open holds back `xmin` and can block vacuum across the
  whole database**, causing bloat everywhere. `pg_stat_activity` +
  `state = 'idle in transaction'` is the check.
- **`work_mem` spills show as `Disk` in `Sort Method`/`Hash Batches`,** but a
  clean plan with high `Buffers: read` is disk I/O, a different problem.
- **JSONB `->>` on a computed expression is not indexed** unless you add a
  matching expression index.

## Locking and contention

Different failure mode: the query is fine, the query is *waiting*. Check
before tuning anything:

```sql
SELECT pid, state, wait_event_type, wait_event, now() - query_start AS duration, left(query, 100)
FROM pg_stat_activity
WHERE state <> 'idle' ORDER BY duration DESC;
```

- `wait_event_type = 'Lock'` — something else holds a conflicting lock. Long
  transactions and idle-in-transaction sessions are the usual cause. Fix the
  holder, not the waiter.
- `wait_event_type = 'IO'` — disk. Look at `EXPLAIN (ANALYZE, BUFFERS)`.
- `wait_event = 'IPC'` on a parallel query — workers contending; usually
  `max_parallel_workers_per_gather` or `parallel_setup_cost` mis-tuned.

Deadlocks: `SELECT * FROM pg_stat_activity WHERE query LIKE '%deadlock%'`, or
`pg_locks`. Deadlocks are usually inconsistent lock ordering across code paths —
fix by always taking locks in the same order, not by raising
`deadlock_timeout`.

## Bloat and autovacuum

Symptom: scans get slower over time, tables are large on disk, `Seq Scan` reads
more pages than the row count implies.

```sql
SELECT relname, n_dead_tup, last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables WHERE n_dead_tup > 10000 ORDER BY n_dead_tup DESC;
```

Fix the cause, not the symptom. A high-churn table under default autovacuum
settings needs per-table `autovacuum_vacuum_scale_factor` and
`autovacuum_analyze_scale_factor` lowered, or `VACUUM (ANALYZE)` scheduled.
`VACUUM FULL` takes an `ACCESS EXCLUSIVE` lock and rewrites the table — it is a
last resort, and it needs a maintenance window.

## Schema changes without downtime

Never add a `NOT NULL` column with a volatile default, or a `NOT NULL` column at
all, in one step on a large table. Expand-contract:

1. **Expand** — add the column as nullable, or with a constant default
   (Postgres 11+ adds a constant default without a rewrite; a *volatile* default
   like `now()` still rewrites the whole table).
2. **Migrate** — backfill in batches, in separate transactions, with a small
   `pg_sleep` between batches so you never hold a long lock.
3. **Contract** — once reads/writes are all on the new column and the backfill
   is verified, enforce the constraint: `NOT NULL` via
   `ALTER TABLE ... ADD CONSTRAINT ... NOT VALID` then `VALIDATE CONSTRAINT`,
   which takes only a `SHARE UPDATE EXCLUSIVE` lock.

Backfill pattern that keeps the table responsive:

```sql
UPDATE t SET new_col = old_col
WHERE id IN (SELECT id FROM t WHERE new_col IS NULL LIMIT 5000);
-- repeat in a loop, separate transactions, pause between batches
```

Adding an index on a live table: `CREATE INDEX CONCURRENTLY`, never in the
same transaction as other DDL.

## Migrations safety checklist

- [ ] Does it take a lock stronger than `ACCESS EXCLUSIVE` on a hot table?
- [ ] Does it rewrite the table (new column with volatile default, changed type,
      `VACUUM FULL`)?
- [ ] Is it reversible, and is the rollback written down?
- [ ] Does it need a long transaction, and what does that block?
- [ ] Is `CONCURRENTLY` required, and is the code path free of
      `BEGIN/COMMIT` around it?
- [ ] Has it been run against a copy of production data, at production size?

## When to stop tuning

Stop and change the design when:

- The query is fast but called thousands of times per request. Batch it.
- The join spans a service boundary. Denormalise, or cache.
- The plan is good and the data is simply large. Archive, partition, or
  materialise.
- You are tempted to raise `shared_buffers` past available RAM. It will not help
  and will cause swapping.

## Files

- `references/plan-nodes.md` — every common plan node, what `actual time`,
  `rows`, and `loops` mean for it, and what to do about it.
- `references/indexing.md` — index types, when each is right, expression and
  partial indexes, covering indexes, and how to find redundant ones.
- `scripts/plan_review.py` — parse `EXPLAIN (ANALYZE, BUFFERS)` output and
  flag the usual suspects.

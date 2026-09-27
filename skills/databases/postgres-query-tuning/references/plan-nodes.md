# Plan nodes reference

How to read the fields Postgres prints, node by node.

## The three numbers that matter

Every plan node carries these. Read them together, never alone.

```
actual time=0.088..12.340  rows=45  loops=200
              ^^^^^^^^^^^^          ^^^^^
              startup..total         ^ how many times the node ran
              per loop, ms           v rows returned per loop
```

The number to multiply is **`total_time × loops`**. A node that looks free
(`actual time=0.05`) repeated 200,000 times is ten seconds. This is the N+1
shape and the single most-missed thing in a plan.

- `actual time=a..b`: average ms per loop to produce the first row (`a`) and the
  last (`b`). A huge gap between them means the node is streaming, which is
  usually good.
- `rows`: estimate (`rows=N` alone) vs actual (`actual rows=M`). Divergence is
  a statistics problem and it invalidates every decision above it.
- `loops`: how many times the node was re-executed. Anything above a few
  hundred deserves an explanation.

## Node types

### Sequential Scan

```
Seq Scan on orders  (cost=0.00..18543.00 rows=520000 width=64)
                    (actual time=0.011..8.240 rows=519887 loops=1)
  Filter: (status = 'pending'::text)
  Rows Removed by Filter: 0
```

- `Rows Removed by Filter: 0` → the scan is returning everything, so the plan is
  right. **Do not add an index here.**
- `Rows Removed by Filter: 9,000,000` → the index would have helped. Index the
  filter columns.
- Also correct when the table is small enough to live in a few pages, or when
  the planner thinks the filter matches most rows.

### Index Scan

```
Index Scan using orders_status_idx on orders  (cost=0.29..842.10 rows=52000 width=64)
                                (actual time=0.031..3.980 rows=51893 loops=1)
  Index Cond: (status = 'pending'::text)
```

Each returned row costs a heap fetch. When the match is a small fraction of the
table this is right; when it approaches half, the planner should have chosen a
seq scan and probably misjudged the selectivity.

### Index Only Scan

```
Index Only Scan using users_email_idx on users  (cost=0.29..8.31 rows=1 width=64)
                                    (actual time=0.028..0.030 rows=1 loops=1)
  Index Cond: (email = '...')
  Heap Fetches: 12000
```

- `Heap Fetches: 0` → all-visible, the ideal case.
- `Heap Fetches:` high → the visibility map is stale, so Postgres had to visit
  the heap anyway. `VACUUM (ANALYZE)`. Do not "fix" this with a different index.
- `Filter: (active AND (...))` under an Index Only Scan → an extra condition that
  is not in the index. Add the column to the index to make it covering.

### Bitmap Index Scan / Bitmap Heap Scan

Two-phase: build a bitmap of matching pages, then read those pages in order.
Good for a match set that is a moderate fraction of a large table.

- `Recheck Cond:` → the bitmap could not store exact row locations (too many),
  so rows are re-checked against the heap. High `Recheck Cond` + high
  `Heap Blocks: lossy=` means it spilled: raise `work_mem`.
- `exact=… lossy=…` — any nonzero `lossy` count means spilling.

### Nested Loop

```
Nested Loop  (actual time=0.040..152.300 rows=5000 loops=1)
  ->  Index Scan using orders_pkey on orders (...)
        (actual time=0.010..0.020 rows=1 loops=5000)
  ->  Index Scan using items_order_idx on items (...)
        (actual time=0.028..0.029 rows=1 loops=5000)
```

- `loops` on the **inner** side is the danger. Inner side cheap *and* few rows
  per outer row is the good case; the bad case is a non-trivial inner cost times
  a large loop count.
- Sometimes unavoidable (no useful index on the inner side), sometimes a
  missing index, sometimes literally an N+1 in the application that got pushed
  into SQL. Look at the application before adding a speculative index.

### Hash Join

```
Hash Join  (actual time=1200.000..4800.000 rows=200000 loops=1)
  Hash Cond: (orders.customer_id = customers.id)
  Hash Buckets: 262144  Batches: 4  Memory Usage: 25MB
```

- `Batches: 1` → in memory, ideal.
- `Batches > 1` → spilled to disk. Raise `work_mem` for the session, or reduce
  the input.
- Uses `work_mem` for the build side, so it competes with concurrent queries.

### Merge Join

```
Merge Join  (actual time=300.000..310.000 rows=1000 loops=1)
  Merge Cond: (users.created_at = events.created_at)
```

Inputs arrive pre-sorted, so it streams. Usually the plan you want for large
sorted joins, and the one an `ORDER BY`-matching index enables.

### Sort

```
Sort  (Method: quicksort  Memory: 25MB)
      (actual time=1810.000..1812.000 rows=200000 loops=1)
  Sort Key: orders.created_at
  Sort Method: external merge  Disk: 480MB
```

- `Memory: 25MB` under the line → in memory.
- `Sort Method: external merge  Disk: …` → spilled. Raise `work_mem`.
- A sort directly above a small `Limit` is avoidable: an index matching
  `ORDER BY` gives an index scan that stops early.

### Aggregate / GroupAggregate / HashAggregate

```
HashAggregate  (actual time=200.000..280.000 rows=500 loops=1)
  Group Key: customer_id
  Batches: 1  Memory Usage: 30MB
```

`GroupAggregate` requires sorted input (it rides on a `Sort` or index order);
`HashAggregate` does not but needs memory. Very high `Batches` on the hash side
means spilling.

### Subquery Scan / CTE Scan

A node wrapped in `Subquery Scan` usually means the planner could not push the
qual down into the subquery. Often a `NOT EXISTS` that should be an anti-join.
In Postgres 12+, `NOT EXISTS` with an indexed correlated column normally becomes
an anti-join automatically — if it did not, the index is likely missing or the
correlation is obscured by a function call.

### Gather / Parallel

```
Gather  (actual time=0.500..980.000 rows=200000 loops=1)
  Workers Planned: 4  Workers Launched: 4
  ->  Parallel Seq Scan on events (...)
```

- `Workers Launched: 0` → the planner wanted parallelism but could not get
  workers, usually because `max_parallel_workers_per_gather` is 0, the pool is
  exhausted, or the table is too small to be worth splitting.
- If a parallel plan is chosen but the table is small, per-worker setup cost can
  exceed the benefit.
- `Gather Merge` appears when sorted output is required.

## Buffers

`EXPLAIN (BUFFERS)` adds the honest I/O picture:

```
Buffers: shared hit=12 read=3048 dirtied=0
```

- `hit` → served from `shared_buffers` (RAM).
- `read` → actually read from disk/OS cache.
- `dirtied` → pages written back. A high number means the query is modifying a
  lot.
- A plan with high `total actual time` and high `hit` is **CPU-bound**, not I/O
  bound. Adding disk or RAM will not help; reduce work or parallelism will.
- A plan with high `read` is genuinely I/O-bound. Fewer, larger reads (indexes,
  batching) help; a faster CPU does not.

Comparing `hit`/`read` **before and after** a fix is the most reliable
measurement — more reliable than the reported time, which varies run to run.

## Settings that change plans

The `SETTINGS` output lists only non-default settings. When hunting a plan that
"worked yesterday", check this line first — someone set a session GUC, a role
GUC, or the plan was cached under different settings.

Settings most often responsible for surprising plans:

| Setting | Effect when misconfigured |
|---|---|
| `work_mem` | Low → disk spills everywhere. High globally → OOM risk. |
| `random_page_cost` | Set for spinning media on SSD storage → planner distrusts indexes and picks seq scans. |
| `effective_cache_size` | Tells the planner how much is cached. Too low → underestimates index usefulness. |
| `enable_seqscan` | `off` forces index scans and can be catastrophically worse. |
| `default_statistics_target` | Low → bad estimates on skewed columns. |
| `max_parallel_workers_per_gather` | `0` disables parallel plans. |
| `plan_cache_mode` | `auto` may pick a generic plan that is bad for a skewed parameter. |

`random_page_cost` is the classic: an SSD-backed server with the default `4.0`
looks like spinning rust to the planner.

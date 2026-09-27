---
name: nosql-data-modeling
description: Model data for document, key-value and wide-column stores - embed versus reference, document size limits and the fan-out trap, partition key selection, hot partitions, single-table DynamoDB layouts, and the real anti-patterns. Use when someone is choosing MongoDB, DynamoDB, Cassandra or Redis for a relational problem, when a document grew too big, when one shard is hot, when reads became N+1 against MongoDB, or when designing an access-pattern-first schema. Triggers on "NoSQL", "document model", "embed vs reference", "partition key", "hot partition", "16MB", "MongoDB", "DynamoDB", "Cassandra", "Cassandra modeling".
compatibility: Covers MongoDB 6+, Amazon DynamoDB, Cassandra 4+, and Redis 7+; check the docs for product-specific limits.
metadata:
  version: "1.0"
---

# NoSQL Data Modelling

Access patterns decide the schema, not the entities. The output is a set of
tables/collections and the queries each one serves, written down before any
schema is created.

Read `references/fan-out.md` when an unbounded child collection is involved —
it has the arithmetic worked through for orders → line items.

## First: should this be NoSQL at all?

Pick a document store when at least two are true:

- Reads are key-by-id or by a single high-cardinality attribute.
- A document is written and read as a unit — you always want all of it.
- Write volume is high and grows faster than read complexity.
- You need horizontal write scaling more than multi-table joins and
  secondary indexes.
- The working set can be denormalised, or the extra I/O of denormalising is
  cheaper than the join.

Stay relational when you need arbitrary ad-hoc filters across many columns,
strong multi-row constraints, or the data is inherently relational and small.
MongoDB is not a faster Postgres; it is a different set of trade-offs, and
moving a normalized relational schema into documents without changing the
access patterns buys nothing.

MongoDB's aggregation pipeline and joins (`$lookup`) are real, but a query that
leans on them is a relational query wearing a disguise. Redis is a cache and a
data structure server; it is not a system of record for anything you cannot
rebuild.

## Embed or reference

**Embed** when the child is:

- Bounded (a hard upper limit you can enforce: at most 20 tags, at most 5
  addresses).
- Read together with the parent, always.
- Not queried independently or not shared.
- Small relative to the parent.

**Reference** when the child is:

- Unbounded, or grows without limit.
- Queried independently, sorted, filtered, or joined.
- Shared by many parents.
- Large relative to the parent (images, files, messages, events).

A one-to-many that is genuinely unbounded — a user with 200k orders, a post
with 50k comments — cannot be embedded. This is the most common modelling
mistake in MongoDB, and it fails slowly, at 14MB, in production.

## The size limits you must design around

| Store | Limit | What it means |
|---|---|---|
| MongoDB | 16 MB per document (`BSONObjectTooLarge`) | Hard cap, no partial updates possible |
| DynamoDB | 400 KB per item | Hard cap; partition total is 10 MB enforced for transactional writes |
| Cassandra | ~2 MB per partition value (`InvalidRequest: Partition too big`) | Per-partition value, not per row |
| Redis | 512 MB per string (older) | A big blob blocks the event loop |
| SQLite | ~1 GB default, 1e9 rows | Not a document store, but the "one big table" trap is the same |

When an embedded array approaches 1000 elements, the BSON traversal cost,
the page fragmentation, and the write amplification (a single `$push` rewrites
the whole document and the index entries) all get worse together. The number
1000 is a smell, not a limit.

Read `references/fan-out.md` for the worked example of moving 200k line items
out of an order document without a big-bang migration.

## The fan-out problem, stated precisely

Anything stored inside a document cannot be indexed, filtered, or joined
independently. Asking for "all orders containing product X" over an embedded
`items[]` array means a full collection scan, no index, no selectivity. The
moment an embedded field needs a query, that field is a reference.

Bucket the collection that *is* the problem. Unbounded child data is stored in
its own collection, keyed by parent, with a hard window per document (time
ranged or count-capped), so each document stays small and each write touches
one small document instead of rewriting a 15MB blob.

## Access patterns first

Write the queries down first, as a table. Every access pattern that has no
key is a schema you have to add later, and adding it to a document store means
changing the primary key structure.

| # | Query | Frequency | Key it needs | Table/collection |
|---|---|---|---|---|
| 1 | Get order by id | high | `PK = ORDER#<id>` | orders |
| 2 | List a user's last 50 orders | high | `PK = USER#<id>, SK = ORDER#<ts>#<id>` | user_orders |
| 3 | Find orders containing SKU 42 | low | `GSI1PK = SKU#42` | orders (GSI) |
| 4 | Aggregate revenue by month | batch | none — warehouse | separate |

Then design keys so those four lookups are all `GetItem`/`find` on a known
key. If a pattern needs a scan, either add an index (DynamoDB GSI, Cassandra
secondary index, Mongo index on the referenced field) or admit the scan is
acceptable at that frequency.

## DynamoDB single-table layout

One table, composite string keys, access patterns expressed in the key names.

```
PK                              SK                        Entity
ORDER#o-123                     ORDER#o-123              order header
ORDER#o-123                     ITEM#i-1#sku-99          order line
USER#u-42                       ORDER#2024-03-01#o-123   user's order index
SKU#sku-99                      ORDER#o-123              reverse lookup
```

- Keys are **strings only**, max 2048 bytes; the `#` separator is a convention,
  not syntax. `GSI1PK`/`GSI1SK` are the names of the single secondary index
  DynamoDB gives you free-ish; every one of them is eventually consistent by
  default unless you request a strongly consistent `Query` on it.
- Read with `Query` on a partition, never `Scan`. A `Scan` of a 100M-item
  table reads 100MB per page and will throttle.
- `ProjectionExpression` to avoid fetching item bodies you don't need; fetch
  large item attributes selectively.
- An item may be up to 400 KB, and the whole partition for a transactional
  write is limited to 10 MB.

## Partition keys and hot partitions

The single most consequential decision. A hot partition is one key absorbing
far more than its share of traffic: a single hottest tenant, a `customer_id`
where one customer is 10% of writes, a monotonically increasing key where all
new writes land on one partition.

Symptoms and fixes:

| Symptom | Cause | Fix |
|---|---|---|
| Throughput plateaus while the table is empty | One key, one partition, one hot key | Add a hash-sharded sub-key: `CUSTOMER#<id>#<shard 0-9>` and query all 10 in parallel |
| `ProvisionedThroughputExceededException` on a table far under its capacity | Hot partition + a burst | Spread across partitions; the fix is layout, not capacity |
| One shard is 10x the CPU of the others | Same as above, visible in Cassandra `nodetool status` | Compound partition key: `PRIMARY KEY ((tenant, bucket), ts)` |
| Sequential IDs | Time-based partitioning concentrates writes | Random/hash key, or a bucket prefix |

A hot partition can never be fixed by raising provisioned throughput, because
the limit is per partition, not per table. Key design is the fix.

## MongoDB specifics

- Embed a bounded array; a large array of sub-documents is fine up to a few
  thousand elements, but index the fields you query *inside* the array if you
  ever need to, because an index on the parent is useless for `items.sku`.
- `$push` and `$set` on a large embedded array rewrite the document; this is
  the classic write-amplification problem. Prefer a separate collection for
  anything that grows.
- `useUnifiedTopology` is the default and only option in modern drivers;
  connection pooling is via the `maxPoolSize` URI option, default 100.
- A document whose largest field is a growing array is a `bucket` pattern
  candidate: group children by a fixed size or time window and store the
  bucket id, so writes append to small documents.
- Use `explain("executionStats")` to check whether a query used `IXSCAN` or
  `COLLSCAN`. A `COLLSCAN` on a large collection is the signal to add an
  index or restructure.
- Aggregation `$lookup` in a loop from the application is the relational join
  you were trying to avoid; do it once in the pipeline, or restructure the
  data.

## Schema flexibility is an illusion

"Schemaless" does not mean "no schema"; it means the schema lives only in
your application, so nothing validates it. Document stores trade a declared
schema for per-document heterogeneity, which means:

- A `price` field that is a number in one document and a string in another
  will break comparisons, indexes, and aggregations in ways nothing catches.
- Index a field and you have declared a schema for that field. An index on
  `email` now requires every document to have a compatible `email`.
- Enforce what you can at the engine (a validator in MongoDB, a
  `CHECK`-like conditional expression, a schema registry in Kafka) because
  application-only validation has holes wherever a second writer exists.

Use MongoDB's JSON-schema `validator` on collection creation, and add it
before you have ten writers with different ideas of the shape.

## Anti-patterns

- **The relational dump.** A collection per table with `_id` foreign keys and
  a `$lookup` for every join. It is slower than a relational database and has
  none of the constraints.
- **Unbounded embedded arrays.** See the fan-out reference.
- **`$where` and non-indexed `sort`** on large collections.
- **Counting on a document store.** `count` / `db.order.count()` over millions
  of documents is a full pass; maintain a counter, or query a warehouse.
- **Writing a full document to change one field** (read-modify-write of a
  document with no atomic operator). Use `$set` / `$inc` so concurrent writers
  do not lose each other.
- **Multi-document transactions as the default** in Cassandra or DynamoDB. They
  exist, they are expensive, and they were the last thing designed. Design so
  they are not needed.
- **Relying on a MongoDB transaction for a cross-collection invariant.** One
  replica set, one primary, a latency spike on every write. Use a single
  document or an application-level invariant.
- **Keying by a mutable attribute** (a `status` in the primary key) — every
  status change is a delete plus an insert, and concurrent writers will
  silently fork.

## Files

- `references/fan-out.md` — worked example: unbucketing an order document as
  the `items` array grows, with the migration path and the DynamoDB/Cassandra
  key layout for the same data.

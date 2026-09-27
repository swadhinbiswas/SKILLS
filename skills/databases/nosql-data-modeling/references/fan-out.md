# Worked example: the order document fan-out

The scenario, in full, because this is the failure that shows up in
production eight months after launch.

## Stage 1 — the schema that works on day one

```javascript
// db.orders
{
  _id: "o-123",
  customer_id: "u-42",
  status: "paid",
  created_at: ISODate("2024-03-01T10:00:00Z"),
  items: [
    { sku: "sku-99", name: "Widget", qty: 2, unit_price: 500 },
    { sku: "sku-12", name: "Gadget", qty: 1, unit_price: 1200 }
  ]
}
```

Embedded items are correct here. The order is always read whole, the customer
has two items, and nothing ever queries "which orders contain sku-12?".

## Stage 2 — the growth that breaks it

Real data after a year:

- Average order: 12 items. Fine.
- 99th percentile order: a wholesale customer with **4,000 items**.
- Largest: 210,000 items, ~9 MB of BSON.

What degrades, and why:

| Document | Approx. BSON size | Symptom |
|---|---|---|
| 12 items | ~1 KB | fine |
| 4,000 items | ~180 KB | fine for writes, awkward to page through |
| 210,000 items | ~9 MB | `$push` of one line item rewrites 9 MB per write |
| 300,000 items | ~13 MB | approaching the 16 MB cap |
| 320,000 items | ~14 MB | `BSONObjectTooLarge` on write; the order is now **unwritable** |

The 16 MB failure is not a warning. Once a document cannot accept `$push`, no
new item can be added to that order, and the order is stuck.

Secondary damage before the hard failure:

- Reading an order header for the list view pulls 180 KB per order to display
  three fields. 50 orders in a list = 9 MB of network and BSON parsing for
  data nobody looks at.
- Every write amplification: a single status update rewrites the whole
  document and all the index entries in it.
- There is no index that helps. `db.orders.createIndex({"items.sku": 1})` is
  legal and useless — it is a multikey index that would have to be rebuilt
  whenever any array element changes, and it still cannot answer "all orders
  containing sku-99" without a collection scan anyway.

## Stage 3 — the target model

Split the items into their own collection, and bucket the children so no
document grows without bound.

```
db.orders          header only: _id, customer_id, status, totals, created_at
db.order_items     { order_id, bucket, seq, sku, qty, unit_price }
```

The bucket field is the fix for "unbounded". Choose one and enforce it:

- **Count buckets**: `bucket = floor(seq / 500)`. Cap 500 items per document,
  hard, in application code. Reads: one `find` for the header plus one for
  each bucket; writes: `$push` into a 500-item document, not a 9 MB one.
- **Time buckets**: one document per order per month, for orders where the
  line count tracks time. Good for append-mostly data; bad for a single order
  that is created complete.

```
{ _id: "o-123#b-000", order_id: "o-123", bucket: 0,
  items: [ ...up to 500... ] }
```

If a query must be "all orders containing sku-99", add an index to the item
collection, or a separate small collection
`db.order_items_by_sku { sku, order_id }` written in the same application
transaction (or via a change stream). That collection is small — one document
per (order, sku) — and indexable.

## Stage 4 — the migration, without a maintenance window

Do not do this as one big update. The pattern:

1. **Deploy dual-read.** Application reads items from `order_items` if it
   exists, else from `orders.items`. Writes go to both.
2. **Backfill in batches**, keyed on the order id, one order per iteration or
   a few hundred, each batch a small write. Skip orders already bucketed.
3. **Verify** with a count comparison per order, sampled across the size
   distribution — check a 4,000-item order, a 12-item order, and the largest
   order, not just random ones.
4. **Deploy new-only reads.** The application stops reading `orders.items`.
5. **Drop `orders.items`**, in a migration, once nothing reads it. This is the
   point of no return; see `schema-migrations` for how to schedule it.

Read `references/fan-out.md` is not needed here — you are reading it.

## The same data in DynamoDB

Single table, composite keys, items as their own entity so they can be
skipped and paged:

```
PK              SK                      attrs
ORDER#o-123     ORDER#o-123             status, customer, totals
ORDER#o-123     ITEM#0001#sku-99        qty, unit_price
ORDER#o-123     ITEM#0002#sku-12        qty, unit_price
USER#u-42       ORDER#2024-03-01#o-123  (user's order list)
SKU#sku-99       ORDER#o-123            (reverse lookup, GSI1PK)
```

- "All items for an order" is a `Query` on `PK = ORDER#o-123` with
  `KeyConditionExpression = "begins_with(SK, :item)"` — 4,000 items come back
  in pages of 1 MB, and the 400 KB item limit applies to each *item*, not the
  page.
- "Orders containing sku-99" is a `Query` on the GSI with
  `GSI1PK = "SKU#sku-99"`. Eventually consistent by default; say so in the
  product requirements rather than discovering it.
- "Orders containing sku-99 AND status='paid'" is a fan-out merge across the
  GSI — or it does not have a key, and the answer is a second GSI. You get
  two GSI relationships on a DynamoDB table; plan them.

## The same data in Cassandra

Unbounded children get a partition per child, never per parent:

```sql
CREATE TABLE order_item (
  order_id  uuid,
  item_seq  int,          -- clustering column
  sku       text,
  qty       int,
  unit_price decimal,
  PRIMARY KEY ((order_id), item_seq)
);
```

- A partition's total value must stay under ~2 MB (`InvalidRequest: Partition
  too big`). For a 4,000-item order, `order_id` alone is too coarse: use
  `PRIMARY KEY ((order_id, bucket), item_seq)` with `bucket = item_seq / 500`.
- "Orders containing sku-99" needs a secondary index
  `CREATE INDEX ON order_item (sku);` — and Cassandra will warn that a query
  on a non-key column may be a full scan. It is a materialized-view problem
  in disguise, and the honest answer is usually a `orders_by_sku` table
  written by the application.
- `SELECT * FROM order_item WHERE order_id = ?` with no clustering bound
  returns *all* items and can hit the partition size limit. Always bound the
  clustering range.

# The transactional outbox

Read this when a failure between "the database committed" and "the event was
delivered" would lose an event — which is every webhook, every
publish-to-a-broker, and every cross-service notification.

## The problem it solves

Two writes, one transaction each, no atomicity between them:

```
1. BEGIN; UPDATE orders SET status='paid'; COMMIT   -- succeeded
2. broker.publish("order.paid", ...)                 -- process died here
```

The order is paid and the event does not exist. The reverse order is worse: the
event exists for an order that was never updated, and a consumer will act on
data that does not exist. Neither can be fixed by a retry in the caller, because
the caller may be gone.

## The pattern

Write the event to a table **in the same transaction as the state change**, then
have a separate process deliver from the table and mark it done.

```sql
CREATE TABLE outbox (
  id           bigserial PRIMARY KEY,
  topic        text        NOT NULL,
  aggregate    text        NOT NULL,          -- "order:01J8Z..."
  event_id     uuid        NOT NULL UNIQUE,   -- consumer dedupe key
  payload      jsonb       NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  delivered_at timestamptz,                   -- NULL = not yet sent
  attempts     int         NOT NULL DEFAULT 0,
  next_attempt timestamptz NOT NULL DEFAULT now(),
  last_error   text
);
CREATE INDEX ON outbox (next_attempt) WHERE delivered_at IS NULL;
```

```python
def mark_paid(order_id: str, payment_id: str) -> None:
    with db.transaction() as tx:
        tx.execute(
            "UPDATE orders SET status='paid', paid_at=now() WHERE id=%s", [order_id]
        )
        tx.execute(
            "INSERT INTO outbox (topic, aggregate, event_id, payload) "
            "VALUES ('order.paid', %s, gen_random_uuid(), %s)",
            [f"order:{order_id}", json.dumps(
                {"order_id": order_id, "payment_id": payment_id,
                 "version": tx.scalar(
                     "SELECT version FROM orders WHERE id=%s FOR UPDATE",
                     [order_id])})],
        )
    # Committed: state change and event are now atomic.
```

```sql
-- Delivery worker. Claim a batch, send, mark. FOR UPDATE SKIP LOCKED lets
-- several workers run without processing the same row twice.
WITH batch AS (
  SELECT id FROM outbox
  WHERE delivered_at IS NULL AND next_attempt <= now()
  ORDER BY id
  FOR UPDATE SKIP LOCKED
  LIMIT 100
)
UPDATE outbox o SET attempts = o.attempts + 1
FROM batch WHERE o.id = batch.id
RETURNING o.*;
```

Send each claimed event, then set `delivered_at = now()`. On failure, set
`next_attempt = now() + backoff(attempts)` and `last_error`. After N attempts,
leave the row with `delivered_at IS NULL` and alert — do not delete it. A
dead-letter row you can inspect and replay is worth more than a clean table.

## Why each piece is there

- **`event_id` is a UUID generated once, reused across every retry.** The
  consumer dedupes on it, which is what makes at-least-once delivery safe.
  Generating a new id per attempt would break idempotency downstream.
- **`aggregate` + a per-aggregate version** lets the consumer drop stale and
  out-of-order events (`version` in the payload, not just the row).
- **Partial index on `next_attempt WHERE delivered_at IS NULL`** keeps the
  worker's scan proportional to the backlog, not to the table's lifetime size.
  Without it, a busy table is slow forever even when the backlog is empty.
- **`FOR UPDATE SKIP LOCKED`** is what makes the worker horizontally scalable
  and safe to restart. Without it, two workers double-send (fine, the consumer
  dedupes) or block each other (not fine).
- **Backoff on the row, not in the worker loop.** A worker sleeping 30s for one
  failed event stalls the rest.

## Per-aggregate ordering

The table is ordered by `id`, which is *global* insertion order, not per-entity
order. If two events for the same aggregate must be applied in order, either:

- Deliver with a single worker while the backlog is small, or
- Include a per-aggregate `version` in the payload and make the **consumer**
  idempotent and order-tolerant (drop anything not newer than the last applied
  version). The second is the one that scales; it is also a prerequisite for
  the consumer's dedupe table.

## When not to bother

The outbox costs a table, a worker, a poll interval, and a backlog you have to
monitor. It is worth it when the event triggers something with a real business
consequence (a payment, a shipment, a notification someone will notice is
missing). For internal cache invalidation, in-process function calls, or
metrics, publish directly and accept the occasional loss.

## Monitoring

These four, or you will find out from a customer:

- **Oldest undelivered row age.** The single number that tells you the outbox
  is stuck. Alert on it, not on row count.
- **Delivery latency** (now() - created_at, p99).
- **Attempt count** on rows older than a minute — a growing tail means the
  destination is down and you are about to hit a limit.
- **Backlog size**, with the growth rate.

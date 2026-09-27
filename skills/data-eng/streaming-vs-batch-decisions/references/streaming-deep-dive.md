# Streaming deep dive

Detail behind the decisions in SKILL.md: broker mechanics, offset/lag
diagnosis, exactly-once limits, and windowing semantics with concrete cases.

## Broker comparison (where they differ from Kafka)

| Concept | Kafka | Google Pub/Sub | SQS |
|---|---|---|---|
| Unit of ordering | partition (log) | **ordering key** (per-key ordering opt-in) | FIFO queue (whole-queue ordering) or standard |
| Parallelism unit | partition count | subscriber count / max concurrent | worker concurrency (standard) |
| Consumer position | offset (committed per group) | ack deadline + ack | visibility timeout + delete |
| Delivery | at-least-once (with EOS for Kafka-to-Kafka) | at-least-once | at-least-once |
| Ordering guarantee | per-partition, in-order | per ordering key, opt-in, best-effort | FIFO queues: in-order per queue |
| Replay | seek to offset / new consumer group | re-publish (snapshot to a new topic) | messages deleted on ack; no replay (use DLQ) |

Pub/Sub is operationally simpler (managed, no partitions to size) and the
right default for many GCP workloads; Kafka wins on replay, per-key ordering
scale, and ecosystem. SQS is the simplest queue but has no native replay and
weak ordering — fine for task queues, wrong for ordered event logs. Pick per
the ordering/replay need, not by brand.

## Offset and lag: how to diagnose a consumer

Kafka:

```bash
kafka-consumer-groups.sh --bootstrap-host localhost:9092 \
  --describe --group my-group
# GROUP TOPIC PARTITION CURRENT-OFFSET LOG-END-OFFSET LAG CONSUMER-ID
# my-group  events  0         1000            1500        500  consumer-1
```

- **LAG = log-end-offset − current-offset** = messages not yet processed for
  that partition. The sum over partitions is total consumer lag.
- **Growing lag** = falling behind (throughput < production rate). Fix:
  more consumers (up to partition count), faster processing, smaller
  batches, or scale the producer down. If already at partition count,
  throughput is the problem — profile the processing.
- **Flat high lag** = caught up as fast as it produces, but a backlog exists.
  Let it drain; if it never drains, throughput < rate.
- **A single partition hot** (lag on one partition only) = a **skewed
  partition key** (one key gets huge volume). Fix the key.
- **Lag jumping to 0 then re-reading** = commits are not happening or
  `max.poll.interval.ms` is being exceeded and the consumer keeps getting
  kicked (rebalance loop). Check for long processing per batch.

Pub/Sub equivalent: the subscription's **unacked message count / oldest
unacked age**. A growing backlog → same fixes (more subscriber instances,
faster handling, or a partition-key problem in the publisher).

## Exactly-once: what is actually guaranteed

**Kafka exactly-once (EOS):** with `enable.idempotence=true` on the producer
and Kafka transactions, a **consume → transform → produce** loop, all in one
transaction, is exactly-once *for the output topic*. The input offset and the
output writes commit atomically. This is genuinely useful for a
change-data-capture → normalized-stream pipeline.

**What EOS does NOT cover:**

- **Writes to an external system** (Postgres, an API, S3 in another account).
  The Kafka transaction commits the offset; the external write is not in it.
  Result: at-least-once into the external sink, so the sink must dedupe
  (upsert on the deterministic key) or you get duplicates.
- **Side effects** (emails, webhooks, payments) — same problem; needs an
  outbox or an idempotent target.
- **Code paths outside the transaction**, and non-idempotent transforms that
  depend on wall-clock or random state.

So the practical architecture: **at-least-once delivery + idempotent sinks.**
Design every sink (DB table, search index, downstream topic) to be safely
re-appliable. Concretely, for "consume Kafka → upsert Postgres":

```sql
INSERT INTO projections (event_id, customer_id, value, updated_at)
VALUES ($1, $2, $3, now())
ON CONFLICT (event_id) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;
```

Commit the offset **after** the upsert. A crash before commit replays the
message; the upsert is idempotent, so the replay is harmless.

## Watermarks and windowing, concretely

### Tumbling window, event time, with a late-data policy

Scenario: count orders per 5-minute window. Events can arrive slightly out of
order and a few can be late.

- Define windows in **event time** (`order.timestamp`), not processing time.
- A **watermark** = "I will not see event-time data older than (max event
  time seen − allowed lateness)". Set allowed lateness to cover your p99
  lateness (e.g. 10 minutes).
- A window's result is **final** once the watermark passes window-end +
  allowed lateness. Before that, results are **speculative** and may be
  revised.
- An event arriving after its window closed is **late**: apply a policy —
  drop (and count/log it), or update the window within a longer "allowed
  lateness", or send to a correction/upsert path.

A worked late event: order A at `12:03` arrives at wall-clock `12:20`; the
`12:00–12:05` window's watermark passed at `12:15` (5-min window + 10-min
lateness). It is late → policy applies. If the policy is "drop", it's
excluded (and logged); if "allowed lateness 2h", the window is recomputed and
emitted as a correction. Pick one per pipeline and document it.

### Sliding vs session, by question asked

- "Orders per minute" → tumbling.
- "Rolling 1-hour count, updated every minute" → sliding (1h window, 1m step).
- "How long/did a user stay, and what did they do in a session" → session
  windows (gap-bounded, e.g. 30-min inactivity ends a session). Session
  windows need a gap threshold and can be unbounded in pathological cases
  (a continuous stream never ends a session) — cap total session length.

### State and scaling

- Windowed/stateful aggregation holds **per-key state**; total state =
  (keys) × (avg state per key). Millions of keys with long windows = large
  state, needs a state store and sharding, and slower checkpoints.
- Bound state: short windows, session gaps, and TTLs on dedupe/state keys.
- **Checkpoint size/time** is a real operational cost for stateful streams;
  a growing checkpoint that slows restarts is a scaling smell.

## Stream processing engines, briefly

| Engine | Fits |
|---|---|
| **Kafka Streams / ksqlDB** | Light transformations on Kafka topics, low-ops; ksqlDB for SQL-ish stream processing |
| **Flink** | Complex, high-throughput, exactly-once, advanced windows/state; steepest learning curve |
| **Spark structured streaming** | Batch familiarity + streaming; micro-batch model; good for SQL over streams |
| **Pub/Sub + Dataflow / Cloud Run** | GCP-native; Dataflow = Apache Beam model |
| **Consumer app + warehouse (Lambda/ECS)** | Stream into durable storage, compute in batch — "stream for capture, batch for compute" |

You do not need Flink. If your stream is "ingest events and land them in a
table/lake," a consumer writing to storage plus batch compute is simpler and
just as correct. Reach for a real stream engine when you need *stateful,
low-latency aggregation* that batch cannot do within your latency budget.

## Testing streams

- **Use a fixed input topic/partition with known event-time ordering**,
  including out-of-order and late events, and assert the final windowed
  result. A stream correct only on in-order, on-time data is not correct.
- **Test replay**: process the same input twice (reset offsets) and assert the
  sink is identical (idempotency).
- **Test crash-mid-stream**: kill mid-run, restart from checkpoint, assert no
  loss and no duplicates in the sink.

## Decision tree

```
Latency requirement stated as a number?
├─ No / "whenever" / hours-minutes  → BATCH (or short-interval batch)
└─ Yes
   ├─ > 1 min needed?               → BATCH on a short interval (micro-batch)
   └─ Sub-second needed
      ├─ Is the source push-only / high-rate telemetry?  → STREAM
      ├─ Is it a full-history reconciliation/aggregate?  → stream to storage, BATCH for the truth
      └─ Team has stream ops capacity (partitions, offsets, monitoring, 24/7)?
         ├─ No  → stream-capture + batch-compute (hybrid), or accept a worse latency
         └─ Yes → STREAM; design sink for idempotency, window in event time
```

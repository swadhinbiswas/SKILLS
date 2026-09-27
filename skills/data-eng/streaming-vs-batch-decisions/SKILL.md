---
name: streaming-vs-batch-decisions
description: Decide per workload whether to run batch or streaming, and do streaming correctly - latency vs cost vs complexity, Kafka/Pub-Sub partition and ordering guarantees, consumer groups and offset management, the limits of exactly-once, and event-time windowing. Use when choosing an architecture, when a stream job reprocesses endlessly, when ordering is wrong, when a consumer group lags, or when someone proposes "real-time" for a report. Triggers on "real-time", "streaming vs batch", "Kafka", "partition", "consumer group", "offset", "exactly-once", "windowing", "watermark", "late data", "Pub/Sub".
compatibility: Kafka semantics described are for Apache Kafka 3.x; Google Pub/Sub and SQS differ in the details called out. Spark structured streaming uses Kafka source/sink semantics. Verify flags per version.
metadata:
  version: "1.0"
---

# Streaming vs Batch Decisions

The default answer is **batch**. Streaming earns its cost only when the
*latency requirement* genuinely demands it and the *operational complexity* is
affordable. "Real-time" is a latency budget, not a virtue.

## The decision, per workload

Choose per data flow, not per company. Ask these in order:

1. **What is the latency requirement?** Minutes/hours → batch. Sub-second →
   streaming. "When the user looks" (a dashboard they poll) → usually batch is
   fine. If no one can name a number, it's batch.
2. **What is the cost/complexity of streaming for this flow?** Streams need
   partitions, offset/state management, exactly-once reasoning, schema
   evolution on the topic, and 24/7 monitoring. Batch is a cron job you can
   reason about. On a small team, that overhead dominates.
3. **Is the data naturally incremental and continuous?** A clickstream,
   telemetry, a log, a market feed, a game event stream → streaming fits. A
   nightly report over a relational DB, a monthly billing rollup → batch,
   always.
4. **Does the consumer need the full history to be correct?** A windowed
   aggregate or a reconciliation job usually needs completeness → batch
   (or a stream-to-lake step that batch reads). See `data-pipeline-reliability`
   in this repo for late data and watermarks.
5. **Is the source push or pull?** If the source can be polled and its
   lateness budget is minutes, a batch read is simpler and safer. Streaming
   only helps when you genuinely need sub-minute freshness or the source only
   emits.

**Default: batch. Go streaming when (a) you can state a sub-minute latency
requirement, and (b) the flow's complexity budget covers it.** Frequently the
right answer is hybrid: stream into durable storage (cheap, append-only), and
run batch/warehouse jobs on top for the reporting — "stream for capture,
batch for compute."

| Need | Choice |
|---|---|
| Nightly/monthly reports, reconciliation, finance | Batch |
| Sub-second user-facing reaction (fraud, recommendations) | Streaming |
| "Live" dashboard refreshed every few minutes | Batch on a short interval (or micro-batch) |
| Event log / audit trail capture | Stream (or batch append) — immutability matters |
| CDC from a database | Streaming (Debezium) or batch snapshot+increment — pick by latency need |
| ML feature pipelines needing fresh features | Streaming for the hot features, batch for training |

Micro-batch is the underrated middle: poll every few seconds/minutes, process
what arrived. It gets most of the freshness with batch's operational
simplicity, and it's the right answer for "near-real-time" more often than
people admit.

## Kafka/Pub-Sub core model (the parts that bite)

### Partitions and ordering

- A topic is split into **partitions**; each partition is an **ordered**
  append-only log. **Ordering is guaranteed only within a partition**, and
  only for a *single* producer writing in order.
- **The partition key decides the ordering scope.** Messages with the same
  key go to the same partition and stay in relative order. No key (or a
  round-robin key) → no ordering, messages spread across partitions.
- **To order a workflow, key by the entity ID** (e.g. `order_id`): all events
  for one order are ordered. You *cannot* order across orders without
  funnelling everything to one partition (a throughput ceiling).
- **Partitions are the unit of parallelism** and (in Kafka) of consumer
  scaling. A consumer group can have at most as many active consumers as
  partitions on the subscribed topic. More consumers than partitions = idle
  consumers. Partitions cannot be decreased easily in Kafka; **choose the
  count deliberately** (it caps your future parallelism).
- **Rebalancing:** when consumers join/leave a group, partitions are
  reassigned. A long rebalance stalls consumption. Tune `session.timeout.ms`
  / `max.poll.interval.ms` for long processing.

### Consumer groups and offsets

- A **consumer group** shares the partitions of a topic: each partition goes
  to exactly one consumer in the group. Different groups are independent
  (each gets all the data — this is how you fan out to separate consumers).
- **The offset is the group's position** in the log, committed either
  automatically (periodically) or manually. On restart, the group resumes
  from the committed offset — so a crash **replays from the last commit**.
- **Commit-after-process, not before.** If you commit the offset before your
  side effect is durable, a crash *loses* data (offset moved past unprocessed
  messages). If you never commit, you *replay* (at-least-once). Prefer
  process-then-commit: do the work idempotently, then commit. This is the
  heart of reliable streaming.
- **Long processing vs `max.poll.interval.ms`:** if processing a batch takes
  longer than the max poll interval, the broker thinks the consumer is dead
  and rebalances it away (messages reprocessed, work duplicated). Either
  process small batches, pause the partitions while working, or raise the
  interval.
- **Lagging consumer:** `kafka-consumer-groups.sh --describe --group X`
  shows per-partition LAG. A growing lag means the consumer can't keep up —
  scale consumers (up to partition count), or the job is too slow, or it's
  stuck. In Pub/Sub, the equivalent is unacked message count / backlog.

### Exactly-once: the real limits

- Kafka's **exactly-once semantics** (idempotent producer + transactions) give
  exactly-once **reads and writes within Kafka** (a consume-transform-produce
  loop, all in one transaction). This is real and useful.
- It does **not** extend to an **external system** (a database, an HTTP
  call). "Read Kafka, write Postgres" is at-least-once unless you dedupe or
  use the consumer's transactions + a dedupe key in the sink.
- It does not cover side effects like sending an email.
- **Therefore:** design sinks to be **idempotent** (upsert on a deterministic
  key) and accept at-least-once delivery. This mirrors batch — see
  `data-pipeline-reliability`.
- **Replays are a feature, not a bug.** Being able to reset offsets and
  rebuild a downstream table is a recovery tool; plan for it (keep your
  processing idempotent so a replay is safe).

## Windowing: the core of stream aggregation

Aggregates are computed over **windows** of time. The two clocks that matter:

- **Event time**: when the event *happened* (a field in the message).
- **Processing time**: when the stream *sees* it.

Windows are almost always defined in **event time** (so the result is correct
regardless of arrival order), which requires handling **late data** via a
**watermark** — the system's assertion that it will not see event-time data
older than `W` (with bounded lateness). Anything arriving after its window's
watermark passes is **late** and needs a policy (drop, update an allowed-late
window, or route to a correction path).

Window types:

| Type | What it does | Use for |
|---|---|---|
| **Tumbling** | Non-overlapping fixed windows (e.g. per minute/hour) | Counts/rates per interval; the default |
| **Sliding** | Overlapping fixed size, stepping (e.g. 1h window every 5 min) | Smoothed trends, "last N minutes" |
| **Session** | Activity-based, gap-bounded (a session ends after `gap` of inactivity) | User sessions, click-through attribution |
| **Hopping** | Sliding with a small step | "Rolling 7-day" in daily steps |

Decisions that matter:

- **Trigger/emit policy** (streaming): when does a window's result become
  final? On watermark (final), on processing time (speculative — may change),
  or both (emit speculative, then correct). Choose a bounded **allowed
  lateness** — how long after the watermark to still update a window.
- **Unwindowed is valid**: stateful per-key accumulation (running totals,
  dedupe-by-key) is just an ever-growing window; bound it (session windows,
  TTL) or state grows without limit.
- **State is the scaling cost**: windowed aggregation keeps per-key state.
  Large state (many keys, long windows) needs state stores and, eventually,
  sharding. Keep windows short and keys bounded where you can.
- **Test with out-of-order and late events**: a windowed job that is correct
  only on in-order data is not correct.

## Spark structured streaming notes (common engine)

- Model as an **unbounded table**; the engine keeps state and re-runs
  incrementally as new data arrives.
- **Checkpointing** = the stream's progress (offsets + state) persisted so it
  can resume. Location must be durable and **exactly-once consistent**: write
  outputs and checkpoint to the same reliable location, and enable
  `spark.sql.streaming.multipleWatermarkPolicy` awareness if you combine
  queries. A lost checkpoint replays.
- **Watermarks** in streaming DataFrames: `withWatermark("ts", "10 minutes")`
  defines event time and allowed lateness. Data older than the watermark is
  dropped — an explicit, configurable lateness policy.
- **Trigger (`processingTime`, `once`):** how often to run the incremental
  computation. `once` = one batch then stop (good for backfill/testing);
  continuous = micro-batches.
- Output sinks: **idempotent sinks** (upsert/merge by key) make replays safe;
  a plain append sink duplicates on replay.

## Gotchas

- **A keyless (round-robin) producer silently destroys the ordering the
  consumer depends on,** and the "out-of-order" bug appears days later as a
  rare wrong total. `key = null` in librdkafka or the Confluent clients means
  sticky-partition assignment, not a stable entity key — always set
  `key = order_id` (bytes/str) in the producer and verify the key with
  `kafka-console-consumer --property print.key=true` before debugging the
  consumer.
- **An idempotent producer deduplicates within a session, not across
  restarts.** `enable.idempotence=true` is a producer-level, per-session
  guarantee: if the producer process restarts and reprocesses the same input,
  the broker cannot know. `init_transactions` plus a transactional-id per
  replica is what fences a zombie producer; without it, two live producers with
  the same transactional id cause the second to fail with
  `INVALID_TXN_STATE`/`CONCURRENT_TRANSACTIONS`.
- **`auto.offset.reset` only applies when there is no committed offset.** Once
  a group has committed, `earliest` is ignored and a new consumer resumes at
  the commit — so a replay "from the beginning" needs a *new* group id or an
  explicit `seekToBeginning`, not a config change. Same trap with
  `enable.auto.commit=true`: it commits in the background, so a crash replays
  from an offset that was never actually processed. Turn it off and commit
  after the sink write.
- **Out-of-order events with no `max.in.flight.requests.per.connection=1` (or
  idempotence enabled) get reordered inside the producer's own retry queue.**
  A transient broker error on message 2 lets message 3 overtake it, and the
  partition is permanently out of order even though ordering is "per
  partition". This is why idempotence matters even when you think your producer
  is single-threaded.
- **Watermarks are idle-source-driven, so a quiet stream never advances
  them.** In Flink a watermark only advances when a source event arrives; a
  job that sees no traffic holds its last window open indefinitely and emits
  nothing, which looks like a hung job, not a quiet one. Set
  `pipeline.watermark-idle-timeout` (Flink) / `withWatermark` idleness handling
  (Spark Structured Streaming) so an idle partition stops blocking the
  watermark.
- **Spark Structured Streaming silently drops everything older than the
  watermark.** `withWatermark("ts", "10 minutes")` means `ts` is also the
  *event-time* column: rows older than the watermark are discarded, not
  corrected, and a retry that rewinds the input replays into a job that no
  longer has that state. Configure the input as a replayable log you can
  re-read from a known offset, not a source that only holds "now".
- **`spark.sql.streaming.multipleWatermarkPolicy` defaults to
  `min` across queries sharing a state store**, so a join where one side has a
  5-minute watermark silently imposes a 5-minute wait on a side that would
  have been fine at 1 hour. Set the policy explicitly to the value you mean
  when combining queries.
- **Pub/Sub has no partition-assignment model and no per-partition ordering
  guarantee**, only best-effort ordering per `orderingKey`; a publish is
  **at-least-once**, so duplicates on the subscriber are expected, not a bug.
  Pub/Sub also has a **7-day message retention** and a **1,000 outstanding
  messages per subscriber per HTTP pull**-style delivery default — long outages
  drop messages, so "we can always replay from the topic" is false for Pub/Sub
  unless you write to BigQuery or GCS on ingest. The default subscription's
  ack deadline is 10 s and extends on each pull, so a consumer that takes
  longer gets a redelivery it must tolerate.
- **Redis Streams consumer groups are not idempotent by themselves** —
  `XREADGROUP` + `XACK` is at-least-once, and the "pending entries list" grows
  forever unless you `XAUTOCLAIM` and process it (idle entries are invisible to
  normal `XREADGROUP` and just sit there). Same design lesson as Kafka offsets,
  different commands.

## Decision checklist

- [ ] Stated the **latency requirement as a number**, and it justifies
      streaming over batch/micro-batch.
- [ ] Chosen batch where latency allows (the majority of workloads).
- [ ] For streams: chose partition **keys** to define the ordering scope
      (per entity), and sized partitions for intended parallelism.
- [ ] For streams: consumer processes before committing offsets, and sinks
      are **idempotent** (upsert on a deterministic key).
- [ ] Understood exactly-once is only within Kafka; external sinks are
      at-least-once + dedupe.
- [ ] For aggregates: windows are in **event time**, with a **watermark** and
      an explicit **allowed-lateness / late-data** policy.
- [ ] Stream state is bounded (short windows, TTLs) and the checkpoint
      location is durable.
- [ ] Chose micro-batch over true streaming if the need is "near-real-time".

## Files

- `references/streaming-deep-dive.md` — Kafka/Pub-Sub operational details,
  offset/lag diagnosis, exactly-once vs idempotent-sink examples, and a
  stream-vs-batch decision tree with more workloads.
- For idempotency, watermarks, and late-data policies in depth, see
  `data-pipeline-reliability` in this repo. For orchestrating stream + batch
  jobs, see `data-pipeline-orchestration`.

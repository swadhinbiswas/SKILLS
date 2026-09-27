---
name: system-design-interview
description: Answer a system design question under constraints - pin down requirements, estimate capacity with stated assumptions, assemble the building blocks, make trade-offs explicit, and survive the failure-and-data-model follow-ups. Use when the user says "design a system", "system design interview", "how would you build X", "scale this to N users", "architecture for a URL shortener / feed / chat / rate limiter", or is deciding what to build before writing code.
compatibility: Language-agnostic; arithmetic is platform-independent.
metadata:
  version: "1.0"
---

# System Design Interview

Produce a design that a reviewer can stress: requirements you can check your
own numbers against, an explicit bottleneck, a data model, and a failure story.
The score is in the reasoning, not in the boxes-and-arrows.

## The answer structure (use this every time)

Say this order out loud. It keeps you from designing something the requirements
did not ask for, and it puts the numbers where the interviewer can attack them.

1. **Clarify (2–3 min).** Functional requirements, scale, constraints. Write
   them down in the chat; the interviewer is checking that you ask.
2. **Estimate.** One line each: DAU, QPS, storage, bandwidth. State your
   assumptions as numbers, not adjectives.
3. **Data model and API surface.** Tables/schemas, keys, access patterns.
4. **High-level design (5 min).** The path of one request, end to end.
5. **Deep dive (10 min).** Where you think the hard part is, and where the
   interviewer pushes.
6. **Failure, scale, and trade-offs (5 min).** Single points of failure, what
   breaks first, what you would do with 10× the load.

Budget roughly 45 minutes. Spending 20 on the high-level diagram is the most
common failure mode.

## Step 1 — Clarify before you draw

Ask, and write the answers down:

- **What does the user do, in one sentence?** ("Post a URL and get a short
  one back.") If you cannot say it in one sentence, you do not understand it.
- **Read/write ratio, and which operations are hot.** A read-heavy system and a
  write-heavy system have different architectures from the start.
- **Scale, as a number.** "How many DAU? Peak QPS? Growth in 12 months?"
- **Latency target.** p99 under 100ms, or under 3s, changes whether you can
  do synchronous cross-service calls at all.
- **Data lifetime and volume per day.** Determines storage class and whether
  you need archiving.
- **Consistency requirements.** "Must a user's follower count never be wrong?"
  "Can two people see different order states?" This is the question that
  decides databases, caches, and queues.
- **Multi-region? Multi-tenant? Compliance constraints?**
- **What is explicitly out of scope?** Stating this earns more than drawing
  another box.

## Step 2 — Estimate capacity, showing the arithmetic

Do the arithmetic out loud, with the assumption, then the number. Numbers only
need to be within an order of magnitude, but the **process** is what is being
evaluated.

```text
Assume: 10M DAU, 10% log in per day, each writes 5 items.
  DAU 10M x 0.1 x 5      = 5M writes/day
  avg QPS = 5M / 86400    ≈ 58 writes/s
  peak    ≈ 10x average   ≈ 600 writes/s          (state the 10x assumption)

Storage (10 bytes/item, JSON + index overhead x2):
  5M x 10 x 2 = 100 GB/day  ≈ 36 TB/year

Reads: read:write = 100:1
  600 reads/s peak sustained, ~60k reads/s if reads scale 100x independently
```

Always sanity-check against something real: a 1 TB disk holds ~10^9 rows; a
single Postgres instance handles thousands of writes/s with the right schema;
one 16-core box does ~10^5 simple queries/s. If your number says you need
40,000 machines, recheck the assumption, not the machine count.

Storage arithmetic that gets missed: **bandwidth and egress.** 5 TB/day of new
data replicated three ways is 15 TB/day of disk writes plus 3× replication
traffic, and it never goes away.

## Step 3 — The building blocks

Know what each one is actually for, and when it is a liability.

| Block | Solves | Do not use it to |
|---|---|---|
| **Load balancer / reverse proxy** | TLS termination, health checks, request routing, one public IP | Authenticate business requests |
| **CDN / edge cache** | Static and cacheable content, absorb read traffic, cut origin load | Dynamic per-user data you cannot key by user |
| **Application server** | Business logic; stateless so you can run N | Long-lived per-user state (that is a session store) |
| **Relational DB (Postgres/MySQL)** | Transactions, joins, strong consistency, flexible queries | Unbounded write throughput on one primary; hot large scans |
| **Cache (Redis/Memcached)** | Read amplification, hot keys, rate limit counters, session store | Anything requiring durability or multi-key atomicity |
| **Queue / stream (Kafka/SQS/SQS)** | Absorb bursts, decouple producers from consumers, retry, fan-out | Synchronous work a user is waiting on |
| **Object storage (S3/GCS)** | Large blobs, backups, cold data, static assets | Anything needing random updates or queries |
| **Search index (Elasticsearch/OpenSearch)** | Full-text, faceting, fuzzy matching, sort by relevance | Authoritative data or transactional writes |
| **CDN-cached HTML** | Milliseconds for whole pages | POSTs, or anything user-specific without a cache key |
| **Sharding / partitioning** | Scale one logical dataset horizontally | Solve a slow query, a bad schema, or a missing index |

Two more that people forget: a **scheduler/cron** for anything periodic, and a
**search/analytics pipeline** for anything you cannot afford to compute on the
read path.

## Step 4 — High-level design: trace one request

Pick one operation (create a short URL; post a status; load a feed) and walk
it end to end: client → LB → app server → cache → DB → response. Then walk a
second, very different operation. This forces consistency in your own design
and it is what the interviewer is picturing when they nod.

Sketch the pieces in text (or ASCII) with the data stores labelled and arrows
labelled with what flows. Label every arrow with its protocol when it is not
obvious ("gRPC", "HTTP/2", "Kafka topic `order-events`").

State your single source of truth explicitly. Everything else is a copy, and
every copy has an invalidation story. If you cannot name the source of truth,
the design is not finished.

## Step 5 — Make trade-offs explicit

Name the alternative you rejected and why, in one clause. This is where most
interview answers are lost: a design with no stated trade-off reads as
incomplete, because the interviewer cannot tell whether you considered the
obvious alternative.

- **SQL vs NoSQL.** Default to Postgres. Reach for a document/key-value store
  when access patterns are known, simple, and huge; or when you need
  horizontal write scaling on one table. Say which reason applies.
- **Sync vs async.** Sync if the user is blocked on the answer. Async
  (202 + queue) if it takes more than a second, if it is a side effect, or if
  it must not block the request.
- **Monolith vs services.** Default monolith. Split when scaling, ownership, or
  release cadence genuinely differ — not to look modern. See
  `skills/architecture/service-boundaries-and-monoliths/SKILL.md`.
- **Strong vs eventual consistency.** Per field, per operation, and say which.
- **Read your writes / read from primary / read from replica.** If you read from
  a replica, say how stale it can be and what the user sees when it is.
- **Push vs pull (feed).** Fan-out-on-write (push) for the author, latency is
  low and write cost is high; fan-out-on-read (pull) for the reader, read cost
  is low and the read path is expensive. Most systems do a hybrid: push to
  celebrities, pull for the long tail.
- **SQL vs search index for queries.** Index is a copy; say how it is built
  and how stale it may be.

## Step 6 — Deep dive where it hurts

The interviewer will push on one of these; be ready:

- **The hot path.** Which single component, at 10× load, saturates first? Name
  it before you are asked. A good answer: "the write to the primary — it is a
  single node doing 600 writes/s with fsync on; I would batch or shard by
  `user_id`."
- **The data model.** Show keys, indexes, and the two or three queries that
  must be fast. A missing composite index is the most common real design bug.
- **The N+1 in a fan-out.** "1000 followers, one query each" — batching,
  DataLoader-style, or an async fan-out with a bulkhead.
- **The partition/shard key.** Name it, and name the query that would break
  it (cross-shard aggregate, "all users in EU ordered by name"). A shard key
  with no cross-shard query plan is a design that will not survive contact.
- **The queue.** What is in it, what is the retry policy, what happens if the
  consumer is down for an hour, and what is the dead-letter path?

## Red flags in an answer (the review lens)

These are the things that make a design wrong regardless of how good the
diagram looks. Check your own answer — or someone else's — against this list.

- **No failure discussion.** Nothing about a dependency being down, a node
  dying, a message arriving twice, a partial write. This is the single most
  common disqualifier. "What happens if this component is unavailable?" is a
  guaranteed follow-up.
- **No data model.** Boxes and arrows with no tables, keys, or index plan. You
  cannot review a design you cannot query.
- **Over-engineering.** Kafka, Cassandra, Kubernetes, 12 microservices, and a
  service mesh for 1000 users/day. Every component you add is a component that
  can fail at 3am, and it is a component the interviewer now expects you to
  defend. Justify each one against a requirement; if the answer is
  "best practice", cut it.
- **Under-engineering with a scale claim.** One Postgres primary taking 50k
  writes/s, or a single instance of anything holding state that must survive
  it, is wrong. Either fix the claim or fix the design.
- **No numbers anywhere.** "It scales" is not a design. Every capacity claim
  needs its arithmetic and its assumption.
- **A cache with no invalidation story.** "Cache the user" and then never
  say what happens on update is an answer that gets cut off mid-sentence by
  the interviewer.
- **Ignoring the read path.** Designing the write path beautifully and never
  saying how a 1000-item list is loaded. Feeds and lists are read-heavy; that
  is where the traffic is.
- **Security and abuse as an afterthought.** Rate limiting, auth, tenant
  isolation, and auditability are requirements, not a wrap-up bullet.
- **No observability.** No metrics, no dashboards, no alerting. In a real
  system, "how would you know it is broken" is part of the design.
- **Trusting a client.** Client-supplied prices, permissions, pagination
  counts, or "isAdmin" flags. The server decides.

## Output template

```markdown
## Requirements
Functional: <1-3 sentences>
Non-functional: latency target, consistency, availability, scale
Out of scope: <explicit>

## Capacity (assumptions shown)
DAU/peak QPS/steady QPS: <n> (<assumption>)
Storage: <n>/day (<n>/year)
Read:write ratio: <n:1>

## Data model
<table>: keys, indexes, and the query each index serves
Source of truth: <which store>

## High-level design
<ASCII diagram> + the path of one request, end to end

## Deep dive
<the component most likely to saturate, and what happens at 10x>

## Failure and operations
Single points of failure and their mitigations
What degrades gracefully, what does not
Metrics/alerts; retry and backoff policy; recovery procedure

## Trade-offs taken
<choice> over <alternative>, because <reason>
```

## Gotchas

- **"10× peak" is a guess and it is usually wrong.** State it as an assumption
  and note which components are linear in it (stateless app servers) and which
  are not (one primary, one shard, one queue partition).
- **Average QPS hides everything.** Design and state for peak; peaks last 10
  minutes and are when you get paged.
- **Replication is not a backup.** A replica with a dropped table replicates
  the drop. Restores are the thing to design.
- **The read replica lag is a correctness bug waiting to happen** for any
  "write then immediately read" flow. Route the read after a write to the
  primary, or accept and document the staleness.
- **A queue does not reduce work, it defers it.** A queue in front of a
  consumer that cannot keep up is a latency-and-outage generator with extra
  steps. Size for the steady-state consumer throughput, not the burst.
- **Egress costs scale with users, not with servers.** It is frequently the
  largest line item, and it is the one people forget until the bill arrives.
- **A single hot key beats a bad shard design.** One celebrity with 100M
  followers will find any unpartitioned counter. Hash the key, or push to
  followers asynchronously.
- **UUIDv4 primary keys scatter index inserts** (random, so every insert lands
  on a different B-tree page and page splits everywhere). Use UUIDv7,
  ULID, or a snowflake-style id for database primary keys; keep v4 for
  externally visible identifiers where you want unguessability.
- **Counting and ranking are the operations that break first** ("top 10",
  "follower count"). They are full scans over the whole table; precompute them.
- **Multi-region active-active is a data consistency project**, not a
  deployment project. If you cannot state the consistency model, do not claim
  it.

## Safety notes

- Treat capacity numbers as estimates with stated assumptions, and label them
  as such. Never present a made-up figure as a measurement.
- If the honest answer is "this is over-engineered for the stated scale", say
  it and propose the simpler design. That answer is rewarded, not penalised.
- When reviewing someone's design, quote the specific requirement or the
  specific missing answer for each red flag. "This looks over-engineered" is not
  actionable feedback.

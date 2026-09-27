---
name: distributed-databases
description: Operate a replicated or sharded database - leader-follower vs multi-leader, replication lag and read-your-writes, quorum reads, split-brain, CAP and PACELC, Raft/Paxos intuition, and the pain of resharding. Use when a user has replica lag, stale reads after a write, a primary failover, a split-brain or clock-skew alert, a "no quorum" error, or is deciding between a replica, a multi-region write, or a sharded cluster. Triggers on "replication lag", "read your writes", "split brain", "CAP theorem", "quorum", "Raft", "failover", "resharding", "cross-region latency".
compatibility: Behaviour described for PostgreSQL 12+ physical/logical replication, MySQL 8.0 group replication, MySQL/InnoDB Cluster, and Raft-based systems like etcd, TiKV, and CockroachDB; verify product-specific commands.
metadata:
  version: "1.0"
---

# Distributed Databases

Enough replication and consensus to operate one safely. The focus is the
operational gotchas — the part you only learn from an incident.

## Topology: pick one

| Topology | Write | Read | Use when | Cost |
|---|---|---|---|---|
| Single primary, N read replicas | Primary only | Replica is fine after a short lag | 90% of apps | Replication lag, read-your-writes workarounds |
| Multi-primary (async) | Any node | Any node | Multi-region, tolerating conflicts | Conflict resolution, divergence |
| Multi-primary (sync/quorum) | Majority of replicas | Quorum | Needs linearizable writes, accepts latency | Every commit pays a network RTT |
| Sharded | Distributed by key | Distributed | Single node cannot hold the data | Cross-shard queries, resharding, hotspot keys |

Default: **single primary + read replicas**, with a documented staleness
budget and a read-your-writes mechanism. Add shards only when a single
primary genuinely cannot hold the data or absorb the write rate; multi-primary
only when multi-region writes are a hard requirement.

## Replication lag and read-your-writes

A replica is asynchronous by default: the primary commits and returns before
the replica has the row. Every "my write disappeared" bug is this.

- **Measure lag the way your engine exposes it.** Postgres:
  `SELECT now() - pg_last_xact_replay_timestamp() AS lag;` on the replica.
  Bytes: `pg_stat_replication` `sent_lsn` vs `replay_lsn`. MySQL:
  `SHOW REPLICA STATUS\G` → `Seconds_Behind_Source` and
  `Replica_IO_Running` / `Replica_SQL_Running`. A large
  `Seconds_Behind_Source` with both threads running means a **long-running
  transaction or a large row** on the replica, not a network problem.
- **Read-your-writes options**, pick one:
  - **Route the read to the primary for a short window after a write** (sticky
    primary, or a "read-your-writes session token" that the router honours).
    Simple and correct.
  - **Wait for the replica to catch up**: read the LSN/GTID of your write and
    poll the replica until it has replayed it. Precise, one extra round trip.
  - **Wait for a timestamp**: read from the primary if the requested
    `since` is within N seconds of now, replica otherwise. Coarse but cheap.
  - **Return the written value from the write response** and skip the read
    entirely. Often the best fix — it removes the read.
- **Do not make the application retry the read on the replica in a loop** to
  "wait for it to appear". That turns a small lag into a request timeout and
  hides the real problem.

## Failover, split-brain, and the fencing problem

- **Automatic failover hides a split brain unless you fence.** If a primary
  is partitioned from the replicas, the replicas may elect a new primary
  while the old one is still accepting writes. Two writers, divergent data.
  The fix is **fencing**: a monotonically increasing epoch handed to the new
  primary, and every write path that rejects a stale epoch
  (`TermIncorrect: leadership is moving`, `StaleTerm`). Without fencing, a
  failover is a bet.
- **Sync replication protects durability, not availability.** `synchronous_commit
  = on` waits for at least one standby. If all standbys are down, commits
  block — that is the intended behaviour, and it is a *different* failure from
  data loss. `synchronous_standby_names` with more than one standby lets the
  primary wait for any N of them.
- **Raft and the single-writer rule.** A leader must hold a quorum lease to
  accept writes; if it loses the lease it steps down (`ErrLeaseLost`,
  `not the leader`). In practice this means a brief, client-visible error
  during every leader change, and it is the price of never having two leaders.
  The read path can be served by a follower only if it can prove it has not
  been fenced; otherwise it must go to the leader or use a lease/read-index
  protocol.
- **Clock skew vs lease-based consensus.** Consensus systems avoid relying on
  wall clocks, but many lease implementations do use them, and skew between
  the leader and followers produces spurious leadership changes. If you see
  repeated elections, check NTP before you suspect the network.

## Quorum reads and R = W + N − 1

A quorum system (Dynamo-style, Cassandra, Riak, Cosmos) tolerates
`N/2 + 1` failures. A read of `R` replicas that returns the **latest** value
must guarantee it read the newest write somewhere, so `R + W > N` is required;
`W + R > N` is the usual statement. With `N = 3` and `W = 2`, `R = 2` is
sufficient.

- `R = 1` on a replica is a stale read by construction; that is how "sometimes
  I see the old value" happens.
- Hinted handoffs and read repair (Cassandra) are what make a quorum read
  converge; without read repair, reading a stale replica and moving on leaves
  the divergence in place.
- **Consistency is per request**, not per database. A strong read of one key
  plus a quorum read of another is a normal, useful combination.

## CAP, used properly

- **C**onsistency (linearizability, not "eventual"), **A**vailability, **P**
  artition tolerance. During a partition you choose C **or** A. That is the
  whole theorem. It is not "pick two of three".
- **The theorem says nothing about a healthy network**, which is why it is
  almost never the deciding factor. You are choosing behaviour during the
  partition you hope never happens, and you should decide it before, not
  during.
- **PACELC** is the useful extension: if there is a **P**artition, choose
  **A**vailability or **C**onsistency; **E**lse (no partition), choose
  **L**atency or **C**onsistency. It admits the real trade-off: in the normal
  case you are trading a network round trip for strict consistency, and that
  is a choice you make on every read.
- In practice: a single-region primary + replicas is **CA-ish** (no partitions
  to speak of, and it is trivially consistent), which is why so much of the
  industry runs it. Multi-region writes are where CAP actually bites.

## Sharding basics and the pain

- **A shard is a whole database with the same schema**, split by key. Pick a
  key that is (a) present on the read path, (b) balanced, (c) not the field
  you most often need to filter *across*.
- **Reshard is the hard part.** Options, in increasing pain: add shards and
  move data (online, weeks, dual-write/dual-read), rebuild from a backup
  into the new layout (a maintenance window, often the honest choice), or
  double-write into both layouts and cut over once caught up.
- **Never resize a cluster by changing the key function in place.** Add a new
  topology, backfill, dual-write, verify counts, then switch reads.
- **Cross-shard queries** are the thing that will actually hurt: joins,
  global sorts, and `COUNT(*)` need scatter-gather across every shard and
  are O(all shards). Design the schema so those queries do not exist, or
  pre-aggregate them into a reporting store.
- **Hot shards** come from an uneven key (a big tenant, a time-bucketed
  sequence). Fix with a sub-key sharding, not with more shards on the same
  key.
- **No foreign keys across shards.** Enforce the invariant in the application
  or with a compensating delete; a FK cannot span two databases.

## Gotchas and error strings

| String / symptom | Means | Do |
|---|---|---|
| `could not serialize access due to concurrent update` on a replica | Hot-standby feedback / recovery conflict | Nothing; it retries. Confusing only if it reaches clients |
| `requested WAL segment ... has already been removed` on a standby | Replication slot missing → the primary has discarded WAL the standby still needs | Create the slot; check for dropped slots before the disk fills |
| `ERROR: replication slot "x" is active for PID ...` | A consumer died holding a slot; WAL accumulates | Terminate the stale consumer; monitor `pg_replication_slops` for `active = false` |
| Replication lag climbs steadily | A long-running query on the replica holding a snapshot, or `hot_standby_feedback` throttling WAL removal | Find the oldest `xmin` on the replica (`pg_stat_activity` where `backend_xmin` is not null) |
| `Replica_IO_Running: No` | Network/auth, not a query problem | Check connectivity, credentials, `SHOW REPLICA STATUS` error text |
| `not the leader` / `ErrNotLeader` | The connection is pinned to a node that lost leadership | Re-resolve the leader; make the client leader-aware, not a static host |
| `no quorum` / `No quorum for ...` (Cassandra) | Fewer than `N/2+1` replicas acknowledged | Fix the cluster membership, not the request |
| Reads see data that a write "deleted" | Read from a lagging replica, or a stale hinted handoff | Read-your-writes mechanism, or read repair |
| Two writes both "succeeded", data diverged | Split brain with no fencing | Fencing/epochs; check whether failover is fenced before trusting it |
| Failover is fast but clients error for ~seconds | Raft leader change + no client-side retry | Client must follow the leader and retry idempotent operations |
| A node rejoins and is hours behind | It was down while WAL was retained, or it was removed from the membership | Re-bootstrap (`pg_rewind`/snapshot resync) rather than letting it replay |
| Time jumps after failover, and TLS or token validation breaks | Clock sync differs across nodes | NTP everywhere; in Postgres also watch `track_commit_timestamp` assumptions |

## Operating checklist

- [ ] Lag is monitored as a number, per replica, with an alert threshold.
- [ ] Read-your-writes is implemented and named in the API's contract.
- [ ] Failover is **fenced**, and the fencing path is tested (kill the primary,
      confirm the old one cannot write).
- [ ] Backups are taken from a primary or with `hot_standby` awareness, and a
      restore has been performed at least once.
- [ ] Backlog is bounded: the replica can fall behind for hours without
      filling the disk, or you alert on it.
- [ ] The shard count, rebalance strategy, and the next reshard date are
      written down, and the reshard is rehearsed on a copy.
- [ ] Clients treat a leader change and a node-down as retryable (idempotent
      operations only).

## Files

- None — this is deliberately self-contained. The isolation and transaction
  mechanics that interact with replication are in `database-transactions`.

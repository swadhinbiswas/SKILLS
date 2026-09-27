---
name: database-transactions
description: Reason about transaction semantics - what each isolation level actually prevents, lost updates and optimistic locking, SELECT FOR UPDATE, savepoints, deadlock avoidance by consistent lock ordering, and why long transactions are the root cause of most production stalls. Use when a user reports phantom or dirty reads, a lost-update or double-spend bug, "could not serialize access due to read/write dependencies", deadlock errors, or is choosing an isolation level or asking whether a transaction is too long. Triggers on "isolation level", "race condition", "lost update", "deadlock", "Serializable", "repeatable read", "transaction not committing".
compatibility: PostgreSQL 12+, MySQL 8.0 (InnoDB), and SQL Server; SQLite differs and is called out explicitly.
metadata:
  version: "1.0"
---

# Database Transactions

Transactions fail in ways that look random until you name the anomaly. The
output is a decision: which isolation level, which locking strategy, and
where the transaction boundary sits.

Read `references/isolation-levels.md` for the per-engine anomaly matrix and
worked lost-update examples.

## Pick the weakest level that is actually correct

| Need | PostgreSQL | MySQL InnoDB | Notes |
|---|---|---|---|
| No dirty reads | `READ COMMITTED` | `READ COMMITTED` | The default in both. Start here. |
| No non-repeatable reads | `REPEATABLE READ` | `REPEATABLE READ` | Needed for "read, decide, write" over several rows |
| No phantom rows (set-based predicates) | `REPEATABLE READ` | `SERIALIZABLE` | Engines differ; `SELECT ... WHERE status='new'` behaves differently |
| Serializable, predicate-based | `SERIALIZABLE` | `SERIALIZABLE` | Retries required; not a correctness guarantee without them |
| Business invariants (balance ≥ 0) | Constraint + `SERIALIZABLE`/lock, or a conditional `UPDATE` | `SELECT ... FOR UPDATE` | Prefer a real constraint where one exists |

`READ UNCOMMITTED` in Postgres is treated as `READ COMMITTED` and emits a
warning; in MySQL InnoDB it is also mapped to RC, and rows may show *partially
updated* values because updates are not row-atomic.

**Do not reach for `SERIALIZABLE` to paper over a modelling problem.** It is
faster to fix a missing `CHECK` or a missing unique index than to serialize an
entire workload.

## The default fix for lost updates

A read-modify-write in application code is broken under every isolation level
except `SERIALIZABLE`, because the read and the write are separate statements:

```python
# WRONG: two concurrent sessions both read total=100, both write 110 -> lost update
total = conn.execute("SELECT total FROM accounts WHERE id=1").scalar()
conn.execute("UPDATE accounts SET total=? WHERE id=1", (total + 10,))
```

Fix it in the **database**, not the application. Pick the first that applies:

1. **One atomic statement** — no read at all:
   ```sql
   UPDATE accounts SET total = total + 10 WHERE id = 1;
   ```
2. **Conditional update (compare-and-set)** — the row's `version` or the value
   itself is in the `WHERE`:
   ```sql
   UPDATE accounts SET total = ?, version = version + 1
   WHERE id = ? AND version = ?;   -- 0 rows updated == someone beat us
   ```
3. **Optimistic locking** — a `version` column bumped by every writer; the
   application retries a fixed number of times on 0 rows updated. This is the
   right default for user-driven optimistic UIs.
4. **`SELECT ... FOR UPDATE`** — when the read is genuinely needed first.

The error `UPDATE ... WHERE version = ?` affecting 0 rows is **not** an error
condition; treat it as a retry signal, not a bug.

## `SELECT ... FOR UPDATE`

```sql
BEGIN;
SELECT total FROM accounts WHERE id = 1 FOR UPDATE;  -- row lock held
UPDATE accounts SET total = 10 WHERE id = 1;
COMMIT;
```

- Locks the rows the query reads. Under Postgres `READ COMMITTED`, if a
  concurrent update arrives while you wait, the lock returns the **updated**
  row — the value you read may be newer than the snapshot you started with.
  Re-check any predicate you relied on after the lock is granted.
- Use a covering predicate: `WHERE id = 1 FOR UPDATE` locks only `id = 1`.
  A bare `FOR UPDATE` on a query that scans `status='new'` locks every matching
  row it touches, which is how one transaction blocks a queue.
- **Take the lock as early as possible**, before doing any other work
  (HTTP calls, computing, serializing). Every statement between `BEGIN` and
  `COMMIT` extends the lock's lifetime.
- `FOR UPDATE NOWAIT` raises `55P03 could not obtain lock on row in relation ...`
  immediately instead of waiting. `FOR UPDATE SKIP LOCKED` skips locked rows
  and returns the rest — ideal for queue workers, where two workers must not
  process the same item.

## Deadlock avoidance

Postgres reports the real cause:
`ERROR: deadlock detected` followed by `DETAIL: Process 1234 waits for ShareLock on transaction 5678; process 5678 waits for ShareLock on transaction 1234`.

Deadlocks are not bad luck; they are two transactions taking the same locks in
different orders. Fix the order, not the retry:

- **One global lock order.** Sort the keys and always acquire in that order —
  `SELECT ... FOR UPDATE` over `WHERE id IN (...) ORDER BY id`. An in-memory
  `sorted(ids)` is enough.
- **One lock per transaction where possible.** An aggregate (`UPDATE accounts
  SET balance = balance + delta`) or a single row beats a read-then-write over
  a set.
- **Short transactions.** The window in which an inconsistent order can form is
  the transaction length.

Retry on deadlock is legitimate, but only with the lock order fixed and a
bounded, jittered retry. `database is locked` (MySQL/SQLite) and `40P01
deadlock_detected` (Postgres) are retryable; `23505 unique_violation` is
usually not.

## Savepoints

Use a savepoint to roll back part of a transaction, not to manage long work.

```sql
BEGIN;
SAVEPOINT sp1;
INSERT INTO audit (...) VALUES (...);   -- a chunk you may want to undo
-- on failure: ROLLBACK TO SAVEPOINT sp1;  the transaction stays usable
COMMIT;
```

Every `SAVEPOINT` is a real subtransaction: it pins an `xmin` (Postgres) and
blocks vacuum across the whole database. A savepoint in a hot loop is a
production problem, not a code smell.

## Transactions that run too long

The failure mode is never a single slow query — it is a transaction that
quietly stays open. Symptoms and their causes:

| Symptom | Likely cause |
|---|---|
| `idle in transaction` for minutes | Code path that commits only on the success branch — a `return` or a raised exception skips the commit |
| Replication lag | A long write transaction on the primary replays slowly on standbys |
| Table bloat everywhere | An old `xmin` blocks vacuum (long read, long transaction, or an abandoned replication slot) |
| `FATAL: terminating connection due to administrator command` storms | `idle_in_transaction_session_timeout` firing |
| Row locks pile up | Transaction opened early, lock taken late |

Find them in Postgres:

```sql
SELECT pid, state, now() - xact_start AS xact_age, left(query, 80)
FROM pg_stat_activity
WHERE state <> 'idle' OR now() - xact_start > interval '1 min'
ORDER BY xact_start;
```

Enforce it in config rather than hoping:
`idle_in_transaction_session_timeout = '60s'`,
`statement_timeout` per role, and `lock_timeout` short for anything
user-facing so waits fail fast instead of piling up.

Rules of thumb:

- Open the transaction as late as possible and commit as early as possible.
- **Never** hold a transaction across an HTTP call, a message publish, a
  `sleep`, or user input. The classic dual-write problem: if you must write to
  two systems, use an outbox row in the same transaction and deliver
  asynchronously.
- Set the isolation level **per transaction** that needs it, not per session.
- Read-only reports: use a read replica, not a long read on the primary.

## Savepoints, retries, and idempotence together

A retried `SERIALIZABLE` transaction must be safe to run twice. The
Postgres error is explicit:

```
ERROR: could not serialize access due to read/write dependencies among transactions
```

That is a **retry signal**, not a bug: the transaction was aborted cleanly,
nothing was committed, and re-running it whole is correct. Wrap the *entire*
transaction body in a bounded retry with jitter; retrying only the failed
statement is wrong, because the transaction is already aborted.

## Gotchas

- **`autocommit=False` in an ORM does not always begin a transaction on the
  first SELECT.** Some drivers defer `BEGIN` until the first write, so a read
  and a later write are in *different* transactions — the read-modify-write
  protection you assumed is absent. Verify with the engine's logs or
  `SHOW TRANSACTION ISOLATION LEVEL` / the pool's current state.
- **Committing a transaction that also changed schema can take seconds**, and
  the wait is attributed to the *first* statement. Set a short
  `lock_timeout` so the failure names the real culprit.
- **A savepoint rolled back to does not release the row locks taken after it
  in some engines.** Postgres releases locks acquired after a subtransaction
  rollback, but buffered I/O and row versions can still block vacuum.
- **Two-phase commit is not a substitute for an outbox.** It is a correctness
  tool for the rare case where a distributed atomic commit is genuinely
  required, and it is not supported the way people expect on replicas.
- **An aggregate computed from a `FOR UPDATE` snapshot can still be wrong** if
  the read predicate did not cover the rows you care about; locking locks what
  you selected, not what you meant.
- **Idempotency beats every isolation level** for retried work. Prefer an
  idempotency key table with a unique constraint over retry logic scattered
  across services.
- **"Repeatable read" means different things per engine.** MySQL's
  `REPEATABLE READ` uses gap locks and can lock far more than the rows you
  read, causing much more contention than Postgres's snapshot isolation.
  `innodb_locks_unsafe_for_binlog` and gap locks are the usual suspect for
  "why is this INSERT blocking".

## Files

- `references/isolation-levels.md` — anomaly matrix per engine, the four
  read/write anomalies, worked lost-update and phantom examples, and how
  Serializable maps to Raft/CockroachDB.

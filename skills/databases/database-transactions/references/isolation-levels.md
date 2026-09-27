# Isolation levels and anomalies

## The four anomalies

| Anomaly | What goes wrong | Read scenario |
|---|---|---|
| Dirty read | You read a row another transaction has written but not committed, and it is then rolled back | T1 `UPDATE balance=0`; T2 reads `0`; T1 aborts |
| Non-repeatable read | The same row read twice in one transaction returns different values | T1 `UPDATE balance=100`; T2 reads `100`, then T1 commits; T2 reads `110` |
| Phantom read | A predicate returns a different *set* of rows on the second read | T1 `SELECT * FROM orders WHERE status='new'` returns 5; T1 inserts a 6th; T2 re-runs the query and gets 6 |
| Lost update | Two read-modify-writes overwrite each other; the last writer wins with a value derived from stale data | Two workers both read `total=100` and both write `110` |

Dirty and non-repeatable reads are about *one row*. Phantoms are about *a
predicate*. That distinction is exactly why `REPEATABLE READ` prevents one and
not the other in Postgres.

## Engine behaviour, side by side

| Level | Dirty | Non-repeatable | Phantom | Postgres locks | MySQL InnoDB locks |
|---|---|---|---|---|---|
| Read Uncommitted | possible (PG/ MySQL map to RC) | possible | possible | none | none |
| Read Committed | no | **yes** | **yes** | none for reads | none for reads |
| Repeatable Read | no | no | Postgres: **yes**; MySQL: **no** (gap locks) | none | record + **gap/next-key** |
| Serializable | no | no | no | SSI predicate locks; conflicts abort one txn | all reads take shared locks; gap locks |

Postgres `REPEATABLE READ` is snapshot isolation: no locks are taken for reads,
and a concurrent writer does not block you. It is *not* enough to stop a
phantom. `SELECT ... WHERE status='new' FOR UPDATE` under `REPEATABLE READ`
takes predicate locks and will abort with
`could not serialize access due to concurrent update` if a matching row
changed.

MySQL InnoDB's `REPEATABLE READ` closes the phantom gap with next-key locks
on the index range. This makes it stricter for reads *and* much more
contention-prone: an `INSERT` into a range can block a `SELECT ... FOR UPDATE`
over the same range, and two `INSERT`s of different keys can deadlock.
`SELECT ... LOCK IN SHARE MODE` is a MySQL-specific read lock.

SQL Server: `READ COMMITTED` takes shared locks and **re-reads** upgraded rows,
so it is *not* the same as Postgres RC; `SNAPSHOT` requires
`ALLOW_SNAPSHOT_ISOLATION` on the database and is the real MVCC option;
`READ_COMMITTED_SNAPSHOT` changes RC to MVCC. Postgres-like, you have to turn
it on.

SQLite: one writer at a time. `BEGIN` is a deferred transaction that takes no
locks until the first read/write, which is why SQLite reports `database is
locked` (SQLITE_BUSY) rather than blocking for the whole transaction. Use
`BEGIN IMMEDIATE` to take the write lock up front and get a deterministic
`SQLITE_BUSY` rather than a mid-transaction failure.

## Worked lost update

```
T1 (session 1)                          T2 (session 2)
BEGIN;
SELECT total FROM accounts WHERE id=1;  -- 100
                                        BEGIN;
                                        SELECT total FROM accounts WHERE id=1;  -- 100
                                        UPDATE accounts SET total=110 WHERE id=1;
                                        COMMIT;
UPDATE accounts SET total=110 WHERE id=1;
COMMIT;
```

Final total: **110**, not 220. This happens under *every* isolation level
except `SERIALIZABLE`, because neither T1 nor T2 ever writes a value it did
not compute from a snapshot that is already stale at write time.

The three fixes:

```sql
-- 1. atomic, best
UPDATE accounts SET total = total + 10 WHERE id = 1;

-- 2. compare-and-set, application retries on 0 rows
UPDATE accounts SET total = 110, version = version + 1
WHERE id = 1 AND version = 7;

-- 3. pessimistic, both correct
BEGIN;
SELECT total FROM accounts WHERE id = 1 FOR UPDATE;
UPDATE accounts SET total = 110 WHERE id = 1;
COMMIT;
```

## Worked phantom

```
T1                                        T2 (Postgres REPEATABLE READ)
BEGIN ISOLATION LEVEL REPEATABLE READ;
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT count(*) FROM orders                SELECT count(*) FROM orders
 WHERE status = 'new';    -- 5             WHERE status = 'new';    -- 5
INSERT INTO orders (status) VALUES ('new');
SELECT count(*) FROM orders
 WHERE status = 'new';    -- 5
COMMIT;
                                          SELECT count(*) FROM orders
                                            WHERE status = 'new';  -- 6  <- phantom
```

Same code under Postgres `SERIALIZABLE`, T2's final `SELECT` aborts with
`ERROR: could not serialize access due to concurrent update` — a retry is
required. Under MySQL `REPEATABLE READ`, T1's `INSERT` would block or deadlock
against a `SELECT ... FOR UPDATE` over that range.

The practical fix for a work queue is to make the predicate set stable with a
`SKIP LOCKED` claim, not to escalate isolation:

```sql
-- Postgres
SELECT id FROM jobs WHERE status = 'new'
ORDER BY id
FOR UPDATE SKIP LOCKED
LIMIT 100;
-- MySQL 8: SELECT ... FOR UPDATE SKIP LOCKED
```

## Serializable is SSI, and it is optimistic

Postgres `SERIALIZABLE` uses serializable snapshot isolation: readers take
locks on *ranges* (predicate locks) and abort when a dangerous structure
(`rw` conflict between two transactions) is detected at commit time. Nothing
waits on a reader; transactions either commit or abort with
`40001 could not serialize access due to read/write dependencies among
transactions`.

Two consequences people get wrong:

- **The error appears at `COMMIT`, not at the statement that caused it.** The
  message names a `Process`/`transaction` that may already be long gone, and
  it is not the other transaction's fault. Retry the whole transaction.
- Retries must be **bounded and jittered** (`40001` is a normal, expected
  outcome under load, and unbounded retries turn a spike into an outage).
- Sagas / CockroachDB / Spanner expose the same SSI semantics as "serializable"
  because they cannot take blocking locks across ranges.

## Isolation level is not the only knob

| Knob | Effect |
|---|---|
| `READ DEFERRABLE` (PG) | Skips reads that would return stale data rather than block, at the cost of a serialization failure. Use for long reports. |
| `NOT DEFERRABLE` (PG) | FK/unique checks that take a stronger lock; usually leave on for correctness. |
| `nowait` / `skip_locked` | Convert lock waits into queue-friendly behaviour. |
| Repeatable-read snapshot age | A long-lived snapshot pins `xmin` and blocks vacuum; keep report transactions short or use a replica. |
| Unique index | The strongest invariant guard; a unique violation is deterministic and needs no retry. |
| `CHECK` constraint | Same for ranges; enforced atomically by every engine. |

---
name: database-connection-management
description: Size, configure and debug database connection pools - pool exhaustion, connection storms on restart, prepared-statement caching, pgbouncer and proxy session vs transaction pooling, and the per-request-connection anti-pattern. Use when a user sees "too many connections", pool timeouts, connection refused under load, a deploy that knocks over the database, or is picking pool sizes and timeouts for an ORM or a proxy. Triggers on "connection pool", "too many connections", "max_connections", "pool exhausted", "pgbouncer", "connection storm", "pool_size", "FATAL: sorry, too many clients".
compatibility: PostgreSQL, MySQL, and the common language drivers (psycopg, asyncpg, node-postgres, JDBC, Go database/sql, SQLAlchemy); verify driver-specific option names.
metadata:
  version: "1.0"
---

# Database Connection Management

A connection is expensive (memory, a backend process, a file descriptor, a
buffer allocation) and scarce (a hard `max_connections` limit that will reject
you). Pools are the only way to keep that cost bounded.

## The per-request connection anti-pattern

Opening a connection per HTTP request (or per query) is the most common way a
service takes down a database, because the arrival of requests and the
acquisition of connections are unbounded and uncoordinated. Symptoms: the
database looks fine until a traffic spike, then
`FATAL: sorry, too many clients already`, and recovery requires killing
clients.

- **One pool per process, created at startup, sized for the process count.**
  A pool per request is just as bad as no pool: it churns backend processes,
  defeats server-side prepared statements, and exhausts `max_connections`
  during a rolling deploy.
- **Never hold a connection across a network call to another system.** A
  checkout that spans an HTTP request to a slow third party is a pool
  capacity bug waiting to happen. Check out, do the work, check in.
- **The pool is a queue with a timeout, not a blocking line.** A checkout that
  waits forever converts a slow database into a hung application. Every pool
  has a checkout/connection timeout, and it is short (a few seconds).

## Sizing: the CPU/IO trade-off

Two hard limits, and the pool must fit under both.

- **Postgres** `max_connections` (default 100, often raised to
  `200-500` for app servers) and `superuser_reserved_connections` (3) are kept
  for you by admin and monitoring. A connection is a process with a private
  memory allocation; 500 idle backends on a small box is RSS you cannot use
  for `shared_buffers`.
- **MySQL** `max_connections` (default 151) plus the `max_connections` the
  internal threads reserve.

Rules of thumb:

- **Start at `pool_size = 4–10` per process**, not 50. If a single query needs
  more, the query is slow, not the pool small.
- **Sum across all application instances and the database's own headroom.**
  `instances × workers_per_instance × pool_size + admin + migrations <
  max_connections × 0.7`. Twenty instances × 8 workers × 10 = 1600 — far past
  a single Postgres's comfortable range. That is the signal to add pgbouncer,
  not to raise `max_connections`.
- **The pool should be smaller than the number of CPU cores times a small
  factor** for OLTP, because each connection that is actually running a query
  wants a core; more runnable queries than cores just adds context switching
  and I/O contention. For batch/analytics, many connections can help hide I/O
  latency, but the right answer there is usually a separate pool (or a
  separate database) with a lower, explicit limit.
- **A pool larger than the database can serve is not a buffer, it is a
  queue at the wrong end.** Requests then wait on the database instead of in
  your process, where you could at least time them out and shed load.

Debug the actual cause with the database's view, not the app's:

```sql
-- Postgres: what is connected and doing what
SELECT state, count(*), max(now() - state_change) AS oldest
FROM pg_stat_activity GROUP BY state;
SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1;
SHOW max_connections;
```

High `idle` connections are a pool that is too large or leaked; high `active`
with high wait events is a database that is too slow for the workload (tune
the query — see `postgres-query-tuning`).

## Pool exhaustion, by symptom

| Symptom | Likely cause | Fix |
|---|---|---|
| Checkout timeout, `QueuePool limit ... overflow ...` / `connection pool exhausted` | All connections busy, pool too small **or** connections leaked | Find the leak (below); size the pool to the core count; bound the work |
| `FATAL: sorry, too many clients already` | Sum of pools exceeds `max_connections` | Add pgbouncer, or reduce total pool size |
| `FATAL: the database system is starting up` | The server restarted and old clients are reconnecting | Connection storm — see below |
| `could not connect to server: ... too many clients` intermittently, under deploy | Connection storm on restart | Stagger starts; add pgbouncer; pre-warm slowly |
| `FATAL: terminating connection due to administrator command` | `idle_in_transaction_session_timeout` or a `pg_terminate_backend` sweep | Find the long transaction (see `database-transactions`) |
| Pool grows to its max over time and never shrinks | Leaked connections (checkout without checkin) | Find the code path; the pool is the last line of defence, not a fix |

**Leaks** are the most common cause of "the pool is too small". The signature
is a pool whose in-use count only goes up. Look for: a connection held across
an `await`/HTTP call, an exception between checkout and checkin (use
`with`/`try…finally`/`defer`), a generator function that yields while holding
a connection, or a streaming cursor never closed.

## Connection storms on restart

When a process starts it does not open `pool_size` connections at once — it
opens them as demand arrives. After a restart, the entire fleet does this
simultaneously, and the database receives a thundering herd of thousands of
connection requests in a few seconds. Symptoms:
`FATAL: the database system is starting up`, connection timeouts across all
services, and a database that is briefly unable to serve anything.

- **Stagger and rate-limit startup.** Warm the pool gradually, with jitter,
  rather than at full speed. Do not open the pool synchronously in an
  application health probe.
- **Keep connections across deploys where you can** — blue/green or a proxy
  in front (see pgbouncer) means old connections drain while new ones
  connect.
- **Frontend/backend pooling (pgbouncer) is the real fix** because the
  database only ever sees the proxy's small server-side pool, no matter how
  many clients connect.
- Postgres has a `superuser_reserved_connections` backlog precisely so an
  admin can get in to fix a storm; do not plan on it.

## Prepared statement caching

Prepared statements are parsed once and reused, which saves planning time and
lets the server cache the plan. The pool and statements interact badly:

- **Server-side prepared statements are tied to a connection.** A statement
  prepared on connection A is unknown to connection B; if your pool is larger
  than 1 and the driver caches the prepared handle, statements leak or get
  re-prepared per checkout.
- Postgres: `max_prepared_transactions` applies to two-phase commit, not to
  prepared statements; ordinary named prepared statements are unlimited but
  consume memory in the per-backend plan cache. `pg_prepared_statements` shows
  them; `DEALLOCATE ALL` frees them.
- Drivers: enable client-side reuse (SQLAlchemy `pool_pre_ping`, psycopg's
  statement cache, `go-sql-driver` with `PreferSimpleProtocol=false` for
  implicit prepare, JDBC `prepStmtCacheSize`/`prepStmtCacheSqlLimit` for the
  server-side cache). Prefer relying on the **driver's** cache and not on
  explicit `PREPARE` in application SQL.
- **Prepared plans go stale** when the underlying statistics change; Postgres
  re-plans on execution (generic vs custom plan), so a cached plan is not a
  correctness risk, but it can be a *performance* risk on skewed parameters —
  see `references/plan-nodes.md` in the `postgres-query-tuning` skill.
- `pool_pre_ping` (SQLAlchemy), `tcp_keepalives`, and a server-side
  `idle_session_timeout` handle connections the network dropped; a pool with
  no health check hands a dead connection to a request and you get a
  one-off error.

## Proxy pooling: session vs transaction

A pooler (pgbouncer, ProxySQL, RDS Proxy, Pgpool-II) multiplexes many clients
onto few server connections. The mode determines what is legal in SQL.

| Mode | Connection is held for | Constraint | Use when |
|---|---|---|---|
| `session` | the whole client session | Full SQL, including `SET`, temp tables, `LISTEN`, `PREPARE`, advisory locks | You need session state; you want full compatibility |
| `transaction` | one transaction | **No** `SET`/temp tables/`LISTEN` between transactions; `SET` must be inside the transaction or use `server_reset_query` | The default for web apps with plain SQL |
| `statement` | one statement | Autocommit only, no transactions | Autocommit read-only replicas |

Practical consequences of transaction mode:

- Any code that does `SET timezone = ...` then issues queries **outside** a
  transaction will lose the setting when the connection is returned. Use
  `server_reset_query` (`DISCARD ALL`) and set the value via the connection
  string (`?options=-c%20timezone%3DUTC`) so it is re-applied on every server
  connection.
- `LISTEN/NOTIFY` and advisory locks used as application locks do not survive
  transaction pooling. Use a real external lock (Redis, etcd) or a table
  lock inside a transaction.
- `WITH HOLD` cursors and server-side `PREPARE`/`EXECUTE` across transactions
  break in transaction mode.
- Pooler capacity is a hard limit on the database's real concurrency: size
  `default_pool_size` (server connections per database/user pair) to the
  database's `max_connections` budget, and size client pools to the *client*
  count (hundreds is fine, the pooler multiplexes).

MySQL's analogue is ProxySQL in `mysql_native` vs the transaction-aware
protocols (MySQL 8's `CLIENT_COMPRESS`, and notably **MySQL doesn't have a
true transaction pooler** the way pgbouncer does for Postgres — MySQL
Community's `mysql-connector` and Oracle MySQL Proxy behave differently).
For MySQL, RDS Proxy and ProxySQL are the usual answers; do not assume
transaction pooling semantics identical to pgbouncer's.

## Gotchas

- **A statement cache and a pooler only coexist from pgbouncer 1.21, and only
  with `max_prepared_statements` above 0** (it defaults to 0 where it was
  introduced). Below that, protocol-level named statements sent by node-postgres
  or PgJDBC fail in transaction mode with `prepared statement "s1" does not
  exist` or `prepared statement "s1" already exists`, because the name is
  re-prepared on a different server connection each time.
- **`idle_in_transaction_session_timeout` defaults to 0 — disabled.** A leaked
  transaction keeps its snapshot open, holds the global `xmin` horizon back so
  autovacuum cannot remove dead tuples anywhere in the database, and keeps any
  row locks it took. Set it per role, and watch
  `pg_stat_activity WHERE state = 'idle in transaction'`.
- **Behind a transaction pooler, a client-side "idle" session is invisible to
  the server as idle** — the server only sees an open transaction, so
  `pg_stat_activity` shows `active`/`idle in transaction` for a client that is
  doing nothing. Client-side timeouts are the only control you have there.
- **libpq connects to a standby unless you say otherwise.** With several `A`
  records behind one hostname it will happily land on a replica and every write
  fails with `cannot execute INSERT in a read-only transaction`. Pass
  `target_session_attrs=read-write` in the connection string.
- **A checkout timeout bounds queueing, not execution.** A query that hangs for
  ten minutes keeps its connection for ten minutes, and the pool drains with no
  error anywhere. Bound execution server-side with
  `ALTER ROLE app SET statement_timeout = '30s'` and `lock_timeout`; client
  timeouts cannot substitute.
- **A fixed-size pool reconnects in lockstep.** HikariCP's `minimumIdle` defaults
  to `maximumPoolSize`, so an idle pool expires fully and the whole fleet
  reconnects at once — set `minimumIdle` lower and give `idleTimeout` somewhere
  to shrink to. `connectionTimeout` below 250ms is rejected outright.
- **`pool_pre_ping` costs a round trip per checkout and does not fix
  "too many clients".** The server refusing the connection is a capacity problem;
  the ping just fails faster. Use keepalives (`tcp_keepalives`, `keepalives_idle`)
  where you only need dead-socket detection.
- **MySQL closes idle connections long before you expect.** Past
  `wait_timeout` (default 28800s) or an intermediate proxy/LB idle timeout you
  get `MySQL server has gone away` (2006) or
  `Lost connection to MySQL server during query` (2013). Set `pool_recycle` below
  the *smallest* hop's timeout and keep `pool_pre_ping` on.
- **One async connection is not concurrency-safe.** Two tasks sharing a
  `database/sql` `*sql.DB` are fine; two tasks sharing one `sql.Tx` or one
  asyncpg `Connection` interleave on the same wire and you get
  `InterfaceError: cannot perform operation: another operation is in progress`.
  Transaction per task, connection per transaction.
- **The pooler's `default_pool_size` is per database *and* user pair, and its
  client side has its own ceiling.** `default_pool_size` (default 20) × pairs is
  the real server-connection count, and exceeding `max_client_conn` surfaces as
  `no more connections allowed (max_client_conn)` on the proxy, not on Postgres.
- **A long transaction in transaction mode consumes the proxy's whole server
  connection for its duration.** Transaction pooling multiplexes only across
  transactions, so one 30s transaction holds one backend and blocks every client
  multiplexed onto it.

## The per-instance checklist

- [ ] One pool per process, created at startup, never per request.
- [ ] `pool_size` × instances + workers × ... is under ~70% of
      `max_connections`.
- [ ] A short checkout timeout, and the app sheds or retries rather than
      blocking forever.
- [ ] Connections are never held across an external network call.
- [ ] `pool_pre_ping`/health check on, so a dropped connection is detected.
- [ ] Statement caching left to the driver; no hand-rolled `PREPARE`.
- [ ] Startup is staggered and rate-limited; no synchronous full warm-up in a
      health check.
- [ ] If behind a pooler: mode chosen deliberately, `server_reset_query` set,
      and no session-state assumptions in the application.
- [ ] `application_name` (or equivalent) is set on every pooled connection, so
      `pg_stat_activity` tells you which service is holding what.

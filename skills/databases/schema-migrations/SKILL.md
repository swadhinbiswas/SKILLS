---
name: schema-migrations
description: Evolve a live database schema across Postgres, MySQL and SQLite without downtime or data loss - expand-contract rewrites, backfill versus constrain, rollback and forward-fix rules, and the tool-specific commands for Alembic, Flyway, Prisma, golang-migrate, Django, Rails, Knex and Liquibase. Use when someone asks to add/rename/drop a column or table, when a deploy is blocked on a migration, when a migration failed halfway in production, or when an index or constraint locks a hot table. Triggers on "migration", "alter table", "add column", "schema change", "rollback", "zero downtime", "DDL lock".
compatibility: Covers PostgreSQL 12+, MySQL 8.0+, and SQLite 3.35+; every tool example uses that tool's documented CLI.
metadata:
  version: "1.0"
---

# Schema Migrations

Change a live schema without a maintenance window and without losing data. The
output is a set of migration files, a written rollback, and a rehearsal against
production-sized data.

## Non-negotiable rules

1. **Never edit a migration that has been applied anywhere but your laptop.**
   Checksums will not match and every environment diverges. Roll forward with
   a new migration; the ledger is append-only.
2. **Every migration ships with a rollback, or a documented forward-fix.** An
   irreversible migration (dropped column, dropped table, narrowed type) must
   say so in its header and name the forward-fix that replaces the rollback.
3. **Assume the deploy and the migration are two different events.** Code must
   work with both the old and the new schema during the window between them, or
   you have to sequence them deliberately.
4. **One logical change per migration.** A migration that adds a column *and*
   backfills *and* drops the old column cannot be bisected or partially
   rolled back.

## Workflow

- [ ] 1. Classify the change: additive / constraint / type change / removal
- [ ] 2. Check the lock it takes, per engine (table below)
- [ ] 3. Write expand → migrate → contract as separate deploys
- [ ] 4. Write the migration file and its rollback
- [ ] 5. Rehearse on a production-sized copy, timed
- [ ] 6. Deploy expand, backfill, then contract — with a verified checkpoint between

## Step 1 — classify the change

| Change | Online? | Default approach |
|---|---|---|
| Add nullable column | Yes, all three engines | Single migration |
| Add column with constant default | PG 11+ yes; MySQL 8.0 instant for some cases; **SQLite no** | Split: add nullable, backfill, then set default |
| Add `NOT NULL` | No — scans and validates | `NOT VALID` + `VALIDATE`, then `SET NOT NULL` |
| Add foreign key | No by default | `NOT VALID`, then `VALIDATE CONSTRAINT` |
| Add check constraint | No by default | `NOT VALID`, then `VALIDATE CONSTRAINT` |
| Add index | PG: `CONCURRENTLY`; MySQL 8.0: online DDL; SQLite: build a new table | Separate migration, never inside a transaction with other DDL |
| Rename column/table | No | Expand-contract with two columns; never rename in place |
| Change column type | Rewrites the table (PG 11+ narrows in place, but breaks large values) | New column, backfill, swap, drop old |
| Drop column/table | Yes but unrecoverable | Separate deploy, after the data is provably unused |

## Step 2 — what each engine actually does

**PostgreSQL**

- `ACCESS EXCLUSIVE` is required for most `ALTER TABLE` forms. It does not
  block reads, but it blocks every subsequent read while it waits behind the
  lock queue — one slow `ALTER` can stall a whole database.
- Adding a nullable column: metadata only, instant.
- Adding a column with a **constant** default: instant since PG 11. A
  **volatile** default (`now()`, `gen_random_uuid()`) rewrites the entire
  table. Expand with no default, backfill, then `SET DEFAULT`.
- `CREATE INDEX CONCURRENTLY`: no write lock, two table scans, cannot run
  inside a transaction block, and can leave an `INVALID` index behind. Retry
  with `DROP INDEX CONCURRENTLY` first.
- Set `lock_timeout` on every connection that runs DDL so a queued lock fails
  fast instead of stacking up:
  ```sql
  SET lock_timeout = '3s';
  SET statement_timeout = '0';  -- DDL legitimately runs long; lock wait must not
  ```
  `ERROR: canceling statement due to lock timeout` means your transaction
  would have blocked others; retry, do not raise the timeout and wait.
- Long locks make a **queue** in `pg_locks`; queries behind them do not get an
  error, they just stop moving. Check `pg_stat_activity` before blaming the
  app.

**MySQL 8.0**

- `ALGORITHM=INPLACE` avoids a copy but still takes a metadata lock at start
  and end. `ALGORITHM=COPY` rewrites the table and blocks writes for the whole
  operation.
- `LOCK=NONE` permits concurrent reads and writes; unsupported operations fail
  fast with a clear error rather than silently copying. Always try the strict
  form so a surprise rewrite cannot happen:
  ```sql
  ALTER TABLE orders ADD COLUMN risk_score INT, ALGORITHM=INPLACE, LOCK=NONE;
  ```
- `pt-online-schema-change` and `gh-ost` do copy-based changes without blocking,
  at the cost of triggers, replication lag, and a trigger on the table.
- Foreign keys are never `LOCK=NONE`-able when adding to an existing table; plan
  a maintenance window or add the constraint after backfill.

**SQLite**

- Almost all `ALTER TABLE` is unsupported. `ALTER TABLE ... RENAME COLUMN`,
  `ADD COLUMN`, and `DROP COLUMN` exist; everything else requires the
  create-new-copy-rename dance.
- `ADD COLUMN NOT NULL DEFAULT 'x'` is allowed only if the default is
  constant, and it is O(1).
- To add a constraint or change a type you must build `new_table`, copy, drop,
  rename. `PRAGMA foreign_keys` must be `ON` (it is off by default) for the
  whole process to be safe.
- DDL takes a writer lock. `SQLITE_BUSY` / `database is locked` during a
  migration means a live connection is reading; set
  `PRAGMA journal_mode=WAL` and a `busy_timeout` in every client.

## Step 3 — expand-contract

For anything that rewrites, narrows, renames, or constrains a large table, ship
three changes across at least three deploys.

```
deploy 1  EXPAND   add new_col (nullable, no default)
deploy 2  MIGRATE  backfill in batches, then dual-write from the app
deploy 3  VERIFY   count mismatches; only now enforce constraints
deploy 4  CONTRACT drop the old column, add NOT NULL, tighten the type
```

The application must tolerate the middle state: it reads `coalesce(new_col,
old_col)` and writes **both**. That single fact is what makes expand-contract
work — once you dual-write, you can stop caring about the backfill and just
wait for the natural turnover of rows.

Batched backfill, each batch its own transaction with a pause, so the table
never stops serving traffic:

```sql
-- run in a loop, committing per batch
UPDATE t SET new_col = old_col
WHERE id IN (SELECT id FROM t WHERE new_col IS NULL LIMIT 5000);
-- then sleep briefly, e.g. 100ms, before the next batch
```

If the new column is `NOT NULL` from the start, the dual-write app keeps it
filled and the backfill is a safety net for rows written by anything else.

## Step 4 — constrain last

Backfill first, then add the constraint unvalidated, then validate:

```sql
ALTER TABLE orders ADD CONSTRAINT orders_email_fk
  FOREIGN KEY (customer_id) REFERENCES customers(id) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_email_fk;
```

`NOT VALID` skips the existing-row scan and takes only
`SHARE ROW EXCLUSIVE`, so it is near-instant. `VALIDATE` scans but permits
concurrent reads and writes. A validated constraint is a promise the database
will now enforce; if validation fails you get the offending rows named, fix
the data, re-validate. Same pattern in Flyway/Liquibase via
`<constraints nullable="true">` and a separate validate step.

## Step 5 — rehearse

A migration is not done until it has run against a copy of production **at
production row count**, with wall-clock time recorded. Check specifically:

- [ ] How long does the lock hold? (It should be milliseconds, not seconds.)
- [ ] Does the backfill finish before the next deploy? If not, that is a
      separate resumable job, not a migration.
- [ ] Does it run inside a transaction that also does DML? That is how
      Postgres deadlocks and how bloat starts.
- [ ] Is the rollback written and *tested* by running it, not by reading it?
- [ ] What happens if the app version still running is the old one? It must
      work.
- [ ] Does the new code fail if the migration has not run yet? It should fail
      with a clear error, not corrupt anything.

## Step 6 — the deploy itself

Prescriptive, because this is where mistakes are expensive:

1. Take the migration artifact (immutable, checksummed) and the app artifact.
2. Run the migration against production first (it is backward compatible with
   the old app by construction), then deploy the app. If the order is
   reversed, the expand is not additive and the old app breaks.
3. Watch the lock queue and error rate for the duration. The backfill runs
   separately, throttled, and is resumable.
4. Verify with a read-only query (`SELECT count(*) ... WHERE new_col IS NULL`
   must be 0) before the contract deploy.
5. Only then drop the old column. That drop is the point of no return.

If a migration fails halfway, do not edit the file. Diagnose, add a new
migration that repairs the state, and mark the failed one in the ledger so the
tool does not retry it blindly.

## Gotchas

- **"You have N unapplied migrations" after a fresh clone** usually means a
  branch merge reordered files, and the tool resolves it by timestamp or by
  lexicographic name. Rename with a numeric prefix (`0007_...`) or adopt the
  tool's revision id, never `git merge` migration folders.
- **Checksum mismatches** (`Alembic: Migration file ... has been modified`,
  `Flyway: checksum mismatch`) mean someone edited an applied migration. Never
  `repair`/`stamp` to silence it in production — find the environment that is
  actually behind and reconcile deliberately.
- **Squashing migrations for a fast test setup changes their checksums** and
  breaks every long-lived environment. Give tests their own build path
  (`alembic upgrade head` against a fresh database) rather than squashing.
- **`DROP COLUMN` executed with `IF EXISTS` in production can silently succeed
  on the wrong database.** Always echo `current_database()` into migration
  output and refuse to run outside the expected environment.
- **Foreign keys make every write on the child check the parent.** A
  last-minute `NOT VALID` FK can be a real throughput regression, not free
  correctness.
- **A unique index creation can fail on data you thought was unique.**
  Deduplicate *before* the migration ships, not in the migration.
- **`SET NULL` on a column with a dependent view or a `DEFAULT` that another
  migration assumes** is a silent contract break for old app versions.
- **Postgres: a long transaction in the same session as DDL holds the old
  snapshot**, so the `ALTER` waits even though nothing conflicts. Commit
  before you migrate.
- **Adding an index does not speed up a query the planner cannot prove.**
  After creating it, check the plan actually uses it (see
  `postgres-query-tuning`).
- **`CREATE INDEX` inside a Flyway/Liquibase migration is transactional** and
  therefore cannot use `CONCURRENTLY`. Configure the tool for
  non-transactional DDL rather than silently taking the blocking path.

## Tool notes

Read `references/tool-workflows.md` for the exact CLI for Alembic, Flyway,
Prisma, golang-migrate, Django, Rails, Knex, and Liquibase, including
autogenerate pitfalls, `CONCURRENTLY` configuration, and how each tool handles
failed migrations and checksums.

## Files

- `references/tool-workflows.md` — per-tool commands, autogenerate gotchas,
  non-transactional DDL config, backfill and rollback patterns.

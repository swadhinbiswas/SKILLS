---
name: integration-test-harnesses
description: Build integration test harnesses that are reliable in CI and on laptops - containers for real dependencies, transaction-per-test or truncate-and-refill database fixtures, docker compose services in CI, deterministic seed data, and teardown that actually runs. Use when tests pass locally but fail in CI, when a suite needs Postgres/Redis/Kafka locally, when tests interfere with each other under parallelism, or when someone mentions port conflicts, timezone bugs, dirty state, or EADDRINUSE. Triggers on "flaky in CI", "test database", "docker compose for tests", "EADDRINUSE", "test isolation", "integration tests slow".
compatibility: Examples use Docker and docker compose v2, pytest, and Testcontainers-style APIs; adapt the intent for other ecosystems. Verify CLI flags with --help for your installed version.
metadata:
  version: "1.0"
---

# Integration Test Harnesses

An integration test is worth the cost only if it fails for one reason: the
wiring is wrong. Everything here is about removing every *other* reason.

## Workflow

- [ ] 1. Decide what the integration test must prove that a unit test cannot
- [ ] 2. Pick the real dependency over a mock: container, not in-memory double
- [ ] 3. Guarantee isolation: per-test transaction **or** schema/database per worker
- [ ] 4. Seed with fixed, deterministic data — no `now()`, no `random`
- [ ] 5. Make teardown unconditional (`finally`, container `tmpfs`, job timeout)
- [ ] 6. Run the suite at the CI parallelism level locally before you believe it
- [ ] 7. Add a guard that refuses to run against anything that looks production

## Real dependencies, in containers

Mocking a database to test SQL is testing nothing. The house default: run the
real engine in a container.

```yaml
# docker-compose.test.yml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: test
      POSTGRES_DB: app_test
      # Deterministic collation and timezone, not whatever the host has.
      POSTGRES_INITDB_ARGS: "--locale=C --encoding=UTF8"
    command:
      - postgres
      - -c
      - timezone=UTC
      - -c
      - log_timezone=UTC
    tmpfs:
      - /var/lib/postgresql/data     # in RAM, and gone when the container stops
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d app_test"]
      interval: 1s
      timeout: 3s
      retries: 30
  redis:
    image: redis:7-alpine
    tmpfs: ["/data"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 1s
      timeout: 3s
      retries: 30
```

```bash
docker compose -f docker-compose.test.yml up -d --wait    # --wait: block until healthy
# run the suite
docker compose -f docker-compose.test.yml down -v
```

- **`tmpfs` for data directories** makes teardown free and stops a crashed run
  from leaving a volume that poisons the next one.
- **Pin the image by tag, and in a serious project by digest.** `:latest` makes
  the test result a function of when it ran.
- **`--wait` (compose v2) or an explicit healthcheck** is mandatory. `sleep 5`
  before the suite is a race that passes locally on a fast laptop and fails on a
  loaded CI runner.
- **The Testcontainers pattern** (Java/Go/.NET/Node/Python ports) gives the same
  guarantee without a compose file and cleans up on process exit. Use it when
  each service needs a different config per test; use compose when the same
  services are wanted by devs and CI alike.

## Database isolation: pick one, per worker

Two workable designs. Pick one and apply it uniformly; mixing them is a
flaky-test generator.

| Design | How | Cost | When |
|---|---|---|---|
| Transaction per test | Wrap each test in a transaction, `ROLLBACK` after | ~0 | Code under test uses the same connection/transaction; no `COMMIT` inside the test |
| Database (or schema) per worker | Each parallel worker gets its own database, truncate/refill between tests | Migrations run N times | Code commits, uses `LISTEN/NOTIFY`, or opens its own connections |

**Transaction per test** (pytest, SQLAlchemy 2.x style):

```python
# tests/conftest.py
import pytest
from sqlalchemy import create_engine, text

@pytest.fixture
def db_session(migrated_db):
    engine = create_engine(migrated_db, future=True)
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        trans.rollback()      # every write disappears, including indirect ones
        conn.close()
```

- `join_transaction_mode="create_savepoint"` matters: it gives the application
  its own nested transaction that still rolls back with the outer one, even if
  the code under test calls `commit()`.
- If the code under test commits on a *different* connection (a background
  worker, a raw `engine.connect()`), the rollback saves nothing and the
  database grows a row per test. That is not a flaky test, it is a wrong
  design — switch to per-worker databases.

**Per-worker database**:

```python
import os, uuid
import pytest
from sqlalchemy import create_engine, text

@pytest.fixture(scope="session")
def _worker_db_base(migrated_db):
    return migrated_db.rsplit("/", 1)[0]        # keep credentials, change dbname

@pytest.fixture(scope="session")
def worker_db(request, _worker_db_base):
    name = f"test_{os.getpid()}_{request.config.workerinput['workerid'].replace('/', '_')}"
    admin = create_engine(_worker_db_base + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))   # identifiers cannot be bound
    yield f"{_worker_db_base}/{name}"
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
```

Name from `os.getpid()` plus the xdist worker id, never from the clock or
`uuid4()` — you need to recognise leftovers. Then, between tests, truncate the
data tables and reset sequences:

```sql
TRUNCATE TABLE order_items, orders, users RESTART IDENTITY CASCADE;
```

`RESTART IDENTITY` matters: without it, ids keep climbing across tests and any
test that asserts on ids, pagination, or a `LIMIT` will eventually behave
differently (and only at run 200).

## Seeding deterministic data

- **No `now()`, no `random`, no `uuid4()` in seed data.** A timestamp default
  makes "created in the last hour" tests depend on when CI ran.
- **A single `factories.py` with an explicit override for every field**; a
  factory with a hidden default is a factory nobody can trust.
- **Timestamps as fixed constants, timezone-aware UTC**; where the test needs
  relative time, inject a clock and advance it.
- **Prefer a SQL or JSON fixture file** over Python seed code when the dataset
  is stable — it is reviewable in a diff and fast to load.
- **One "kitchen sink" seed plus small targeted seeds.** The kitchen sink proves
  wiring; small seeds keep each test's failure legible.
- **Set the database timezone explicitly in every harness** (compose command
  above, or `TZ=UTC` in CI) and make the *application* timezone explicit too.
  `datetime.date.today()` and `AT TIME ZONE` are the two usual offenders.

## docker compose in CI

```yaml
jobs:
  integration:
    runs-on: ubuntu-latest
    timeout-minutes: 20          # a stuck suite must not hold a runner for hours
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: app_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U postgres -d app_test"
          --health-interval 1s --health-timeout 3s --health-retries 30
    env:
      TEST_DATABASE_URL: postgres://postgres:test@localhost:5432/app_test
      TZ: UTC
    steps:
      - uses: actions/checkout@<sha>            # see github-actions-hardening
      - run: docker compose -f docker-compose.test.yml up -d --wait
      - run: pytest -n 4 -m integration
        env:
          TEST_DATABASE_URL: ${{ env.TEST_DATABASE_URL }}
      - if: always()
        run: docker compose -f docker-compose.test.yml logs --no-color
      - if: always()
        run: docker compose -f docker-compose.test.yml down -v
```

- **Use `services:` for a single container, compose for several** — a compose
  service cannot depend on a `services:` container being healthy.
- **`if: always()` on the log and down steps.** Without it, the first failing
  test loses you the container logs, which is the only diagnostic you will
  want.
- **Migrations run inside the test job** (once per worker database) or in a
  separate setup step that is cached as an artifact — a migration step that
  races the readiness of the DB is a classic 10-minute flake.

## Teardown that actually runs

```python
import contextlib, uuid, pathlib

@contextlib.contextmanager
def scratch_dir():
    d = pathlib.Path(tempfile.gettempdir()) / f"t_{uuid.uuid4().hex}"
    d.mkdir(parents=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
```

- Teardown belongs in `finally` / `afterEach` / `yield` fixtures, never after an
  assertion. A failing test must not skip cleanup.
- A per-test unique temp directory (`pytest`'s `tmp_path`, `mkdtemp`) beats a
  fixed path: parallel workers then cannot collide, and a crashed run leaves
  garbage that is obviously garbage.
- **CI must clean the workspace** even when the job is cancelled: a
  `post`/`always` step, and a job-level `timeout-minutes`.
- **Container cleanup**: prefer ephemeral CI runners. On a persistent runner,
  a `docker system prune` between jobs and a per-job project name
  (`COMPOSE_PROJECT_NAME=ci-${{ github.run_id }}-${{ strategy.job-index }}`)
  prevent two jobs from sharing a container.

## The local-vs-CI divergence checklist

Before you call a test flaky, diff these. In rough order of how often they
are the cause:

1. **Parallelism** — CI runs `-n auto` on 4+ workers; you ran serially. Two
   tests sharing a row, a file, or a `SELECT` without `FOR UPDATE` will pass
   serially and fail in parallel. Reproduce with `pytest -n 4` locally, and
   with `PYTHONHASHSEED=random` to shake out set/dict ordering assumptions.
2. **Timezone and locale** — `TZ=UTC` in CI, local machine in `Europe/Berlin`.
   Classic symptom: a date-boundary test that fails only after 20:00 local, or
   a string sort that differs by collation. Fix in the harness, not in the test.
3. **Port conflicts** — two workers, or your laptop's dev server, on 5432.
   Symptom: `EADDRINUSE`, or worse, tests silently talking to your *dev*
   database. Randomise the port per worker or use a unix socket /
   container-internal networking.
4. **CPU count / resources** — a 4-worker DB-backed suite on a 2-core runner
   with a 512MB container will hit timeouts and OOM. Cap workers to
   `min(4, cores/2)` and give the container realistic limits.
5. **Fresh vs warm filesystem** — tmpfs vs disk; macOS case-insensitive
   filesystem vs Linux; Windows path length and reserved names.
6. **Dependency version drift** — a `latest` tag, a floating transitive
   dependency. Compare resolved versions between local and CI before debugging
   the test.
7. **Clock skew / NTP** — a certificate or signed-request test that fails only
   in CI.
8. **Environment variables** — a `.env` file loaded locally and absent in CI.
   Assert the presence of every required variable in a session-scoped fixture so
   a missing one fails immediately with the variable's name.

## Parallelism rules

- **Partition by worker, not by test.** xdist distributes tests round-robin
  across workers; each worker needs its own database, its own port, its own
  temp dir, and its own container. Set `-n` explicitly in CI (`-n 4`), not
  `auto`, so a smaller runner cannot make the suite *slower*.
- **Shard by file or by test id for wall-clock wins** — but shard on a stable
  key (module path), never on a hash of a timestamp.
- **`--dist loadfile` keeps a module's tests on one worker.** Useful when a
  module is expensive to set up, and it removes a class of intra-module races.
  It also serialises hot spots — use it deliberately.
- **Cap DB connections**: workers × pools ≤ `max_connections`. A suite that
  works with 1 worker and throws `FATAL: sorry, too many clients already` at 8
  is a connection-budget bug.

## Safety notes

- **Never point the suite at a shared environment.** The harness must assert
  the target is a local container or a `*_test` database, and fail closed:
  ```python
  if not (url.host in {"localhost", "127.0.0.1", "db"} and url.path.endswith("_test")):
      pytest.fail(f"refusing to run destructive tests against {url!r}")
  ```
- **Never let an integration test call a real third party.** Point SDK clients
  at a local fake (WireMock, a stub service, a recorded-cassette in replay
  mode) and assert the recorded interactions.
- **Never use production data.** Anonymised dumps still carry personal data
  and real volume; if volume is the point, generate synthetic rows at the
  target size instead.
- **Destructive setup commands run against the container only.** If the harness
  cannot prove the target is disposable, stop and ask.

## Gotchas

- `depends_on` in compose waits for *start*, not for *readiness*. Add
  healthchecks and use `--wait`, or your suite connects to a Postgres that is
  still initialising (`the database system is starting up`).
- **`docker compose up` with no `-d` blocks.** In CI this is a job that never
  progresses and hits the job timeout.
- **A leaked connection per test exhausts `max_connections`** long before anyone
  looks for the leak; the symptom is a slowdown that grows with test count.
- **`TRUNCATE` takes an `ACCESS EXCLUSIVE` lock and blocks concurrent workers.**
  With per-worker databases that is fine; with one shared database it
  serialises the suite.
- **Migrations that are not idempotent** break the second run in the same
  volume. Make the harness re-runnable: `DROP DATABASE`+recreate, or
  `alembic upgrade head` from a known base each session.
- **`pg_dump`/`restore` fixtures hide that a migration is broken**; restore is
  a good way to make a suite fast, and a bad way to prove the schema applies.
- **A `--retries` wrapper in the harness is the worst possible flake fix**: it
  hides a real race and multiplies runtime. Quarantine, don't retry.
- **"Works on my machine" in this domain is usually one of the eight items
  above.** Do not re-run the test locally hoping for green; reproduce the CI
  environment explicitly (env vars, worker count, timezone, image digests).
- A test that starts a server and never kills it will occupy the port for the
  *next* run. A `teardown` that can fail is not a teardown — wrap cleanup so it
  cannot raise.

## Files

- `references/local-vs-ci-divergence.md` — the full symptom → cause table for
  "passes locally, fails in CI", plus container-network and healthcheck details.
  Read it when a test fails only in CI and the checklist above has not explained
  it.

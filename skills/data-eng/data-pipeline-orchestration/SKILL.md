---
name: data-pipeline-orchestration
description: Design and operate scheduled data jobs on Airflow, Dagster, Prefect or Temporal - DAG structure, task granularity, idempotency, backfills, sensors, retries with backoff, and the rule that the orchestrator schedules code rather than containing it. Use when a user is building or fixing a DAG, when a task is flaky or a retry loop is hiding a bug, when a backfill is stuck or destructive, or when moving a pipeline between orchestrators. Triggers on "Airflow", "DAG", "scheduler", "sensors", "backfill", "Dagster", "Prefect", "Temporal", "workflow orchestrator", "cron job failed".
compatibility: Examples use Apache Airflow 2.7+/3.x, Dagster 1.5+, Prefect 2/3, and Temporal; verify provider and flag names per version.
metadata:
  version: "1.0"
---

# Data Pipeline Orchestration

The orchestrator decides **when** code runs and **what to do when it fails**.
It is not where data logic lives. The output is a DAG with retries, timeouts,
owners, and a backfill procedure — not a Python file that happens to run on a
cron.

## Choose the orchestrator

| Tool | Model | Pick when |
|---|---|---|
| Airflow | Batch DAGs, tasks, sensors, XCom | Many independent batch jobs, Python shop, big existing DAG estate |
| Dagster | Data assets, software-defined assets, partitions, sensors | Tests-first, typed assets, a team that wants asset lineage over task lineage |
| Prefect 2/3 | Flows + tasks, dynamic orchestration | Python-first, small-to-medium, fast to stand up |
| Temporal | Durable execution, long-running stateful workflows | Steps span minutes-to-days, involve human waits, retries must survive a deploy |

Default: match the tool already deployed. Migrating because of one bad DAG is
almost never worth it.

## The rule: no business logic in the orchestrator

The orchestrator is a scheduler, a dependency graph, a retry policy, and an
alert. The data logic — SQL, transforms, API calls — lives in ordinary tested
code with its own tests, imported by the DAG.

- If you cannot unit-test a task by importing a function, the logic is stuck
  inside the DAG and will drift.
- A DAG file should read as a *list of steps* ("extract → validate → load"),
  each a one-liner calling a function. If a task body is 80 lines, it is a
  library function that belongs in a package.
- Airflow `@task` decorated Python is still tested with a normal `@pytest`
  fixture; make sure the decorated function is thin.

## DAG design

**Structure the DAG by what depends on what, not by what runs at what time.**
Airflow has no built-in data intervals you can rely on for correctness; pass
the interval explicitly (`{{ data_interval_start }}` / `{{ ds }}`) and never
`now()` inside a task.

- **One logical unit per task.** "Load yesterday's orders" is a task. "Load,
  dedupe, join dimensions, write, and email" is four tasks, so a failure retries
  only what failed and the alert names the step.
- **Fan-out over fan-in where the work is independent.** 200 files → 200
  dynamic mapped tasks (Airflow 2.3+ `expand()`, Dagster `map_asset`,
  Prefect `.map`, Temporal child workflows). Do not loop inside one task
  because then there is no per-item retry and no per-item visibility.
- **Set `depends_on_past` only when a task genuinely reads its own previous
  run's output** (a slowly-changing aggregate). It silently serialises the DAG
  and turns one failed run into an infinite backlog.
- **Retries belong to the failure mode.** A transient network call: retry with
  backoff. A non-idempotent write, or a real data error: fail loudly and let a
  human look. Retrying a deterministic bug 5 times just delays the alert by
  20 minutes.

**Every task needs:** a timeout (so a hung task doesn't hold a worker
forever), a retry policy with backoff, and a bounded `max_active_runs` or
concurrency so a backfill cannot swamp a shared resource.

## Idempotency is the property that makes everything else work

Retries, backfills, re-runs, and catch-up all mean **the same code runs twice
on the same data**. Every task must be safe to run twice.

- **Write to a partition or a merge target, not append blindly.** An
  `INSERT` re-run duplicates rows; a `MERGE`/`INSERT ... ON CONFLICT
  DO UPDATE` or an overwrite of the partition is idempotent. Name the
  partition by the run's data interval, not by `now()`.
- **Upserts need a deterministic key.** A surrogate `uuid5` from the natural
  key, or the source's own id. A `uuid4` generated in the task makes a retry
  a duplicate.
- **External side effects are the hard case.** Publishing to a queue, calling
  a payments API, writing a file another system reads: these are not
  idempotent. Two options — make the target idempotent (a dedupe key the
  consumer checks), or move the effect behind a transactional outbox and let a
  separate step publish exactly-once-ish. Never rely on "the retry will
  probably see it".
- **Test it:** run the task twice in a test environment and assert the target
  state is identical. If it isn't, the task is not idempotent, and every
  guarantee above quietly evaporates.

## Backfills

A backfill re-runs the DAG over a date range. It is the operation most likely
to take down a pipeline, so it gets a procedure, not an ad-hoc click.

- **Backfill the same code path as production.** A "temporary backfill script"
  that is never run again is where silent corruption comes from. The only
  difference should be the date range.
- **Estimate before running**: number of days × per-day cost × downstream
  dependency fan-out. A 2-year backfill that re-runs 500 downstream tasks per
  day is 365,000 task runs and will not finish.
- **Throttle it.** Cap `max_active_runs`, cap the date range per invocation,
  and prefer many small backfill invocations over one large one. Both Airflow
  (`dagrun.conf`, `max_active_runs`) and Dagster (partition backfill with a
  concurrency limit) support this.
- **Know what the backfill will clobber.** If a task writes into a table that
  today's run also writes, the backfill and the live run race. Pick one:
  isolate the backfill into a separate target (a `backfill_` table, a different
  branch), or pause the live DAG for the duration.
- **Airflow 2 (`airflow dags backfill <dag_id> -s <start> -e <end>`)** is
  deprecated; use `airflow dags backfill` on Airflow 3 or the UI/API
  `POST /dags/{dag_id}/dagRuns` with a `logical_date` and the Airflow UI's
  "Create Dag Run" with a start date, or run a loop of `dagrun`s yourself.
  Verify the exact subcommand for your version with `airflow dags backfill
  --help`. The `BashOperator` + `airflow dags trigger` loop is the most
  portable.
- **Data intervals, not dates.** A daily DAG with a start date and schedule
  `0 2 * * *` has a logical date of the day *before* the run. Backfilling
  "2024-01-01 to 2024-01-31" means 31 runs, and the boundary is off-by-one if
  you assume otherwise.

## Sensors and waiting

A sensor polls a condition so the DAG does not start until its input exists.
Use them sparingly: a sensor in the *scheduled* path converts a missing input
into a silently deferred DAG that nobody notices until a dashboard is empty.

- **`ExternalTaskSensor`** waits for another DAG's run — it must match the
  *same* `execution_date`, or it waits forever. This is the single most common
  Airflow mistake.
- **Deferrable operators** (Airflow 2.2+) and async sensors (Dagster,
  Prefect async) release the worker slot while waiting instead of holding it
  in a sleep loop. Use them for anything that waits more than a few seconds.
- A `reschedule` mode sensor yields the slot and re-checks on the next
  scheduler pass; a `poke` mode sensor holds the worker. Reschedule for long
  waits.
- **A sensor that never resolves is a stuck DAG.** Give every sensor a
  `timeout` and an `exponential_backoff`; the failure should be an alert, not
  an infinite wait.
- Prefer a **trigger or an explicit dependency** (via a shared table, a file
  landing, or a Dagster `RunRequest` from a freshness sensor) over a sensor
  when the producer is under your control.

## Retries and timeouts

```python
# Airflow: bounded retries, exponential backoff, a hard ceiling per try
from airflow.decorators import task, retry

@task(retries=3, retry_delay=timedelta(minutes=5), retry_exponential_backoff=True,
      max_retry_delay=timedelta(hours=1),
      execution_timeout=timedelta(minutes=30),
      # never retry a genuine data error; only retry transient infra
      retry_on_exceptions=(TransientAPIError,))
def load_orders(interval_start, interval_end): ...
```

- `retry_exponential_backoff=True` with `max_retry_delay` is the default you
  want: retry-1 at +5 min, retry-2 at +10, retry-3 at +20, capped.
- **Set `execution_timeout`/`timeout` on every task.** Without it, a task that
  hangs on a network socket holds a worker slot forever and the DAG never
  completes.
- **A retry that always fails is a delayed alert**, so the alert should fire on
  the *final* failure, and the run's log should show the attempt count. Don't
  set `retries=0` for a transient call, and don't set `retries=10` for a
  deterministic bug.
- `max_active_runs=1` on a DAG whose tasks write the same target — otherwise
  two scheduled runs of the same interval overlap and interleave writes.
- Cross-task data movement via **XCom** is for small control data (a count, a
  partition key, a list of ids). XCom is stored in the metadata DB (Airflow);
  putting a dataset through it is a way to make your metadata DB the
  bottleneck. Pass paths or keys and let the tasks read shared storage.

## Airflow gotchas that cost real time

- **DAG files are imported by the scheduler and by every worker.** A
  top-level network call, a slow import, or a `Variable.get()` at module scope
  makes every scheduler parse slow. Do all I/O inside a task. A common import
  mistake is a pandas/DB-driver import that opens a connection at import time.
- **The DAG must be importable without credentials or a live database.** Parse
  errors show as a broken DAG in the UI and are easy to miss in a large list.
- **`execution_date` / `logical_date` is the interval start, not "now".** A
  task that filters `WHERE created_at >= {{ ds }}` and nothing else silently
  double-counts or misses the boundary. Always pass
  `{{ data_interval_start }}` and `{{ data_interval_end }}` explicitly.
- **Catch-up after downtime**: the scheduler creates a run for every missed
  interval, and they all try to run at once. Set `max_active_runs` so they
  drain over time instead of all hitting the warehouse. A `catchup=False` on
  an hourly DAG that was down for a week silently leaves a week of data
  missing — backfill it deliberately.
- **`@task` and `@dag` version differences**: a DAG imported with a different
  Airflow version than the scheduler errors on parse. Pin the version in the
  environment image, not just in `requirements.txt`.
- **The `airflow dags list-import-errors` command** (and
  `airflow dags list-import-errors --output json`) is the fastest way to find
  a DAG that is silently not scheduling. Check it after any DAG edit.
- **XCom size**: a task returning a large list hits the metadata DB and can
  make the whole scheduler crawl. Return a key, not the data.
- **SubDAGs are deprecated**; use dynamic task mapping or a composite
  operator. `SubDagOperator` still exists but is a maintenance trap.
- **Top-level code runs in the scheduler's process** — never open a DB
  connection, an HTTP session, or compute anything expensive at module scope.
- **Airflow's own tables (`dag_run`, `task_instance`, `xcom`, `log`,
  `job`) grow forever.** A DAG that runs every minute with dynamic mapping
  will fill a small metadata Postgres; budget for retention/vacuum.
- **The scheduler is a single point of contention**: many task-heavy DAGs make
  scheduling latency visible ("why did my 2am run start at 2:40?"). This is
  about DAG count and task count per DAG, not about your data.

## Edge cases

- **A task succeeded but wrote nothing** (empty source day). Return a
  row-count/empty marker from the task and let the next task no-op
  explicitly; do not let "0 rows" look like success with a wrong downstream.
- **The upstream arrived late, after the downstream already ran and alerted.**
  Re-run the downstream interval manually once the upstream lands, or use a
  sensor that would have waited. Decide which, and write it down.
- **A run fails on some partitions and succeeds on others** (dynamic
  mapping). Use `max_active_tiled_runs` (Airflow's `map_index` filtering) or
  a per-item retry; the DAG-level success is all-or-nothing and can mislead.
- **A backfill and a live run overlap.** Serialize them (pause the DAG) or
  isolate the target. Decide before you start, not after the data is mixed.

## Gotchas

- **XCom values are capped at 48 KB in Airflow 2.3+.** Returning a list of
  ids, a nested dict, or a DataFrame fails the task at *completion* time with
  an XCom size error — after the expensive work has already run, so every retry
  redoes it. Return a storage path or a partition key. The default pool's 128
  slots is the other classic "why is nothing running" — a 200-tile mapped task
  queues the rest on pool slots, and only a `pool=` override frees them.
- **`airflow dags test <dag_id> <logical_date>` never touches the scheduler.**
  It runs the whole DAG in one process, so it does not exercise
  `max_active_runs`, `depends_on_past`, trigger rules, pool limits, or sensor
  timeouts. A green `dags test` is not evidence the scheduled run works — clear
  a real DagRun and watch the UI. (`airflow tasks test` likewise does not
  record state or send notifications, so its green tick means less still.)
- **`airflow tasks retry` defaults to `--map-index -1`, which retries every
  tile of a mapped task.** Retrying the one failing tile out of 200 re-runs the
  other 199. Pass `-m <index>`; confirm the flag on your version with
  `airflow tasks retry --help`.
- **A backfill creates ordinary DagRuns, so it fires every downstream DAG.** A
  90-day backfill enqueues 90 downstream runs on top of the live ones, and they
  race. Tag the run (`dag_run.run_type`, or an `is_backfill` field in
  `dagrun.conf`) and filter on it downstream, or pause the downstream DAGs for
  the duration. The same framing bug bites live: an extract that reads the
  source in a different timezone than the partition key puts one event in two
  partitions, and the retry then inserts rather than overwrites, so it is
  counted twice. Pin the timezone and the cutover instant.
- **Dagster 1.6+ ships instance migrations, and a worker or webserver on a
  pre-migration instance fails at startup** with an instance/schema error that
  looks like a broken asset, not a migration. Run `dagster instance migrate` as
  an explicit deploy step between versions, before the new version serves.
- **In Dagster, one asset key can have exactly one partition mapping.** A
  partitioned `fct_orders` and an unpartitioned `fct_orders` cannot coexist —
  the second definition raises a conflicting-definition error. Version by
  partition key or by asset name, never by redefining the same name.
- **Prefect 3 removed `allow_failure`; it is `return_failed=True` now,** and
  `allow_failure` in Prefect 2 returned the value on failure while
  `return_failed` returns a failed *state* — not the same behaviour for the
  downstream task. A Prefect 2 snippet in a Prefect 3 deployment also fails at
  *import* with an unexpected-keyword error, not at run time.
- **Prefect 3 does not persist task results unless a result store is
  configured.** The task still succeeds, but `.result()` from another process,
  `.submit()`/`.map()` futures, and `cache_policy="INPUTS"` caching all fail
  or silently miss. Set `PREFECT_RESULTS_STORE` (or a result block) on the
  deployment, not just locally.
- **Temporal workflow code must be deterministic, or the next replay fails.**
  `datetime.now()`, `random`, `uuid4()`, `time.time()`, and iterating a `set` in
  workflow code break the moment a Workflow Task is retried (a worker deploy, a
  worker crash, an Activity timeout all force a replay from the start) and
  surface as a non-deterministic-workflow / command-attributes-mismatch error
  that points at code you did not touch. Move those calls into Activities and
  pass the value in.
- **Temporal workflow history is capped, and an unbounded loop hits the cap
  long after the work is logically done.** The failure is a workflow-task
  history-size-limit error, not a business error, and it does not show up until
  the run is tens of thousands of events old. A workflow that iterates a large
  list must `Continue-As-New` every few thousand activities and pass state
  forward explicitly. Re-running with the same `WorkflowID` while a run is
  still open raises `WorkflowExecutionAlreadyStartedError`; set
  `WorkflowIDReusePolicy` deliberately.
- **Temporal Activity retries are silent and default to unbounded.** The
  default `RetryPolicy` has no `maximum_attempts`, so a deterministic Activity
  failure (a bad SQL string) retries forever while the Workflow shows a healthy
  `WorkflowTaskFailed` loop. Set `maximum_attempts` and
  `non_retryable_error_types` explicitly, and make the Activity idempotent
  because a timeout *does* re-run it.
- **`execution_date` is the Airflow 2 spelling; Airflow 3 exposes it as
  `logical_date`** (REST payload, DagRun field, `BaseOperator` kwarg) and warns
  on the old name. Verify the exact kwarg against your version's provider
  before a 3.x migration — the expensive part is not the DAG, it is every
  place that matches runs by date (`ExternalTaskSensor`, trigger rules, the
  backfill API).

## Files

- None — self-contained. For what a good partition/table layout looks like,
  see `data-lakehouse-layouts`; for idempotent batch vs streaming choices, see
  `batch-vs-streaming-architecture`.

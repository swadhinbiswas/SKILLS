# Local vs CI divergence

Symptom-first table for "passes locally, fails in CI". Work top to bottom; the
first two explain most cases.

## Symptom table

| Symptom | Likely cause | Confirm | Fix |
|---|---|---|---|
| Fails only when the whole suite runs, passes alone | Shared state: module-level cache, singleton, temp file, env var, leaked thread/task | Run with a single worker and reversed order: `pytest -n 0`, then `pytest -p randomly` / reverse the file list | Per-test fixtures constructing fresh state; `afterEach` restore of env and cwd |
| Fails only under `-n 4` | Two tests touch the same row/file/port; no per-worker isolation | `pytest -n 4 -x` locally, and `--dist loadfile` as a bisect | Per-worker database/port/tmpdir; `--dist loadfile`; partition fixtures by worker id |
| Fails only on a 2-core runner with 8 workers | Resource starvation: DB connections, memory, timeouts | Run with the CI worker count *and* a CPU limit (`taskset -c 0,1`) | Cap `-n`, cap pool sizes, raise container memory, raise per-test timeouts |
| `FATAL: sorry, too many clients already` | workers × pool > `max_connections` | Count pools in the harness config | One pool per worker sized `min(5, max_connections/n)`; or one DB per worker |
| Date-boundary failure, late in the evening | Timezone: `TZ=UTC` in CI, local zone elsewhere | `date` on both machines; log `datetime.now().astimezone()` | Set `TZ=UTC` in CI and in the harness; make the app use aware UTC datetimes |
| String sort or `LIKE` order differs | Locale/collation: `LANG=C` in CI, UTF-8 locale locally | `locale` on both; `SELECT x FROM t ORDER BY x LIMIT 5` | Pin the container locale (`--locale=C`); never depend on collation for correctness |
| `EADDRINUSE`, or tests hit the wrong database | Port shared with a dev server or another worker | `ss -ltnp` (Linux) / `lsof -i :5432` (macOS) | Randomise the host port; in compose use service names, never `localhost` |
| Fails only the second run in the same workspace | Leftover volume/tmpdir from a cancelled run | `docker volume ls`, `ls /tmp` | `tmpfs` for data dirs; `down -v` in an `always` step; unique temp dirs |
| Fails only on a dependency bump | Floating transitive version or `:latest` tag | `pip freeze` / `npm ls` diff between the runs | Lockfile in CI; pin image digests |
| Hangs, no output, eventually times out | A test waiting on a readiness signal that never fires; a `sleep` that used to be enough | `pytest --timeout=30` and read the stack dump | Healthchecks + `--wait`; explicit readiness wait in the harness; per-test timeouts |
| Fails on macOS, passes in CI (or the reverse) | Case-insensitive filesystem, path length, `\r\n`, symlink semantics | `ls -b`, `find . -name '*[A-Z]*'` | Use `tmp_path`, not relative paths; normalise newlines; avoid filename case as identity |
| Missing-`FOO` error only in CI | A `.env` file loaded locally | `env \| grep -i foo` locally vs the job env | Assert required env vars in a session fixture so the failure names the variable |
| Only fails on ARM runners (macOS M-series) | Native binary/arch difference, unaligned memory | `uname -m`; the failing dependency's wheel availability | Build wheels for both arches, or run the job on a matching image |
| Intermittent 1-in-20 with no error | Real race: `SELECT` then `UPDATE` without a lock; `sleep` for a worker | Run the file 50× in a loop; add a lock in the harness | `SELECT ... FOR UPDATE`, or make the test single-worker, or use a deterministic barrier |

## Container networking: the two traps

**1. `localhost` inside a compose network is the container, not the host.**
From the test process on the runner, `localhost:5432` is right (that is what
the `ports:` mapping is for). From a sibling container, it is wrong — use the
service name: `postgres:5432`. Getting this backwards produces a suite that
works on a laptop (where the test process is on the host) and fails in CI (where
you moved the test into a container).

**2. `ports:` collisions on a shared runner.** Two jobs publishing `5432` on the
same host collide with a confusing `address already in use` from Postgres, not
from your app. Give each job its own host port:
`ports: ["${PGPORT:-5432}:5432"]` and randomise `PGPORT` per job, or do not
publish at all and run the test process inside the compose network.

## Healthchecks and readiness

A healthcheck is the only reliable readiness signal; `sleep` is a guess that
happens to work on fast machines.

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U postgres -d app_test -q"]
  interval: 1s
  timeout: 3s
  retries: 60            # 60s budget: long enough for a loaded CI runner
  start_period: 5s       # do not count startup time against retries
```

For services without a client you can shell out to, poll from the harness:

```python
def wait_for_port(host: str, port: int, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"{host}:{port} not accepting connections after {timeout}s")
```

A TCP connect succeeding does not mean the service is *ready* (Postgres accepts
connections while still starting up). Poll a real query
(`SELECT 1`, `redis-cli ping`, an HTTP `GET /healthz`) when the port is up too
early.

## Debugging a CI-only failure

1. **Reproduce the environment, not the test.** Same image digest, same env
   vars, same worker count, same `TZ`, same locale:
   `docker run --rm -it -e TZ=UTC -e CI=true --cpus 2 <ci-image-digest> pytest -n 4 -m integration`
2. **Dump the environment** into the log on failure (in an `always` step or via
   a fixture): `uname -a`, `python -V`, `pip freeze | sort`, `date`, `locale`,
   `nproc`, `free -m`, `env | grep -v -i -E 'token|secret|key'`.
3. **Container logs, always.** The application container's stdout is where the
   real error usually is; CI shows you the test's traceback and throws the
   service's log away.
4. **Re-run the shard, not the job.** If the suite is sharded, re-running the
   whole job proves nothing; re-run the shard and the single test.
5. **Bisect the divergence** in this order: worker count (1 vs 4), `TZ`,
   locale, image digest, dependency lockfile, CPU limit. Each is a single
   variable; changing two at a time wastes the run.
6. **Check whether it is a seed.** Rerun with `--randomly-seed=X` /
   `-p no:randomly` to see if the order is the variable, and if so hunt the
   shared state rather than the timing.

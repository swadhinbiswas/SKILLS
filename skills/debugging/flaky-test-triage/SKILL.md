---
name: flaky-test-triage
description: Diagnose and eliminate tests that pass intermittently - reproduce in a loop with a fixed seed, classify the flakiness (timing, ordering, shared state, real network, clock, randomness, resource exhaustion), find the cause, and fix it rather than adding retries. Use when a test fails sometimes, when CI is red for no reason, when a test passes locally but fails in CI, or when a suite needs quarantining. Triggers on "flaky test", "intermittent failure", "works locally", "fails in CI", "test passes on retry", "red herring", "unstable test", "rerun the test", "quarantine".
compatibility: Language-agnostic; concrete commands use pytest and Jest conventions. Verify runner flags with the runner's --help.
metadata:
  version: "1.0"
---

# Flaky Test Triage

A flaky test is worse than a broken one: a broken test is caught, a flaky one
teaches the team to ignore red. The only durable fix is to find the cause. A
retry, a `sleep`, or a quarantine is a labelled debt position, not a fix.

The single most important insight: **a flaky test is always flaky for a
reason, and the reason is almost always a real defect somewhere** — a race, a
hidden global, an un-awaited promise, a leaked resource, a dependency on the
environment. Treating it as a test problem instead of a product problem is how
flaky suites become normal.

## Workflow

- [ ] 1. Classify the flakiness: is the *failure rate* stable and low, or
      high?
- [ ] 2. Reproduce it in a loop, with the seed and environment fixed
- [ ] 3. Capture the failure *output* from a real run — the diff, the stack,
      the assertion values
- [ ] 4. Bisect the flakiness: run alone, in order, in parallel, in CI
- [ ] 5. Identify the shared resource the test does not own
- [ ] 6. Fix the cause (in the test *or* the product — often the product)
- [ ] 7. Verify with a large loop count, then remove the quarantine

## Step 1 — Get the failure rate and the failure signature

Two flakes with the same test name can have different causes. Establish:

- **Rate:** 1 in 50 runs, or 1 in 3? A high rate is usually an ordering or
  environment problem you can find quickly. A very low rate needs a better
  harness (more parallelism, a stress mode) before you can study it.
- **Signature:** does it always fail the *same way*? The assertion values,
  the diff, the exception. A test that fails with a different error each time
  is usually a race or a resource problem, not a logic problem.
- **First failure or recent failure?** Did it start after a specific commit,
  dependency bump, or CI change? `git bisect` the flake itself — see
  `git-bisect-debugging` — using a loop-until-fail script as the test.

## Step 2 — Reproduce in a loop

A flake you cannot reproduce is a story. Build the loop first.

```sh
# bash: run until it fails, stop at the first failure
for i in $(seq 1 500); do
  if ! ./run-one-test.sh >"/tmp/flake/$i.log" 2>&1; then
    echo "FAILED on iteration $i"; break
  fi
done
```

Run it with the environment pinned: fixed timezone, fixed locale, fixed
seed, no other load, single worker if possible. Add load deliberately *later*
— many flakes need concurrency to appear, so reproduce with and without
`--maxfail=1` and with high parallelism.

```bash
# pytest: fixed seed (installed as a plugin in most projects), -p no:randomly
#        to disable random ordering, and -n auto for xdist parallelism
for i in $(seq 1 200); do
  python3 -m pytest tests/test_checkout.py::test_rounding -p no:randomly -q \
    || { echo "FAILED at $i"; break; }
done
```

```bash
# jest: --runInBand for isolation, --seed for a fixed RNG
for i in $(seq 1 200); do
  npx jest tests/checkout.test.js --runInBand || { echo "FAILED at $i"; break; }
done
```

**Log the seed and the run number in the failure output.** The run number is
what lets you compare a CI failure to a local loop: if it fails at run 37 in
CI and at run 12 locally, the environment differs; if both fail around the
same run, it is closer to a pure race.

## Step 3 — Bisect the flakiness

Ask these in order; each one eliminates a class of cause.

| Question | Command / check | If yes |
|---|---|---|
| Does it pass alone? | run just that test, repeatedly | shared state, ordering, or resource exhaustion with other tests |
| Does it fail in the full suite but pass alone? | run the suite with `--lf` / only that file | global state, an earlier test's side effect |
| Does it fail when run twice? | run the same test twice in one process | **not idempotent** — global state, a file on disk, a counter, a port, a database row |
| Does the order matter? | reverse the order, or `--shuffle` | order dependence, leaked global, un-cleaned fixture |
| Does parallelism matter? | `-n auto` / `--maxWorkers=8` vs serial | race, or a shared file/port/database |
| Does it fail only in CI? | compare OS, core count, clock, filesystem, env vars | environment dependence — see below |
| Does it fail only under load? | run the suite while the machine is busy | timing assumption, fixed sleep, no timeout |

Two runs of the same test in one process failing the second time is a
strong, cheap signal: the test is not idempotent. Look for module-level
mutable state, a lazily-initialised singleton, an env var mutated in setup,
a file written to a fixed path, a row inserted with a fixed id.

**The order-dependence bisect**: binary-search the set of tests that run
before the failing one. Run the failing test plus the first half of the
preceding tests; if it fails, the culprit is in that half.

## Step 4 — Classify and fix the cause

### Timing assumptions and fixed sleeps

The most common cause, and the most reliably fixable.

```javascript
// wrong: the assertion races the async work
expect(cache.get(key)).toEqual(value);
```

```javascript
// right: wait for the condition, with a bounded timeout
await waitFor(() => cache.get(key) === value, { timeout: 2000 });
// or await the actual work, or pollUntil with a short interval
```

- **Never `sleep(1000)` in a test.** Replace it with a bounded wait for the
  specific condition.
- **Never rely on an operation being fast.** If the test depends on
  `setTimeout(…, 50)` completing, it depends on an event-loop scheduling
  detail.
- **Use fake clocks for time-based logic.** Most languages have a test double
  for time; a test that sleeps for a "24 hours later" case is a test that
  takes 24 hours or is wrong.
- **Every wait needs a timeout**, so a real hang fails the test rather than
  the CI job.

### Shared state

- **Isolate the filesystem.** Write to a per-test temp directory
  (`tmp_path` in pytest, `os.tmpdir()` plus a unique name), never to a fixed
  path in the repo. A leftover file from a previous run is a classic
  "passes alone, fails in suite" cause.
- **Isolate the database.** A unique schema/database per test run, rolled back
  or dropped at the end. A test that depends on a row inserted by another
  test is order-dependent by construction. A unique id per test
  (`uuid4`) instead of a fixed one.
- **Isolate the port.** Do not hardcode a port; bind to port 0 and read the
  assigned port, or use a per-test port range. Two shards on one CI machine
  collide.
- **Restore what you mutate.** If a test sets `os.environ["MODE"]` or a
  global config object, reset it in teardown — and reset it in `teardown`,
  not at the start of the next test.
- **Do not mutate the thing you are asserting on.** Tests that depend on
  module-level singletons need the singleton reset between tests.

### Un-awaited async work

The most common cause in JavaScript and the one that hides best.

```javascript
// wrong: the promise is never awaited; the assertion runs immediately
it("saves", () => { service.save(doc); expect(store.docs).toHaveLength(1); });
```

- **Every** `async` test needs an `async` body and an `await` on every
  async call. An `async` test that never awaits anything is the smell.
- **Await the thing, not a sleep.** Await the specific future/promise/call.
- **Use the framework's async assertions** (Jest's `waitFor`, Playwright's
  `expect(...).toBeVisible()` which auto-retries, testing-library's
  `findBy*`) rather than a manual loop.
- **Unhandled rejections in a detached task** surface as a failure in a
  *different* test. That is why the failure looks random. Fail the process on
  unhandled rejections in test mode.
- **Drain background work in teardown** — stop the server, wait for the
  queue to empty, close the client — or it leaks into the next test.

### Real network, clock, and randomness

- **Replace real network with a local double in unit tests.** A unit test that
  makes an HTTP call is an integration test that will flake on a transient
  DNS or TLS error. Keep the real thing in a separate integration suite that
  is allowed to be slower and is tagged as such.
- **Freeze the clock.** A test that reads the current time can fail at
  midnight, at a month boundary, or on a DST transition. Inject a clock.
- **Fix the seed.** Every random source in the test path — the test's own
  randomness, and the seed for the language's global RNG — must be set. An
  unseeded shuffle, a random port, a random UUID in an assertion, and a
  `Math.random` in the code under test all produce flakes.
- **Watch for time-of-day and date-dependent logic**: "today's reports",
  end-of-month billing, "last 7 days" boundaries, DST. These fail
  periodically, which is the definition of a flake.

### Resource exhaustion

- **File descriptor exhaustion.** A test that opens a file/socket without
  closing it starves the next ones. `lsof -p` or the framework's
  descriptor-leak detection. In Python, `ResourceWarning` and
  `pytest -W error::ResourceWarning` turn this into a hard failure.
- **Port collisions** — see above.
- **Process/thread limits** and thread pools not shut down between tests.
- **Database connections** not returned to the pool, so a later test
  times out waiting for one. A pool exhaustion flake looks exactly like a
  slow test.

### Environment differences (CI-only flakes)

- **Core count** changes concurrency semantics and timeouts. Anything that
  depends on parallelism must be sized from the available cores, not
  hardcoded.
- **CPU speed** — a timeout tuned on a laptop is a timeout that fails on a
  loaded CI runner. Scale timeouts by the measured environment, or use a
  generous bound plus a condition-based wait.
- **Filesystem** — case sensitivity, `O_EXCL` semantics, rename atomicity,
  and network filesystems (NFS, overlayfs, some container volumes) have
  different behaviour. `os.path.exists("Foo")` on a case-insensitive
  macOS filesystem is a portability bug.
- **Timezone/locale** — string formatting and date parsing.
- **Container limits** — memory and CPU cgroup limits change GC behaviour and
  timeouts.
- **Dependency drift** — a transitive bump, a different base image, a
  different browser/driver version.

Fix by making the test independent of these, or by pinning the environment
(pinned image digest, pinned lockfile). A test whose correctness depends on
being fast enough is not correct.

## Step 5 — What not to do

| Anti-pattern | Why it is wrong |
|---|---|
| `sleep(1000)` before the assertion | replaces a race with a slower race |
| Retry / re-run in CI | hides a real bug and trains people to ignore red |
| `pytest.mark.flaky` / `@RepeatedTest` with retries | same, with extra steps |
| Widening a timeout | if it needs 30s, it is waiting for something |
| Quarantine without an owner and a date | debt with no due date is permanent |
| `assert` removed to make it pass | the test asserted something real |
| Running tests in a fixed order to "fix" them | order dependence is the bug |
| Marking it `@skip` "temporarily" | this is how suites die |

**Retries have one legitimate use**: distinguishing a *flake* from a
*consistent* failure during triage. They are never a fix, and a CI that
auto-retries must report the retry count — a suite that is green because of
retries is a suite whose signal you have lost.

## Quarantine policy

Quarantine is a **time-boxed, owned, tracked** state, not a permanent exile.
It exists so a blocking flake does not stop everyone, while the real fix is
done.

Policy:

1. A flaky test is found (in CI or locally) → **file an issue immediately**,
   with: the test name, the failure rate, a captured failure log, the run
   count and seed that reproduce it.
2. It is quarantined **only if** it blocks everyone and cannot be fixed
   promptly. Not for "annoying" tests.
3. The quarantine entry has: an owner, an issue link, and a **deadline**
   (weeks, not months). It runs on a schedule (nightly, or a separate job)
   so it does not rot unnoticed.
4. The deadline is not extended more than once, without a written reason
   from someone with authority.
5. Quarantine is *removed* when fixed, and the fix ships with a regression
   guard: a loop-based or stress-mode test that would have caught it.

Implementation, pytest:

```python
# pytest.ini / pyproject.toml
markers = ["quarantine: known-flaky, see issue link in the test docstring"]

@pytest.mark.quarantine(reason="https://issues.example/412 - clock race in retry")
def test_retries_with_backoff(): ...
```

```sh
# run everything except quarantine in the main job
python3 -m pytest -m "not quarantine"
# run quarantine separately, on a schedule, reporting but not gating
python3 -m pytest -m quarantine --junitxml=quarantine.xml
```

Jest uses `testPathIgnorePatterns` plus a separate config for quarantined
files, or `describe.skip` with a linked ticket and an owner in a comment.

**Track the ratio.** A suite where more than a few percent of tests are
quarantined has a systemic problem (shared test infrastructure, real-time
dependencies, no test database isolation) and needs that fixed, not more
quarantines.

## CI flake detection

Make flakiness visible rather than papering over it. A pipeline that
auto-retries and reports green is a pipeline that has lost its signal.

- **Record every run's outcome per test**, including attempts. A test that
  failed then passed is a detected flake, and must be reported as such.
- **Fail the build on a detected flake**, or at minimum annotate it and open
  an issue automatically. A build that only goes red on a *persistent*
  failure is a build that reports what it knows and nothing more.
- **Retry at the job level only for infrastructure failure** (the runner
  died, the image pull failed), and count it separately from test failures.
  Never retry the whole suite to cover a flake.
- **Publish a flake dashboard**: flake rate per test over time, and the
  tests with the highest rate. The top of that list is where the shared
  infrastructure is broken.
- **Detect flakiness across runs, not just within one**: a test that fails
  once a week for three months is a flake that CI's own retry logic will
  hide forever. Compare the per-test failure count to the per-test run count
  across a window.
- **Quarantined tests run on a schedule and their results are reported** —
  never silently dropped.
- **Pin the environment**: a lockfile for dependencies, a pinned base image
  digest, a fixed browser/driver version, a fixed timezone and locale
  (`TZ=UTC LC_ALL=C`).

## Gotchas

- **"It passes in CI now" is not a resolution.** Re-run the job a few times
  before believing it.
- **A test that fails only on the *first* run in a fresh environment** is
  usually an ordering or state issue: the second run inherits state from the
  first. Look at what the first run created and did not clean up.
- **Random test ordering plugins are the single most effective flake-finder**
  available (`pytest-randomly`, `--shuffle` in jest). If a suite is
  order-independent, run with shuffling on permanently in CI — it will find
  every hidden global.
- **A test that mutates production-like data is both a flake source and a
  correctness bug.** Its second run sees its own first run.
- **Fixing the flake in the test can hide a product bug.** If the cause is a
  real race in the product, wrapping the call in a retry in the test has
  hidden a defect that will bite a user. Decide explicitly which you are
  fixing, and say so in the commit message.
- **Test doubles that are themselves stateful** (a fake server, an in-memory
  queue) are a common hidden flake source. Reset them in teardown, or make
  them stateless.
- **A suite that has never been observed to fail is not a suite you
  understand.** A mutation-testing pass (mutate the source, expect tests to
  fail) finds tests that assert nothing.
- **Parallelism changes results.** A test that passes serially and fails
  under `-n 8` has a real race. Do not "fix" it by forcing serial execution
  — that hides the bug and makes CI slower.

## Fix checklist

- [ ] Cause identified and written down in the commit message, not just "fix flake"
- [ ] The fix is in the test, or the product, or both — stated explicitly
- [ ] No `sleep`, no retry, no widened timeout left in the test
- [ ] The test is deterministic: fixed seed, frozen clock, no real network
- [ ] The test is idempotent: passes when run twice in the same process
- [ ] The test is isolated: own temp dir, own schema/ids, own port
- [ ] Verified over a long loop (>= 200 runs, or the old failure count x 100)
- [ ] Verified in the CI environment, several consecutive runs
- [ ] Any quarantine removed, with the issue closed
- [ ] A regression guard exists if the cause was in the product

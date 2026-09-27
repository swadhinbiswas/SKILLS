---
name: ci-pipeline-design
description: Design CI pipelines that stay fast and trustworthy - stage ordering for fail-fast feedback, dependency caching, job and matrix parallelism, sharding, flaky-test quarantine, and pulling work from long pipelines to pull requests. Use when a pipeline is slow, when CI costs are too high, when a build is flaky, when deciding what runs on a PR versus on main, or when someone asks why the build takes 40 minutes. Triggers on "CI is slow", "pipeline takes long", "flaky build", "quarantine tests", "matrix strategy", "cache CI", "10 minute build", "CI timeouts".
compatibility: Examples use GitHub Actions YAML; the design principles apply to any CI system. Pin every action to a commit SHA and see the github-actions-hardening skill.
metadata:
  version: "1.0"
---

# CI Pipeline Design

A pipeline's job is to give a developer a trustworthy answer fast. Every design
choice below trades one of those two things — pick knowingly, and never trade
trust for speed.

## The 10-minute rule

**If the answer to "did my change break anything?" takes longer than 10 minutes,
the pipeline is too slow, and engineers will batch changes, ignore red, or push
to `main` to find out.** That is how broken code reaches production.

Split into two gates:

- **The pre-merge gate** (every push to a PR): fast, parallel, under ~10
  minutes, high signal. Lint, type check, unit tests, build the artifact,
  fast integration tests.
- **The post-merge gate** (on merge to main): the slow, exhaustive tier —
  full E2E, soak/load smoke, matrix across OS and runtime versions, coverage,
  publish, deploy to staging. Failures here page a human and block the *next*
  release, not the merge.

If the slow tier must block merging (regulation, a release train), make it
opt-in per PR with a label, and run it in parallel with review. Never make every
PR pay for a full matrix on the critical path.

## Stage ordering: cheapest and most-likely-to-fail first

Order by **(cost × probability of catching the change)**, highest first.

1. **Format/lint check** — seconds, catches a large fraction of PRs.
2. **Type check** — tens of seconds, catches most cross-cutting breakage.
3. **Unit tests** — minutes, no external dependencies, the bulk of signal.
4. **Build the artifact once** — compile/package/container image. Fail here
   before spending minutes on integration tests that need the artifact.
5. **Integration / contract tests** — needs the artifact and dependencies.
6. **E2E** — minutes, slow, low diagnostic value; last before merge.
7. **Publish / deploy** — after every gate, never inside a test job.

Rules:

- **Fail fast within a job**: `set -euo pipefail`, and no `|| true` on
  anything that must pass. A test job that ends green after swallowing a
  failure is worse than a red pipeline, because it is a lie.
- **Run independent jobs in parallel**, not as steps. Steps in one job are
  sequential by construction; if two things do not depend on each other, they
  are two jobs.
- **One job, one responsibility.** A job that lints, tests, builds, and
  publishes cannot be retried or timed meaningfully.
- **Never deploy from a PR job.** Build the artifact in the PR job, publish it
  to a registry, and have the post-merge job *promote that exact digest*.

## Caching

Caching is the largest single lever on pipeline time, and the largest source of
subtle wrongness. The defaults:

- **Cache the dependency download, never the build output.** `node_modules`,
  `.venv`, `~/.m2`, `~/.gradle`, `$GOPATH/pkg/mod`, `~/.cargo/registry`. These
  are derived from a lockfile, so they are safe to restore.
- **Key caches on the lockfile hash**, not on the branch or the commit:
  `key: ${{ runner.os }}-npm-${{ hashFiles('**/package-lock.json') }}`.
- **Save only on success** of the main path, or every failed run poisons the
  cache. Cache poisoning from test results is the dangerous case: **never cache
  test results, coverage, or build outputs that a later job trusts without
  re-verifying them.** See `ci-caching-and-speedup`.

## Parallelism

Three independent knobs:

- **Jobs in a workflow** — free, no downside. Split `unit` / `integration` /
  `e2e` / `build` into separate jobs with `needs:` only where there is a real
  dependency.
- **Matrix** — the same job across OS, runtime version, architecture, or region.
  Use `fail-fast: false` so one cell's failure does not cancel the others and
  you see the whole picture. Cap the matrix; a 3×3×2 matrix is 18 jobs and your
  bill will notice.
- **Sharding** — split one slow test suite across N jobs.
  ```yaml
  strategy:
    matrix:
      shard: [1, 2, 3, 4]
    fail-fast: false
  steps:
    - run: ./scripts/run-shard.sh ${{ matrix.shard }}/4
  ```
  Shard **by file or by test id**, not by a hash of the current time — you must
  be able to reproduce "shard 3 failed" locally. A `pytest -k "…${{ matrix.shard }}"`
  filter that matches on test names is *not* a shard: it produces four very
  unequal jobs. Keep the shards balanced by sorting by known duration (slowest
  file first) rather than round-robin over an alphabetical list. A ready-made
  `run-shard.sh` is in the reference file below.

- **Intra-job parallelism** — `pytest -n auto`, `go test -p`, `cargo test
  --jobs`, Gradle `--parallel`, `npm run test -- --maxWorkers`. Cap it: more
  workers than cores makes a suite *slower* and can exhaust a database's
  connection budget.

## Flaky-test quarantine

A flaky test is worse than a missing test: it teaches the team that red is
noise, and then real failures are ignored too.

Policy:

1. **Detect, don't guess.** Track flakiness from history (a test that has both
   passed and failed on the same commit over N runs). Most CI systems can
   report this; if yours cannot, mark on the spot when someone re-runs.
2. **Quarantine, do not skip silently.** Move the test to a `quarantine` group
   that is reported but does not block, with a tracking issue and a named
   owner. `xfail(strict=True)` for known-broken, `skip` with a reason for
   environment-dependent.
3. **Time-box it.** Quarantined tests are debt with a deadline. Fail the build
   if anything has been quarantined for more than 14 days — that forces the
   fix or the deletion.
4. **Never increase retries to make it green.** A retry that hides a real race
   converts a flaky test into a silent production bug. If a single retry is
   genuinely needed, cap it at 1, mark the test as a known flake, and keep it
   visible.
5. **Fix the cause, and it is usually one of:** shared state between tests,
   real sleeps, wall-clock or timezone dependence, order dependence, a real
   race in production code (unbounded queue, no lock), a resource leak, or an
   over-tight timeout.

## Pulling work to the PR

- **Run the same job definition on the PR and on main.** Two pipelines that
  "usually do the same thing" drift, and the drift is where you get burned
  ("works in CI on main, fails on my PR").
- **Cut the expensive tier from the PR, not the cheap tier.** Keep lint, types,
  unit, and build on the PR; move the matrix, the full E2E, and the load smoke
  to post-merge.
- **Prefetch what the post-merge job will need**: build the container image in
  the PR job and push it, then have post-merge promote that digest. The post-merge
  job then does not rebuild.
- **Use the PR as the only place the gate runs.** Branch protection with
  "require status checks" and the pre-merge job names explicitly listed — and
  *nothing else* required, or contributors will learn to bypass it.

## Reliability rules

- **`timeout-minutes` on every job.** A hung test must not hold a runner for
  six hours. Pick a number ~2× the expected duration.
- **Pin the runtime and the action versions.** `ubuntu-latest` moves; a build
  that passes today and fails next month because the image changed is the
  definition of unreliable. Prefer `ubuntu-24.04` over `ubuntu-latest`, and pin
  actions to SHAs.
- **Fail on infrastructure errors, retry only those.** A network blip fetching
  from npm should retry the *fetch*, not the test. Distinguish them: pull
  dependencies in their own step with a small retry, and let the test step fail
  hard.
- **Make the pipeline hermetic** where you can: fixed dependency versions,
  no reliance on the runner's global state, no `curl … | bash` of a moving
  tag. See `reproducible-builds`.
- **Log enough to debug a failure without re-running**: versions, resolved
  dependency tree, and the actual test names that failed (not just the count).

## Gotchas

- **Caching a test-result artifact and asserting on it later** is the classic
  "green" that is not green: the earlier run used different code. Key every
  restored result cache to the exact source hash, or re-run.
- **`--depth: 0` on a big repo is slow; `--depth: 1` breaks anything needing
  history** (changelog generation, diffing against the base, `git bisect` in
  CI). Fetch only the refs you need with `fetch-depth: 0` plus
  `fetch-tags: true`, or a targeted `git fetch origin <sha>`.
- **Matrix `fail-fast: true` (the default) cancels sibling jobs** and hides
  whether the failure is systemic. Set `fail-fast: false` when diagnosing.
- **`concurrency` groups cancel in-progress runs.** That is usually right for
  the same branch (save minutes) and wrong for the default branch:
  ```yaml
  concurrency:
    group: ${{ github.workflow }}-${{ github.ref }}
    cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}
  ```
- **A test that needs a fixed port will collide across parallel jobs.** The
  failure looks like a product bug. Randomise ports or use containers with
  internal networking.
- **The suite passing locally and failing in CI is usually parallelism,
  timezone, or a floating dependency version** — see
  `integration-test-harnesses`.
- **Renaming a required status check in the workflow** silently stops the branch
  protection from requiring it. After renaming, update branch protection in the
  same PR.
- **A workflow that runs on `pull_request` *and* `pull_request_target`** can
  run the same job twice with different privileges. Understand which is which
  before adding one.
- **Caching with a `restore-keys` prefix that is too broad** hands you a cache
  from an unrelated lockfile; it will be restored, then the install step will
  fix it, and you have paid the full download anyway plus a large upload.
- **Speed achieved by deleting the slow tier is not speed.** If the slow tier is
  gone, either it moved to post-merge (say so) or it was deleted (that is a
  coverage loss, and someone must have decided that, not you).

## Files

- `references/pipeline-recipes.md` — full workflow skeletons: fast pre-merge
  gate, sharded suite, matrix with caching, quarantine handling, and
  post-merge/publish. Read it when writing a workflow file from scratch or when
  splitting an existing slow pipeline.

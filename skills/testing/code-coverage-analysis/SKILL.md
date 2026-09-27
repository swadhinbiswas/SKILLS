---
name: code-coverage-analysis
description: Use code coverage as a diagnostic signal rather than a target - line vs branch vs integration coverage, what a coverage number hides, choosing between CI reporting and PR gating, and finding the untested critical paths that actually ship bugs. Use when a suite is slow, when a PR is blocked by a coverage drop, when deciding what to test next, or when someone asks "what is our test coverage" or "why did this pass at 95%". Triggers on "coverage", "code coverage", "lcov", "coverage report", "mutation testing", "untested code", "coverage gate", "test gap".
compatibility: Examples use coverage.py/pytest-cov, Jest/Istanbul, and go test -cover. Verify the exact flags with `--help`; option names differ across toolchains and versions.
metadata:
  version: "1.0"
---

# Code Coverage Analysis

Coverage answers one narrow question — *which lines did the suite execute* —
and is routinely asked a different one — *is this code correct*. Use it to find
gaps and to find tests that assert nothing. Never use it as a score.

## Measure the right thing

| Metric | What it measures | Use for | Do not |
|---|---|---|---|
| Line | Lines executed at least once | Finding dead code and obviously untested modules | Treat as a quality number |
| Branch | Each `if`/else, each short-circuit operand, each switch case, each loop entry/exit | Finding untested error paths — the ones that matter | Expect 100% |
| Function | Functions called | Finding never-invoked entry points | Trust alone (a call is not an assertion) |
| Path/mutation | Whether the tests detect a deliberate defect | Validating that the suite has teeth | Expect to reach 100% |
| Integration/system coverage | Which *user-visible* behaviours have a test that exercises the real wiring | Prioritising work by risk | Substitute for line coverage |

Start from line coverage to locate modules, then switch to branch coverage,
then to mutation testing to check the tests themselves.

## Commands

```bash
# Python
pytest --cov=src --cov-branch --cov-report=term-missing --cov-report=html:htmlcov
coverage report -m --skip-covered          # missing lines per file
coverage json -o coverage.json              # for diffing in CI
coverage combine coverage.*                 # merge parallel-worker data
coverage report --fail-under=80             # non-zero exit below the threshold

# TypeScript / JavaScript (Jest)
npx jest --coverage --coverageReporters=text --coverageReporters=json-summary
npx jest --coverage --coverageThreshold='{"global":{"branches":70}}'
npx jest --coverage --collectCoverageFrom='src/**/*.{ts,tsx}' \
         --collectCoverageFrom='!src/**/*.d.ts' --collectCoverageFrom='!src/**/index.ts'

# Go
go test ./... -coverprofile=cover.out -covermode=atomic
go tool cover -func=cover.out
go tool cover -html=cover.out -o cover.html
```

Go's `-covermode=atomic` is required for parallel or concurrent test runs;
without it the counts race and the numbers are wrong (or it panics with "cover
mode is atomic, but -test.covermode was set to count").

## What a coverage number hides

- **Executed ≠ asserted.** A test that calls a function and checks nothing else
  executes every line in it and asserts nothing. This is the single biggest
  blind spot: coverage can be 95% on a suite that would pass if every function
  returned `None`.
- **Line coverage misses short-circuits.** `a and b` on one line is one line;
  covering `a` and never `b` shows as covered. Branch coverage catches it —
  this is where error paths hide.
- **`__init__`, `__repr__`, dataclass-generated code, and framework plumbing**
  inflate numbers. Exclude generated and trivial files or the mean is
  meaningless: exclude `**/*.d.ts`, `**/migrations/**`, `**/generated/**`,
  `# pragma: no cover` on defensively-raising branches.
- **Error paths are systematically untested** and are systematically where
  incidents come from. A file at 95% line coverage with every `except` block
  uncovered is a 60% tested file.
- **Averages hide the file that matters.** One 100%-covered utility can lift a
  repo mean while the payment module sits at 20%. Always gate on
  **per-file or per-directory** thresholds with a "no new uncovered code" rule,
  not on a global average.
- **Coverage says nothing about assertion quality, isolation, flakiness, or
  speed.** A fast suite of weak tests beats a slow suite of strong tests
  operationally and loses on bugs.
- **Coverage of `main`** measures the last deploy's test run, not the PR. It
  is useful for finding gaps nobody has looked at; it is useless for gating.
- **Generated code, migrations, and adapters that are pure pass-through** are
  cheap to cover and worthless to cover. Coverage should be *weighted by risk*,
  not by line count.

## The only question coverage answers well

"Which behaviours does a human user rely on that no test exercises?" Build the
list from the product, not from the report:

1. **Inventory the critical paths** (authn/authz, payments, data loss, PII,
   migrations, anything with a rollback). Ask: if this is wrong, what happens?
2. **Map each to the test that covers it**, and record *what it asserts*. A test
   that only asserts a 200 does not cover the path.
3. **Cross-reference with coverage** — uncovered *and* critical is the work
   queue. Covered *and* asserted weakly is the hidden queue.
4. **Prefer the behaviours nobody tested** over the lines nobody covered. A
   90%-covered `parse_invoice` is less urgent than a 40%-covered `refund`.

```python
# A behaviour-level inventory, checked in. Cheap, and it makes coverage actionable.
CRITICAL_PATHS = [
    "login rejects an expired session",
    "admin cannot see another tenant's data",
    "refund is idempotent when the provider times out",
    "migration 0042 applies to a database with 10M rows",
    "webhook signature mismatch is rejected",
]
# Each entry links to the test that proves it. No link = not covered, by definition.
```

## Gating: report always, block narrowly

The house default: **coverage is reported on every PR, and it blocks only on
regression in the changed files.** Global percentage gates create a game —
developers write assertion-free tests to move a number, and unrelated code makes
every PR look like a drop.

| Policy | When |
|---|---|
| Report only, never block | Early project; the team is still learning what matters |
| **No new uncovered lines in changed files** (diff coverage) | The default once a suite exists. Blocks only code you just wrote |
| Per-directory threshold with ratchet (never decreases) | Mature suites; the ratchet prevents slow decay while allowing improvement |
| Hard global threshold (e.g. 80%) | Legacy suites, or contractual requirements. Expect to freeze rather than test |

Diff coverage is what you actually want: a changed file's uncovered lines fail
the build, everything else is informational.

```yaml
# In CI, after the test step: fail only on new uncovered lines in the diff.
- name: Tests (with coverage)
  run: pytest --cov=src --cov-branch --cov-report=xml --cov-report=term-missing

- name: Diff coverage gate (new uncovered lines in the PR)
  run: |
    set -euo pipefail
    git fetch --no-tags --depth=1 origin "$PR_BASE_SHA"
    npx --yes diff-cover coverage.xml --compare-branch="origin/$PR_BASE_BRANCH" \
        --fail-under=100 --fail-on-error
```

`diff-cover` and its `diff-quality` linter do this for Python XML reports;
`jest-diff` does it for Jest. Verify flags with `--help`.

If your tool does not support diff coverage, the fallback is per-file
thresholds in the config plus a review rule: "if you changed a line, either
cover it or say why in the PR." A `# pragma: no cover` with a comment is an
accepted, reviewable escape; a silent uncovered line is not.

## Reading the report to find work

- **Sort by uncovered lines, not by percentage.** One file at 20% with 200
  uncovered lines beats twenty files at 80% with 4 each.
- **Look for the shapes that mean "no tests exist"**: a class/module at 0%, a
  directory with no test file beside it, a new feature PR that added source and
  no test file.
- **Look for the shape that means "tests exist but assert nothing"**: a
  function at 100% with a single `assert result is not None`. Search for
  assertion-free tests directly — that is a better use of your time than any
  report.
- **`--cov-report=html:htmlcov` and read the red files** when triaging a
  codebase you have not seen. The colour map beats any summary.
- **Re-run coverage on the oldest, least-refactored module first.** Legacy code
  with 5% coverage is where the untested incident will come from.

## Validating that the tests have teeth

Coverage measures execution; only mutation testing measures detection. Change
the code on purpose and see if anything fails.

| Tool | Language | Notes |
|---|---|---|
| `mutmut` | Python | Simple; `mutmut run`, review surviving mutants with `mutmut show` |
| `cosmic-ray` | Python | Parallel, Docker-based, good for CI |
| Stryker Mutator | JS/TS | `npx stryker run`; has an incremental mode |
| `go-mutesting` / `mutest` | Go | The ecosystem default |

```bash
# Python, quick local pass
mutmut run
mutmut results          # surviving mutants = untested behaviour
mutmut show             # the diff for each survivor
```

A surviving mutant is a concrete, actionable gap with a specific line. Ten
survivors in the payment module is a better work queue than a coverage number.
A high mutation score and a low coverage number together mean: the suite is
small but sharp — keep it and add volume. High coverage and a low mutation
score means: the suite executes code and does not check it — the tests are
decoration and the priority is rewriting them, not adding more.

## Gotchas

- **Coverage tools in parallel runs produce fragmented data.** With pytest-xdist,
  use `--cov` on the whole run (it handles combining) or `coverage combine`
  after `coverage run --parallel-mode`. Otherwise the report shows only the
  workers' own files and the number jumps around.
- **Coverage of subprocesses or background workers is invisible** unless you
  enable subprocess support (`COVERAGE_PROCESS_START` plus
  `coverage.process_startup()` for Python). Async consumers and cron jobs are
  the usual "0% covered" files that are actually exercised in production.
- **Jest's default `collectCoverageFrom` only covers files touched by tests**
  unless you set it explicitly — so a brand-new untested file does not appear
  in the report at all and the number goes *up* when you add untested code.
  Set `collectCoverageFrom: ['src/**/*.{ts,tsx}']`.
- **Babel/plugin source maps misalign Istanbul with the source**, so a "covered"
  line may not be the line you think, and ts-jest/swc need their own config.
  If the HTML report looks wrong, suspect source maps before suspecting the tests.
- **Coverage thresholds in the config override the CI flag** in some tools, and
  a stale `coverage/` directory from a previous run is merged into the next
  report. Clean the output directory before each run.
- **A threshold drop of 0.1% fails the build and gets the threshold removed.**
  Set thresholds on a ratchet (non-decreasing) or on diffs, or you will delete
  the gate within a month.
- **Coverage never goes down when you delete tests that only ran on deleted
  code** — but it does when you delete a file that other tests imported. Expect
  confusing jumps after a refactor and check the report per file, not the total.
- **`# pragma: no cover` is a way to delete a gate.** Require a comment
  explaining why, and review it like any other code.
- **High coverage in the wrong layer is worse than no coverage**: 95% line
  coverage from E2E tests that take 20 minutes and tell you only "checkout is
  broken" is a tax, not safety. Coverage should be dominated by fast unit and
  integration tests.
- **Do not chase 100%.** It requires testing getters, framework adapters, and
  unreachable defensive branches, and it converts the test suite into a
  liability. 100% branch coverage on a codebase with no mutation testing is a
  number, not a guarantee.

## Files

- `references/tooling.md` — config snippets for the major toolchains
  (coverage.py, Jest/Istanbul, go test, JaCoCo/Gradle, OpenCover, cargo-tarpaulin,
  c8), diff-coverage wiring, and CI reporting. Read it when wiring coverage into
  a specific build system.

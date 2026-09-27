# Coverage tooling by ecosystem

Configuration snippets for wiring coverage into a build. Flag names move between
versions — check `--help` for the tool in your repo before trusting a line here.

## Python: coverage.py

```toml
# pyproject.toml
[tool.coverage.run]
branch = true
source = ["src"]
parallel = true                 # safe for xdist / multi-process runs
omit = [
  "*/migrations/*",
  "*/generated/*",
  "*/__main__.py",
  "*/conftest.py",
  "*/tests/*",
]
# Subprocess coverage (workers, CLI entrypoints launched as subprocesses)
# Requires: COVERAGE_PROCESS_START=<abs path to .coveragerc/pyproject dir>
#            and `coverage.process_startup()` at the top of the entrypoint.

[tool.coverage.report]
skip_covered = true
show_missing = true
exclude_lines = [
  "pragma: no cover",
  "if TYPE_CHECKING:",
  "raise NotImplementedError",
  "if __name__ == .__main__.:",
]
# Per-file floors, applied only to files above MIN_FILE_LINES so tiny helper
# modules do not freeze the build.
[tool.coverage.report.fail_under]
# numeric global floor:
fail_under = 0

[tool.coverage.json]
output = "coverage.json"
```

```bash
# Local
pytest --cov --cov-branch --cov-report=term-missing --cov-report=html:htmlcov
# Parallel runs: let pytest-cov combine, or do it manually
coverage run --parallel-mode -m pytest -n 4
coverage combine
coverage report -m
# Enforce a floor
coverage report --fail-under=80
```

Subprocess coverage in `conftest.py` (needed when the code under test spawns
workers, or an app is launched as `python -m pkg`):

```python
# conftest.py
import os, coverage
if os.environ.get("COVERAGE_PROCESS_START"):
    coverage.process_startup()
```

## TypeScript / JavaScript: Jest + Istanbul

```js
// jest.config.js
module.exports = {
  collectCoverage: true,
  collectCoverageFrom: [
    'src/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
    '!src/**/index.ts',
    '!src/**/generated/**',
  ],
  coverageReporters: ['text', 'html', 'lcov', 'json-summary', 'cobertura'],
  coverageThreshold: {
    global: { branches: 70, functions: 80, lines: 80, statements: 80 },
    // Per-path thresholds beat a global average.
    './src/payments/': { branches: 85, lines: 90 },
  },
  // Essential when a test may call process.exit or leave handles open.
  coverageReportOnFailure: true,
};
```

Vitest uses the same `coverage` options via `defineConfig({ test: { coverage: {
provider: 'v8', reporter: ['text', 'html', 'lcovonly'], thresholds: {...} } } })`.
The `v8` provider is native and fast but maps to V8 coverage, which can differ
from Istanbul's instrumentation in how it attributes unexecuted branches; if a
number moves unexpectedly after switching providers, that is why.

`coverageReportOnFailure: true` (and its `coverageThreshold` companions) is what
makes a failing PR show you the report — otherwise you only learn you dropped
coverage by re-running locally.

## Go

```bash
go test ./... -coverprofile=cover.out -covermode=atomic
go tool cover -func=cover.out | sort -k3 -n | head -40   # lowest-coverage funcs
go tool cover -html=cover.out -o cover.html
```

```go
// go test only reports per-package coverage by default. For a merged report,
// cover the whole module in one run and use -coverpkg for cross-package data.
func TestAll(t *testing.T) { ... }   // single package that imports everything
```

```bash
go test -coverpkg=./... -coverprofile=cover.out ./...
```

`-covermode=atomic` is mandatory with `t.Parallel()` or the race detector; Go
panics with "cover mode is atomic, but -test.covermode was set to count"
otherwise.

## Java: JaCoCo (Gradle / Maven)

```kotlin
// build.gradle.kts
plugins { jacoco }
jacoco {
  toolVersion = "0.8.12"   // must be new enough for your JDK's class file version
  testCoverageVerification {
    violationRules {
      rule {
        limit { counter = "BRANCH"; value = BigDecimal("0.70") }
        element = "BUNDLE"
        excludes = listOf("**/generated/**", "**/dto/**")
      }
      rule {
        limit { counter = "LINE"; value = BigDecimal("0.80") }
        // New code must be covered: this is JaCoCo's diff coverage.
        element = "CLASS"
        excludes = listOf("**/*Config*")
      }
    }
  }
}
tasks.test { finalizedBy(tasks.jacocoTestReport) }
tasks.jacocoTestReport { reports { xml.required.set(true); html.required.set(true) } }
```

JaCoCo's `CLASS` element limit applies per class, which is the closest built-in
equivalent of per-file gating — much better than the `BUNDLE` (whole-project)
average.

## .NET: OpenCover / coverlet

```xml
<!-- coverlet.msbuild, in the test project -->
<PropertyGroup>
  <CollectCoverage>true</CollectCoverage>
  <CoverletOutputFormat>cobertura,opencover</CoverletOutputFormat>
  <Threshold>80</Threshold>
  <ThresholdType>line</ThresholdType>
  <ThresholdStat>total</ThresholdStat>
  <ExcludeByFile>**/Migrations/**</ExcludeByFile>
</PropertyGroup>
```

```bash
dotnet test --collect:"XPlat Code Coverage"
reportgenerator -reports:coverage.cobertura.xml -targetdir:coveragereport
```

## Rust: cargo-tarpaulin

```toml
# .cargo/config.toml or CI args
[tool.tarpaulin]
run-types = ["Tests"]     # omit "Doctests" for speed
coverage = true
report = ["Html", "Xml", "Lcov"]
fail-under = 80
exclude-files = ["src/main.rs", "src/generated/*"]
```

```bash
cargo tarpaulin --fail-under 80 --out Html --out Xml
```

## Diff coverage: the gate that does not create a game

```bash
# Python
pytest --cov --cov-branch --cov-report=xml
npx --yes diff-cover coverage.xml --compare-branch=origin/main --fail-under=100

# JS/TS
npx jest --coverage --coverageReporters=json-summary
npx jest-diff --compare-with=main --coverage-threshold=100
```

`--fail-under=100` here means *100% of the lines you changed in this PR are
covered* — not 100% of the repo. That is the distinction that makes the gate
survivable.

Post the report on the PR regardless of pass/fail, with a per-file table. The
table is what makes coverage actionable; the exit code is what makes it a gate.

## Reporting

```bash
# Codecov / Coveralls style uploads: coverage report + test results
# The point of uploading both is the *diff* view: this PR added tests, and
# here is what they now cover.
```

Keep a `coverage/` output directory out of the repo (add to `.gitignore`) and
upload the report as a CI artifact with a retention of a week or two, so a
reviewer can open the HTML months later.

# Pipeline recipes

Workflow skeletons. Placeholder `<sha>` values must be replaced with real
40-character commit SHAs (see `github-actions-hardening` for how to resolve
them) before use. `runs-on: ubuntu-24.04` is pinned rather than `latest`.

## Fast pre-merge gate (target: under 10 minutes)

```yaml
name: pr
on:
  pull_request:
  push:
    branches: [main]
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}

permissions:
  contents: read

env:
  NODE_VERSION: '22'

jobs:
  checks:
    # Lint + types together: both are fast and both are static. One job, because
    # a lint job and a type job that both take 40s are two round trips of latency.
    name: lint and types
    runs-on: ubuntu-24.04
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/setup-node@<sha>
        with: { node-version: ${{ env.NODE_VERSION }}, cache: 'npm' }
      - run: npm ci
      - run: npm run lint
      - run: npm run typecheck

  unit:
    name: unit tests
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/setup-node@<sha>
        with: { node-version: ${{ env.NODE_VERSION }}, cache: 'npm' }
      - run: npm ci
      - run: npm test -- --maxWorkers=50% --reporter=dot

  build:
    name: build image
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    # Push only on main; on a PR build but do not publish. The post-merge job
    # promotes this exact digest rather than rebuilding.
    steps:
      - uses: actions/checkout@<sha>
      - run: docker buildx build --tag ghcr.io/${{ github.repository }}:${{ github.sha }} .

  integration:
    name: integration tests
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    needs: [unit, build]      # real dependencies only: the suite and the artifact
    services:
      postgres:
        image: postgres:16-alpine
        env: { POSTGRES_PASSWORD: test, POSTGRES_DB: app_test }
        ports: ['5432:5432']
        options: >-
          --health-cmd "pg_isready -U postgres -d app_test"
          --health-interval 2s --health-timeout 3s --health-retries 30
    steps:
      - uses: actions/checkout@<sha>
      - run: npm ci
      - run: npm run test:integration
        env:
          TEST_DATABASE_URL: postgres://postgres:test@localhost:5432/app_test
          TZ: UTC
```

## Sharded slow suite

Shard by a stable key so "shard 3 failed" is reproducible. Splitting by file
list, sorted by known duration (slowest first), keeps shards balanced.

```bash
#!/usr/bin/env bash
# scripts/run-shard.sh <shard 1-based> <num shards>
set -euo pipefail
shard="$1"; total="$2"
# Slowest first, then round-robin: keeps each shard near-equal time.
mapfile -t files < <(ls -S tests/e2e/*.spec.ts)
selected=()
for i in "${!files[@]}"; do
  (( i % total == shard - 1 )) && selected+=("${files[$i]}")
done
printf 'shard %s/%s: %s files\n' "$shard" "$total" "${#selected[@]}"
npx playwright test "${selected[@]}"
```

```yaml
  e2e:
    strategy:
      fail-fast: false
      matrix:
        shard: [1, 2, 3, 4]
    steps:
      - uses: actions/checkout@<sha>
      - run: npm ci
      - run: ./scripts/run-shard.sh ${{ matrix.shard }} 4
      - uses: actions/upload-artifact@<sha>
        if: failure()
        with: { name: traces-${{ matrix.shard }}, path: test-results/, retention-days: 7 }
```

## Matrix across runtimes

```yaml
  test:
    strategy:
      fail-fast: false          # see all cells, not just the first failure
      matrix:
        os: [ubuntu-24.04, macos-15]
        node: ['20', '22']
        include:
          - os: ubuntu-24.04
            node: '22'
            coverage: true       # coverage collected on exactly one cell
    name: ${{ matrix.os }} / node ${{ matrix.node }}
    runs-on: ${{ matrix.os }}
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/setup-node@<sha>
        with: { node-version: ${{ matrix.node }} }
      - run: npm ci
      - run: npm test
        if: ${{ !matrix.coverage }}
      - run: npm run test:coverage
        if: ${{ matrix.coverage }}
```

Coverage on one cell only, uploaded once — collecting on every cell and merging
is a source of double-counted lines.

## Quarantine

```python
# tests/conftest.py
def pytest_collection_modifyitems(config, items):
    """Quarantined tests run and are reported, but cannot fail the gate."""
    quarantined = []
    for item in items:
        marker = item.get_closest_marker("quarantine")
        if marker and not config.getoption("--include-quarantine"):
            quarantined.append(item.itemid)
            item.add_marker(pytest.mark.xfail(
                reason=f"quarantined: {marker.args[0] if marker.args else 'see issue'}",
                strict=True,          # if it starts passing, the build fails
            ))
    if quarantined:
        terminal = config.pluginmanager.getplugin("terminalreporter")
        terminal.write_sep("=", f"QUARANTINED ({len(quarantined)}):")
        for i in quarantined:
            terminal.write_line(f"  {i}")
        Path("quarantine.txt").write_text("\n".join(quarantined))

def pytest_addoption(parser):
    parser.addoption("--include-quarantine", action="store_true",
                     help="run quarantined tests for real (used on a schedule)")
```

```yaml
      - run: pytest --cov --cov-branch --cov-report=xml
      - uses: actions/upload-artifact@<sha>
        if: always()
        with: { name: quarantine, path: quarantine.txt }
      # Debt with a deadline: fail if anything has been quarantined too long.
      - name: quarantine is not accumulating
        run: |
          set -euo pipefail
          if [ -s quarantine.txt ]; then
            echo "::warning::quarantined tests:"; cat quarantine.txt
          fi
```

The `strict=True` matters: a quarantined test that starts passing should fail
the build until the marker is removed, so quarantine cannot become a graveyard.

## Post-merge: publish and deploy the artifact built in the PR

```yaml
name: main
on:
  push:
    branches: [main]
permissions:
  contents: read
  packages: write
  id-token: write        # only if the registry/deploy uses OIDC, not a token
jobs:
  publish:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@<sha>
      - uses: actions/login-action@<sha>
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - name: build once, tag with the sha and the branch
        run: |
          set -euo pipefail
          docker buildx build \
            --tag ghcr.io/${{ github.repository }}:${{ github.sha }} \
            --tag ghcr.io/${{ github.repository }}:latest \
            --push .
      # Deploy by digest, never by a moving tag.
      - name: deploy by digest
        run: |
          set -euo pipefail
          digest=$(crane digest ghcr.io/${{ github.repository }}:${{ github.sha }})
          echo "deploying $digest"
          ./deploy.sh "$digest"     # your deploy tool; verify its flags
```

Deploy the digest, not `latest`: a rollback must be "set the running image to
the previous digest", and that is only expressible if you know the digest.

## Making a slow pipeline faster: the order to try

1. **Cache the dependency install** (usually the largest single win). See
   `ci-caching-and-speedup`.
2. **Split independent jobs** that were sequential steps.
3. **Shard the slowest suite** (usually E2E) across 4 jobs.
4. **Turn on intra-suite parallelism** (`-n 4`, `--maxWorkers`), capped to
   available cores.
5. **Move the slow tier to post-merge.** This is the only change that does not
   make the build wrong.
6. **Cut redundant work**: a build step that runs twice, a matrix cell that
   tests a runtime nobody ships, a test that re-runs on a warm cache.
7. **Only then**: buy faster runners. Machines do not fix a bad pipeline
   structure; they scale the waste.

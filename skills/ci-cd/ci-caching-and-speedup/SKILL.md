---
name: ci-caching-and-speedup
description: Speed up CI with correct dependency, build, and Docker layer caching - concrete cache keys and restore keys for npm/pnpm/yarn, pip, Cargo, Go modules, Gradle/Maven, and Docker BuildKit, plus the correctness traps: stale caches hiding bugs, restore-vs-save semantics, and the cache-poisoning risk of caching test results. Use when a pipeline is slow, when a build fails only in CI, or when setting up caching in GitHub Actions or GitLab CI. Triggers on "CI cache", "cache key", "node_modules cache", "npm ci slow", "cache poisoning", "restore-keys", "build cache miss", "gradle cache".
compatibility: Examples use GitHub Actions `actions/cache`, `setup-node`, `setup-python`, `setup-java`, `setup-go`, `Swatinem/rust-cache` and Docker BuildKit `cache-from`/`cache-to`. Pin every action to a commit SHA; verify action inputs against the action's own README for your version.
metadata:
  version: "1.0"
---

# CI Caching and Speedup

Caching trades correctness risk for time. The only rule: **cache what is
derived from a lockfile, key it on the lockfile, and never cache a result that
a later job trusts without re-verifying it.**

## The three-step mental model

1. **Restore** — a matching cache is fetched into the runner (fast).
2. **Build/install** — the normal command runs. It is *idempotent*: with a warm
   cache it should be a no-op or a small delta; with a cold cache it does full
   work. Your build command must behave correctly in both cases.
3. **Save** — a new cache is written **only if the job succeeded** (GitHub
   Actions default). A failed job's partial `node_modules` is not saved, which is
   the safety property you are relying on.

If step 2 is not idempotent, caching is unsafe. `npm ci` deletes `node_modules`
and reinstalls (safe, ignores the cache — which is why the cache is only useful
for the *download* step), while `npm install` mutates the existing tree (fast
with a warm cache, subtly wrong if the cache is from a different lockfile).

## GitHub Actions: cache keys that work

```yaml
- uses: actions/cache@<sha>
  with:
    path: |
      ~/.cache/pip
      ~/.m2/repository
    key: ${{ runner.os }}-maven-${{ hashFiles('**/pom.xml') }}
    restore-keys: |
      ${{ runner.os }}-maven-          # prefix fallback: any older Maven cache
```

Semantics you must get right:

- `key` — an exact match. On an exact hit, the cache is restored and **not**
  re-saved (you keep the old key; it does not get updated with newer contents).
- `restore-keys` — tried in order, longest-prefix first, as a *fallback* when
  `key` misses. A prefix hit means you get a **stale, partial** cache. That is
  only safe if the build step repairs it — which is why the key must include the
  lockfile hash even with restore-keys.
- **Save happens only on job success.** A step that fails after a partial
  install means the job fails, so nothing is poisoned. The exception: steps
  marked `continue-on-error` or `if: always()` still contribute to a job that
  later succeeds.
- **Cache size limits and eviction**: caches expire after 7 days of no access
  and repos have a total size cap. Do not cache a 5 GB Gradle cache "just in
  case"; evict the LRU entries.
- `actions/cache` uses the Actions cache service, **not** the runner's
  filesystem — you cannot `tar` it yourself. (The runner disk cache is a
  separate, node-local mechanism you generally should not rely on.)

### What to cache, per ecosystem

```yaml
# Node (npm / pnpm / yarn) — cache the package-manager store, not node_modules
- uses: actions/setup-node@<sha>
  with:
    node-version: '22'
    cache: 'npm'                # pnpm: cache: 'pnpm'; yarn: cache: 'yarn'
# setup-* with cache: reads the lockfile for the key itself. It caches the
# global package cache (~/.npm), not node_modules. Keep the lockfile committed;
# without one, setup-node's cache step errors out.

# pnpm: the store is what matters
- uses: pnpm/action-setup@<sha>
  with: { version: 9 }
- run: pnpm config set store-dir ~/.pnpm-store
- uses: actions/cache@<sha>
  with:
    path: ~/.pnpm-store
    key: ${{ runner.os }}-pnpm-${{ hashFiles('**/pnpm-lock.yaml') }}
    restore-keys: ${{ runner.os }}-pnpm-

# Python (pip)
- uses: actions/setup-python@<sha>
  with:
    python-version: '3.12'
    cache: 'pip'                # or 'poetry' / 'pipenv'; key from the lockfile
# pip's own wheel cache (~/.cache/pip) helps; a restored venv does not,
# because it contains absolute paths and a platform-specific interpreter.

# Go
- uses: actions/setup-go@<sha>
  with:
    go-version: '1.23'
    cache: true                 # caches $GOMODCACHE keyed on go.sum
# The Go *build* cache (GOCACHE) is a second, larger win and is also
# cacheable: key it on the OS and the go version.

# Rust
- uses: Swatinem/rust-cache@<sha>
  with:
    workspaces: . > target      # key includes Cargo.lock by default

# Gradle / Maven (Java)
- uses: actions/setup-java@<sha>
  with:
    distribution: temurin
    java-version: '21'
    cache: gradle               # or 'maven'
# setup-java with cache: keys on the wrapper properties / pom, and caches
# ~/.gradle/caches (or ~/.m2/repository). Never cache the Gradle *daemon* or
# a built jar as a shared cache.
```

House rules for these:

- **Always `ci`-install, never `install`**: `npm ci`, `yarn install
  --frozen-lockfile`, `pnpm install --frozen-lockfile`, `pip install -r
  requirements.txt` with a hashed requirements file, `cargo build --locked`,
  `go mod download` (which verifies go.sum). A non-`ci` install silently updates
  the lockfile, so CI tests something you did not commit.
- **Commit the lockfile.** No lockfile means no cache key and, worse,
  unreproducible builds.
- **Cache the *store*, not the *install tree*.** `node_modules` contains
  absolute paths, native binaries built for the runner, and a `.bin` symlink
  farm; restoring it across OS versions or runners breaks in ways that look
  like dependency bugs. The package-manager's content-addressable store is
  safe to restore and lets the install step relink it correctly.

## Docker: layer caching with BuildKit

```yaml
- uses: docker/setup-buildx-action@<sha>
- uses: docker/build-push-action@<sha>
  with:
    context: .
    push: false
    cache-from: type=gha,scope=pr       # restore layers saved by earlier builds
    cache-to: type=gha,mode=max,scope=pr # save every intermediate layer
    tags: app:${{ github.sha }}
```

- `mode=max` caches intermediate layers too (slower to save, much better
  incremental builds). `mode=min` only caches the final image — correct but
  slow, because a change in a late layer invalidates everything before it.
- **Layer order decides cache hits.** Copy the manifest first, install
  dependencies, *then* copy the source:
  ```dockerfile
  COPY package.json package-lock.json ./
  RUN npm ci
  COPY . .
  ```
  Copying the source first means every source change invalidates the install
  layer. This one Dockerfile habit is usually worth more minutes than any cache
  setting.
- For multi-stage builds, cache the **builder** stage, not just the final
  image; that is where the expensive layers are.
- Registry-backed (`type=registry,ref=...`) cache survives runner changes and
  is shared across branches — but it is a shared mutable store, so only
  cross-branch *build* cache belongs there, never test results or secrets.

## CI systems: GitLab, CircleCI, Buildkite

- **GitLab:** `cache:` with `key: $CI_COMMIT_REF_SLUG` and
  `policy: pull` (restore) / `policy: pull-push` (save). Use
  `cache:untracked: true` to include untracked build files. GitLab cache is
  per-branch by default; `cache:key:files:` derives a key from a checksum of
  files.
- **CircleCI:** built-in dependency caching per `run:` step, keyed on checksum
  files you declare (`checksum: package-lock.json`). The modern config uses
  Docker layer caching and `restore_cache`/`save_cache` keys with
  `{{ checksum "..." }}`.
- **Buildkite:** no built-in cache; you point plugins at shared volumes or S3,
  and key the paths yourself. Same rules.

## The correctness traps

**Stale cache hiding a bug.** The failure mode: a key that does not include
everything the artifact depends on. If the cache key omits the compiler version,
the OS image, a toolchain file, or a build flag, you can get a green build
built with the wrong tool. The lockfile is necessary but not sufficient.

**Restore vs save asymmetry.** On an exact-key hit, Actions does *not* update
the cache. If your lockfile hash key matches but the cache was saved by an older
job with a different (e.g. toolchain) input, you keep the old content. Solution:
include every input in the key, or bump a cache-version salt
(`key: v2-${{ hashFiles(...) }}`) when the toolchain changes.

**Cache poisoning via results.** This is the dangerous one. If job A's test
results are cached and job B restores them and reports them as pass, then a
poisoned or stale result becomes a green pipeline. Never cache test results,
coverage reports, linter output, or build outputs that a *later* job trusts
without re-deriving them. If you must cache them to pass them between stages,
key the cache to the **exact source SHA** and the test command, and treat a
restore as an optimisation only — the consuming job must verify the key matches
what it needed. In practice: **pass results as workflow artifacts keyed to the
run, not as caches.** Artifacts are immutable and scoped to a run; caches are
mutable and cross-run.

**Cross-branch cache sharing.** A prefix `restore-key` lets a branch restore a
`main` cache. That is usually the point (faster PRs). It is a hazard if the
build produces something a later job consumes — then the branch inherited a
`main` artifact that does not match its code.

**Cache thrash.** If your key includes the commit SHA, every run is a miss and
you are uploading a full cache each time (slow, and you will hit the repo cache
size cap). Key on the lockfile, not the commit.

**Native modules and platform drift.** A cached store restored on a different OS
or libc (glibc vs musl) relinks, but a cached *install tree* does not. Key OS
into every cache and prefer the store.

**Permissions.** A cache restored from another job may have different
permissions than the runner expects (`npm ci` re-fixing them, or `chmod +x`
being needed for a restored `node_modules/.bin`). The install step fixes this;
another reason the install step must run even on a cache hit.

## Diagnosing: is the cache even working?

- `actions/cache` prints `Cache restored from key: ...` / `Cache not found for
  input keys: ...`. If you see "not found" on every run, your key is wrong or
  the lockfile path does not match `hashFiles` (a common bug: `hashFiles` is
  relative to the workspace and a monorepo lockfile is at a subpath).
- Measure the delta: time the install step cold vs warm. If warm is not faster,
  the cache is not covering the expensive part (often the store is cached but
  the extraction/linking dominates).
- `hashFiles` returns empty if the pattern matches nothing — an empty key
  component means every run shares one cache. Check that the glob matches a file
  that exists.
- For Docker, `docker buildx build --progress=plain` prints which steps were
  `CACHED`; if the `RUN npm ci` step is never cached, the layer order is wrong.

## Gotchas

- **`npm ci` deletes `node_modules` first**, so caching `node_modules` with it
  is pointless. Cache the store (what `setup-node cache: npm` does) and let
  `ci` relink.
- **A `pnpm-lock.yaml` change must change the key**; hashing `package.json`
  alone means two different dependency trees share a store entry and `pnpm ci`
  will silently fix it up by re-fetching — you lose the benefit with none of
  the risk, which is the benign version. Hash the lockfile.
- **`cache: 'yarn'` vs `cache: 'npm'`** in `setup-node` with a
  `yarn.lock` present caches the wrong store; pick by the lockfile that exists.
- **Caching `~/.m2` and `~/.gradle` together is fine; caching the Gradle daemon
  or `build/` output across branches is not** — stale class files and a stale
  daemon produce "works locally, fails in CI".
- **A cache that is saved on a job you did not intend to save from** (a fork's
  PR) can be written to a shared branch scope. Scope caches per branch or per
  PR unless cross-branch reuse is wanted deliberately.
- **Deleting a cache to fix a bad build works right up to the next PR**, which
  restores it from a restore-key prefix. Bump the salt in the key instead.
- **Monorepos**: `hashFiles('**/package-lock.json')` hashes every lockfile, so a
  change in an unrelated package invalidates every package's cache. Scope the
  path per job (`path/to/pkg/package-lock.json`).
- **The fastest cache is the one you do not need** — if a step takes 40s and you
  can restructure it to take 4s, no cache is as good and none of the risk.

## Files

- `references/cache-keys.md` — a per-ecosystem table of what to cache, the key
  expression, the exact install command, and the traps for each. Read it when
  setting up caching for a specific toolchain or debugging a cache miss.

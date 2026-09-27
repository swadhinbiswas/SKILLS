# Cache keys by ecosystem

What to cache, the key expression, the install command that must be idempotent,
and the specific trap for each toolchain.

## Table

| Ecosystem | Cache this | Key expression | Install command | Trap |
|---|---|---|---|---|
| npm | `~/.npm` (the content-addressable cache), not `node_modules` | `${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}` | `npm ci` | `npm ci` wipes `node_modules`, so caching the tree buys nothing |
| pnpm | `~/.pnpm-store` (or `pnpm store path`) | `${{ runner.os }}-pnpm-${{ hashFiles('**/pnpm-lock.yaml') }}` | `pnpm install --frozen-lockfile` | hashing `package.json` misses dependency-only changes |
| Yarn (berry) | `.yarn/cache` (the zero-install cache) + `.yarn/releases` | `${{ runner.os }}-yarn-${{ hashFiles('**/yarn.lock') }}` | `yarn install --immutable` | Yarn 1 has no content-addressable cache; cache `~/.cache/yarn` instead |
| Bun | `~/.bun/install/cache` | `${{ runner.os }}-bun-${{ hashFiles('**/bun.lockb') }}` | `bun install --frozen-lockfile` | lockfile is binary; `hashFiles` still hashes bytes fine |
| pip | `~/.cache/pip` (wheels) | `${{ runner.os }}-pip-${{ hashFiles('**/requirements.txt') }}` | `pip install -r requirements.txt` | a restored venv contains absolute paths — cache the wheel cache, not the venv |
| pip-tools / uv | `~/.cache/uv` | `${{ runner.os }}-uv-${{ hashFiles('**/uv.lock') }}` | `uv sync --frozen` | `uv.lock` is the key; `pyproject.toml` alone is too coarse |
| Poetry | `~/.cache/pypoetry` | `${{ runner.os }}-poetry-${{ hashFiles('**/poetry.lock') }}` | `poetry install --no-interaction` | `--sync` matters, or stale venv packages accumulate |
| Cargo | `~/.cargo/registry`, `~/.cargo/git`, `target/` | `${{ runner.os }}-rust-${{ hashFiles('**/Cargo.lock') }}-${{ profile }}` | `cargo build --locked` | `target/` is large; key it per profile and expect eviction |
| Go modules | `$GOMODCACHE` (~go/pkg/mod) | `${{ runner.os }}-go-${{ hashFiles('**/go.sum') }}-${{ go-version }}` | `go mod download` | module cache is read-only in newer Go; `go clean -modcache` to repair |
| Go build | `$GOCACHE` | `${{ runner.os }}-gobuild-${{ go-version }}-${{ hashFiles('**/go.sum') }}` | `go build ./...` | must include the Go version in the key; cache format changes between versions |
| Gradle | `~/.gradle/caches`, `~/.gradle/wrapper` | `${{ runner.os }}-gradle-${{ hashFiles('**/gradle-wrapper.properties') }}-${{ hashFiles('**/*.gradle*') }}` | `./gradlew build` | never share `build/` output across branches; stale class files |
| Maven | `~/.m2/repository` | `${{ runner.os }}-m2-${{ hashFiles('**/pom.xml') }}` | `mvn -B -ntp verify` | `-ntp` suppresses the transfer log that bloats output |
| .NET (NuGet) | `~/.nuget/packages` | `${{ runner.os }}-nuget-${{ hashFiles('**/packages.lock.json') }}` | `dotnet restore --locked-mode` | key on `packages.lock.json`, not `*.csproj` |
| Docker (BuildKit) | layer cache in the GHA cache backend | `type=gha,mode=max,scope=<branch>` | `docker buildx build` | copy manifests before source, or the install layer never caches |

## The `setup-*` shortcuts

Several actions build the key for you and are less error-prone than hand-rolling:

- `actions/setup-node` with `cache: npm|pnpm|yarn` — derives the key from the
  lockfile, caches the package-manager cache dir, fails clearly if no lockfile
  is found.
- `actions/setup-python` with `cache: pip|poetry|pipenv` — same idea; `cache-dependency-path`
  is required in a monorepo so it finds the right lockfile.
- `actions/setup-go` with `cache: true` — caches `GOMODCACHE` keyed on `go.sum`
  and the Go version.
- `actions/setup-java` with `cache: gradle|maven` — keyed on the wrapper
  properties or the pom.

Use these unless you need something they do not cover (a second cache path, a
custom scope, a cross-branch restore). Verify each action's current input names
against its README; they change between major versions.

## Monorepo keys

`hashFiles('**/package-lock.json')` hashes **every** lockfile in the repo. One
unrelated package's dependency bump invalidates every job. Scope it:

```yaml
key: ${{ runner.os }}-node-pkg-a-${{ hashFiles('packages/a/package-lock.json') }}
path: ~/.npm
```

And build per-package caches in parallel with distinct `path`s only when they
genuinely do not share a store — npm's `~/.npm` is safe to share because the
key differs, but two jobs writing the same cache path with different keys race
on the "save" and one wins.

## A worked restore-vs-save bug

```yaml
# Bug: the key omits the toolchain, so a cache built with Node 20 is restored
# on a Node 22 runner. `npm ci` fixes the tree, so it is benign here — but
# change `npm ci` to `npm install` and you are testing 20's resolutions.
key: ${{ runner.os }}-node-${{ hashFiles('**/package-lock.json') }}
```

```yaml
# Correct: every input to the artifact is in the key. Bump `v2` when the
# toolchain, the base image, or a build flag changes — deleting the cache
# does not help, because restore-keys will hand back the old one.
key: v2-${{ runner.os }}-node22-${{ hashFiles('**/package-lock.json') }}
```

## Diagnosing a persistent miss

1. `actions/cache` logs `Cache not found for input keys: <your key>`. Copy it.
2. Check the key's components: is `hashFiles` returning a hash, or an empty
   string? Empty means the glob matched no file — usually a wrong path (the
   glob is relative to the workspace, and in a monorepo the lockfile is deeper).
3. If the hash is right and it still misses, the cache was never *saved*:
   the job must have failed, or the path glob in `path:` matched nothing (a
   tilde is expanded by Actions, but a relative path like `.venv` is relative to
   the workspace and may be empty).
4. If it restores but the job is not faster, the cache does not cover the
   expensive part. Profile the install step: on a warm wheel cache the cost is
   extraction and linking, which no cache removes.
5. Check eviction: `gh cache list` shows size and last-access; a repo at its
   cap silently drops the oldest entries, so your oldest keys never hit.

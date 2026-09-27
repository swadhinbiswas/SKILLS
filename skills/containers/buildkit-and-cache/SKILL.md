---
name: buildkit-and-cache
description: Use Docker BuildKit to make container builds fast and hermetic - RUN cache mounts for package managers, cache-to and cache-from including the GitHub Actions backend, multi-platform builds with buildx, secret and SSH mounts instead of ARG, and a sane local and CI build setup. Use when a Docker build is slow or the cache never hits, when secrets are needed during a build, when building multi-arch images, or when CI does not reuse a build cache. Triggers on "slow docker build", "cache mount", "type=cache", "buildx", "multi-arch", "cross-compile", "cache-to gha", "secret mount", "ssh mount", "BUILDKIT".
compatibility: Requires BuildKit. Buildx is a separate CLI plugin (`docker buildx`); with the classic builder, `# syntax=` and `--mount` fail with "BuildKit is enabled but the buildx component is missing or broken" or are silently ignored with the legacy builder. Verify builder support with `docker buildx version` and `docker buildx ls`.
metadata:
  version: "1.0"
---

# BuildKit and Cache

BuildKit is a different build engine from the classic `docker build`: it
executes build steps in a sandboxed build container, resolves them in
parallel, has a content-addressed cache with pluggable *export* backends, and
adds `RUN --mount` (caches, secrets, SSH, bind mounts), heredocs, and native
multi-platform output. It is the default in modern Docker; the differences
below are what you actually feel.

Read `references/cache-backends.md` when choosing or debugging where the cache
lives — local vs registry vs GHA vs S3 — or when `cache-to` silently exports
nothing.

## Setup

```dockerfile
# syntax=docker/dockerfile:1
```

The first line is not a comment: it tells the builder which frontend to pull.
Put it at the very top of every Dockerfile that uses `--mount` or heredocs.
If the pin is wrong or the frontend cannot be pulled, the build fails with a
fetch error — the fix is a valid tag from the `docker/dockerfile` image, or
`:latest` if you cannot pin.

Confirm the engine is actually using BuildKit:

```sh
docker buildx version
docker buildx ls
docker build --progress=plain -t app:tag .
```

`--progress=plain` is what you want in CI: it prints each step's command and
output as plain text instead of a TUI, so the log is greppable. A CI log with
no per-step output almost always means the TUI mode swallowed it.

## Layer caching vs cache mounts

Two different caches, solving two different problems:

| | Layer cache | Cache mount |
|---|---|---|
| Keyed on | the instruction text + the parent chain + `COPY` inputs | an explicit `id` (and `sharing`, and optionally a `from` stage) |
| Reuses | whole layers | a **directory** on the build host |
| Affects image size | yes, cached layers are part of the image | **no** — the mount is not in any layer |
| Survives `--no-cache` | no | **yes** (that is the point) |
| Good for | `npm ci`, `go build` outputs | package *downloads*: `~/.npm`, `/go/pkg/mod`, `~/.m2`, `/var/cache/apt`, `~/.cache/pip` |

The rule: **put downloads in a cache mount, and the install itself in a
normal layer.** A cache mount means the downloaded tarballs never enter a
layer, so a `--no-cache` build is still fast on the network step.

```dockerfile
# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev
```

The `--mount` flags that matter:

- `type=cache,target=<dir>` — the directory to persist.
- `id=<name>` — give it a stable name so separate stages share it. Without `id`,
  the target path is the identity, which is usually fine but breaks when the
  path changes.
- `sharing=locked` — only one build step at a time may write it. Use for
  package managers that are not concurrency-safe (older `apt`, `npm` with a
  shared lockfile). `sharing=shared` (the default) allows concurrent reads and
  serialised writes; `sharing=private` gives each build its own.
- `from=<stage>` — seed the cache from another stage, so you can warm it during
  a build that has the toolchain.
- `mode=0777` / `uid=` / `gid=` — when the cache directory is owned by root and
  your build runs as non-root, the step fails with a permission error. Fix with
  `uid=$(id -u)` or `mode=0777`.

Package-manager recipes:

```dockerfile
# apt: keep the lists in a cache, clean them in the same layer
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

# Alpine
RUN --mount=type=cache,target=/var/cache/apk apk add --no-cache jq

# Go: modules and the build cache are separate mounts
RUN --mount=type=cache,target=/go/pkg/mod go mod download
RUN --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=0 go build -o /out/app ./cmd/app

# Maven / Gradle
RUN --mount=type=cache,target=/root/.m2 mvn -B -o dependency:go-offline || mvn -B dependency:go-offline
RUN --mount=type=cache,target=/root/.gradle ./gradlew --no-daemon assemble

# Python
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-cache-dir -r requirements.txt
```

Note the difference between `apk add --no-cache` and a cache mount: `--no-cache`
means "do not keep the index", which is a *size* decision; a cache mount means
"do download on every build but do not re-fetch". For `apk` you usually want
both behaviours expressed as: cache mount for the index, `--no-cache` for the
packages. Verify the flags with `apk add --help` for your Alpine version.

## Secrets and SSH mounts

`ARG` and `ENV` are **not** secret channels. Verified: an `ARG NPM_TOKEN` used
inside a `RUN` appears in `docker history --no-trunc` as
`|1 NPM_TOKEN=hunter2 /bin/sh -c …` — permanently, in the pushed image, and in
any exported build cache. See `container-runtime-security`.

```dockerfile
# syntax=docker/dockerfile:1
RUN --mount=type=secret,id=npmtoken,required=true \
    NPM_TOKEN="$(cat /run/secrets/npmtoken)" && \
    npm ci && \
    unset NPM_TOKEN
```

```sh
docker build --secret id=npmtoken,src=$HOME/.npmrc .
docker build --secret id=npmtoken,env=NPM_TOKEN .        # no file on disk; CI-friendly
```

Semantics that matter:

- The secret is mounted at `/run/secrets/<id>` for the duration of that `RUN`,
  as a read-only file. It is not a layer, not in the cache, and not in
  `docker history`.
- `--secret id=x,env=VAR` reads the value from the build's environment and
  never writes it to a file. Prefer this in CI.
- `required=true` fails the build if the secret is not supplied. Use it for
  anything the build cannot work without — otherwise a missing secret produces
  a confusing 401 from the registry.
- The secret is available to *every* process in that `RUN`, including a
  compromised build script from a dependency. Scope the `RUN` to the one command
  that needs it.
- Do not `cat`, `echo`, or `set -x` a secret. `set -x` prints the expanded value.
- The cache is **not** invalidated by a secret's value. If the build output
  depends on which credentials were used, a cached layer built with token A is
  reused for a build with token B. Do not make build output depend on the
  secret's value.

For private git dependencies:

```sh
docker build --ssh default .
```

```dockerfile
RUN --mount=type=ssh git clone git@github.com:org/private-lib.git /src/lib
```

The key is forwarded from your agent, not baked in. Needs the key registered
(`ssh-add`) and a known-hosts entry (`--ssh default` plus
`RUN mkdir -p ~/.ssh && ssh-keyscan … `, or an `ssh` config with the host key).

Other mount types BuildKit supports: `type=bind` (a host path, read-only by
default — for a local config file, **never** for a secret) and `type=dev` (a
named context).

## Exporting and importing cache

```sh
# local, into the builder
docker buildx build --cache-from type=local,src=.build-cache --cache-to type=local,dest=.build-cache-new -t app:tag .

# registry (needs a writable registry; works cross-machine and in CI)
docker buildx build --cache-to type=registry,ref=ghcr.io/org/app:buildcache,mode=max -t ghcr.io/org/app:tag .

# GitHub Actions cache backend
docker buildx build --cache-to type=gha,mode=max --cache-from type=gha -t app:tag .
```

`mode=min` (the default) caches only the layers of the final stage;
`mode=max` caches every stage, including the builder — which is what you want
when the expensive part is in a build stage. `mode=max` is bigger, and the
export can be slow; start with it and tune.

The `local` cache is a directory, so the two-step `src`/`dest`-with-`-new`
dance above is required (you cannot read and write the same path atomically):

```sh
rm -rf .build-cache
docker buildx build --cache-from type=local,src=.build-cache --cache-to type=local,dest=.build-cache-new -t app:tag .
rm -rf .build-cache && mv .build-cache-new .build-cache
```

GitHub Actions example:

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      actions: write        # required for type=gha
    steps:
      - uses: actions/checkout@<sha>
      - uses: docker/setup-buildx-action@<sha>
      - uses: docker/login-action@<sha>
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - uses: docker/build-push-action@<sha>
        with:
          context: .
          push: true
          tags: ghcr.io/${{ github.repository }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          secrets: npmtoken=${{ secrets.NPM_TOKEN }}
```

The `secrets:` input is how a CI secret reaches a `--mount=type=secret`
without being on disk. `actions: write` in `permissions` is required for the
GHA cache backend; without it the export fails with a permissions error.
Pin every action to a commit SHA.

## Multi-platform builds

```sh
docker buildx build --platform linux/amd64,linux/arm64 -t ghcr.io/org/app:tag --push .
```

- `--push` (or `--output type=oci,dest=app.tar`, or `--load` for a single
  platform) is required. A multi-platform build **cannot** be loaded into the
  local Docker image store — the store is single-platform. Without an output
  flag, buildx writes the result to the cache and prints a warning; nothing is
  usable.
- Cross-building needs a builder that can produce each platform. The default
  `docker` driver builds only for the host platform and will fail on a
  multi-platform request. Create one:
  ```sh
  docker buildx create --name multi --driver docker-container --use
  docker buildx inspect --bootstrap
  ```
  The `docker-container` driver runs BuildKit in a container, which supports
  QEMU-emulated cross builds. Emulation is **slow** (often 5-20x) and can
  produce subtly different output (e.g. cgo). For anything performance-sensitive
  or cgo-dependent, use **native** nodes: a `docker buildx create` with
  `--append` against a per-arch builder host, or a CI matrix that builds each
  arch on a native runner. Native is also how you get correct, fast, small
  images.
- `TARGETOS`/`TARGETARCH` build args are set automatically by BuildKit for the
  target platform:
  ```dockerfile
  FROM --platform=$BUILDPLATFORM golang:1.23 AS build
  ARG TARGETOS TARGETARCH
  RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH go build -o /out/app ./cmd/app
  ```
  Building the *builder* on `$BUILDPLATFORM` and only the *output* for the
  target is the pattern that keeps cross-builds fast: the compiler runs at
  native speed, and the artifact is for the target.
- With the classic builder, `--platform` silently builds for the host only. If
  you expected a multi-arch image and got one arch, check whether you used
  `docker build` or `docker buildx build`.

## A sane build setup

- **Local inner loop:** a `dev` target in the same multi-stage Dockerfile, built
  with `--target dev`, so the dev image has the toolchain and the source is
  bind-mounted by Compose. `docker buildx build --target dev -t app:dev .`
- **CI:** the `docker-container` driver (or a native builder per arch), the GHA
  or registry cache backend, `--push`, a digest-pinned base, and
  `secrets:` for anything private. Emit an SBOM alongside the image.
- **Provenance:** with buildx, `--provenance=true --sbom=true` attaches an SBOM
  and build provenance to the pushed image (subject to registry support). Check
  `docker buildx build --help` for the exact flags in your buildx version; the
  defaults have changed across releases.
- **`--no-cache` is a debugging tool, not a habit.** It is how you find out
  whether a stale cache was hiding a broken build. Use
  `--no-cache-filter deps` if your buildx version supports it (verify with
  `--help`) to invalidate only the dependency stage.

## Gotchas

- **Cache mount + `--no-cache` = fast builds, not no cache.** That is the
  intended behaviour. If you need a truly clean network step, remove the mount
  or use a different `id`.
- **A cache mount owned by root breaks a non-root build step** with
  `Permission denied` on the mount path. Fix with
  `--mount=type=cache,target=...,uid=$(id -u),gid=$(id -g),mode=0777`.
- **Cache mounts are per-builder, not per-project.** Two services sharing
  `/root/.cache/pip` on the same builder share a cache; a different `id` or a
  fresh builder means a cold cache. This is why the first CI run after a
  builder restart is always slow.
- **`sharing=locked` serialises the build step** — it prevents corruption for
  apt and some npm versions, at the cost of losing parallel downloads. Not a
  bug.
- **The build cache is a secret-leak surface.** A cache exported with
  `--cache-to` from a stage that received a secret via `ARG` contains that
  value. Once exported to a registry, it is public. Use secret mounts, and
  treat a pushed `buildcache` tag as production data.
- **`--no-cache` does not clear a cache mount**, so "it works with
  `--no-cache`" does not prove there is no cache dependency.
- **BuildKit's `RUN` does not run through `/bin/sh` the same way** — it is
  `/bin/sh -c` by default, so `set -euo pipefail` is not implicit and pipelines
  do not fail on error. Declare `SHELL ["/bin/bash","-o","pipefail","-c"]` or
  write pipelines out explicitly.
- **Heredocs in `RUN`** (`RUN <<EOF`) are a BuildKit feature; they need the
  syntax directive and a recent buildx.
- **`--platform=$BUILDPLATFORM` on the builder stage** is the difference between
  a 30-second and a 15-minute cross build. Getting it wrong is the most common
  multi-arch performance bug.
- **A multi-arch `--load` is not supported.** If you need one platform locally,
  pass a single `--platform`.
- **`docker build` vs `docker buildx build` differ in default output** (the
  former loads into the local store, the latter requires `--load`/`--push`).
  Scripts that assume the image is present after `buildx build` will fail.
- **Without the `# syntax=` line, `--mount` fails or is ignored** depending on
  the builder, which makes the failure look nondeterministic across CI and
  local machines. Always pin the syntax.
- **Cache `--cache-from type=registry,ref=…` needs the image to exist** in the
  registry. A first run against an empty cache ref is a cold build, not an
  error. `docker buildx du` shows what the builder actually holds.
- **GHA cache backend requires `actions: write`**; without it the export fails
  even though the build succeeded.
- **`COPY --link`** makes a layer independent of its parent, which is useful
  for rebuild-heavy base layers; it is a BuildKit feature and is not the same
  as cache mounts.

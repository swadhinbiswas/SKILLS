---
name: docker-image-optimization
description: Shrink Docker images and cut build times by finding the real cost - reading docker history and dive layer-by-layer, dropping toolchains with multi-stage builds, choosing slim/alpine/distroless, pruning apt and apk caches, and stopping package manifests from busting the cache. Use when an image is too big, when a docker push is slow, when a container start is slow, or when a build takes minutes on a one-line change. Triggers on "image is huge", "docker history", "dive", "reduce image size", "apk cache", "apt cache", "slow docker build", "distroless", "SBOM".
compatibility: Examples use Docker CLI, `dive` (https://github.com/wagoodman/dive) and Syft/Anchore for SBOMs. `docker history` and `docker build` are universally available; install `dive` separately. Verify tool flags with `--help`.
metadata:
  version: "1.0"
---

# Docker Image Optimization

Image size matters for three separate reasons, and optimising for the wrong one
wastes the effort:

1. **Pull time** — every node pulls it on a cold start, on every node
   autoscaling up, and on every CI job.
2. **Registry and disk cost** — layers are stored forever.
3. **Attack surface** — every package in the image is a CVE waiting to be
   scanned. A 1 GB image with 400 packages fails a Trivy gate on a 2 MB one
   that does not.

`docker history` measures only #1 and #2. CVEs scale with *package count*, not
bytes. Measure both.

## Workflow

- [ ] 1. Measure the baseline: `docker images` and `docker history`
- [ ] 2. Find the top 3 layers by size; check whether each is needed at runtime
- [ ] 3. Drop the toolchain with a multi-stage build
- [ ] 4. Choose a leaner base for the final stage
- [ ] 5. Prune package-manager caches inside the same `RUN` that installs
- [ ] 6. Re-measure, and check the CVE count changed
- [ ] 7. Sanity-check that it still runs and still stops cleanly

## Step 1 — baseline

```sh
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | sort -k2 -h
docker history --no-trunc --format '{{.Size}}\t{{.CreatedBy}}' myapp:tag
```

`docker history` prints one row per layer, newest first, and `CreatedBy` is the
**resolved** command — for `RUN` it includes the full `ARG` values. That makes
it both the measurement tool and the secret-leak tool. Verified output shape:

```
1.23MB   /bin/sh -c apk add --no-cache jq && jq --version
0B       CMD ["/bin/sh"]
9.44MB   ADD alpine-minirootfs-3.20.10-x86_64.tar.gz / # buildkit
```

The buildkit `ADD … # buildkit` row is the base image — you cannot shrink it
from your Dockerfile, only by changing the base. If the base is 800 MB of the
1 GB, stop optimising your own layers and change the base.

## Step 2 — classify the fat layers

| Layer pattern | Verdict | Fix |
|---|---|---|
| `apt-get install` / `apk add` | needed at build, usually not at runtime | multi-stage |
| `pip install` / `npm install` | often includes dev deps and build-only wheels | `--omit=dev`, `--no-compile`, no cache dir |
| `COPY . .` | context too broad | tighten copy, add `.dockerignore` |
| `ADD <big tarball>` | vendored binary | strip it, or use a leaner base |
| `.git` in a layer | accidental | `.dockerignore` + `git archive` if you need it |
| `RUN go build` / `cargo build` | toolchain output | multi-stage; the compiler is GBs |
| `curl` of a big file you no longer need at runtime | leftover | delete in the *same* `RUN` |

The measurement that beats `docker history` is `dive`, which diffs each layer
against its parent and flags "wasted space" (files added then deleted in a
later layer, which `docker history` hides because the bytes are still in the
intermediate layer):

```sh
dive myapp:tag                 # interactive
dive --ci myapp:tag            # non-interactive, prints efficiency %
dive --ci --lowest 10 myapp:tag
```

`dive` efficiency is `added files / total added bytes`. Under ~85% means you are
shipping bytes you immediately deleted. Fix those first — they are free wins.

For package-level detail:

```sh
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy image --severity HIGH,CRITICAL --ignore-unfixed myapp:tag
syft myapp:tag -o spdx-json > sbom.spdx.json      # if you want the full bill of materials
```

Verify the exact flags with `--help`; both tools add flags between releases.

## Step 3 — multi-stage is the default answer

Anything only needed to *produce* the artifact belongs in a stage that never
ships. Compilers, headers, package managers, source, and test fixtures all go
in the builder.

```dockerfile
# syntax=docker/dockerfile:1
FROM golang:1.23-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=cache,target=/root/.cache/go-build CGO_ENABLED=0 go build -trimpath -o /out/app ./cmd/app

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/app /app
USER 65532:65532
ENTRYPOINT ["/app"]
```

A `golang:1.23-bookworm` build stage is ~800 MB; the final image is the
compiled binary plus a few MB of libc. That is a 100x reduction and it costs
you nothing at runtime.

**Only `COPY --from=` out of a stage matters.** Anything left un-copied in the
builder is invisible to the final image — that is the whole point.

Stages are built lazily: `docker build --target build -t app:dev .` stops at the
builder and never touches the last stage. Use that for a fast inner-loop image
that *does* have the toolchain.

## Step 4 — base image choice

Measure these on the same app rather than trusting the table:

| Base | Typical runtime size | Has shell | Notes |
|---|---|---|---|
| `python:3.12` | ~1 GB | yes | includes buildpack-deps-style toolchain; `python:3.12-slim` is ~150 MB |
| `python:3.12-alpine` | ~50 MB | yes | musl; some wheels fall back to compiling, which is slow |
| `node:22` | ~1.1 GB | yes | includes npm, yarn, headers; `node:22-bookworm-slim` is ~230 MB |
| `node:22-alpine` | ~140 MB | yes | musl; fine unless native deps |
| `gcr.io/distroless/static-debian12` | ~2 MB + binary | **no** | only for fully static binaries |
| `gcr.io/distroless/nodejs22-debian12` | ~250 MB | no | runs Node, still no shell |
| `gcr.io/distroless/java21-debian12` | ~250 MB | no | JRE only, no shell |

Rules:

- Non-root as a **numeric** uid in the final stage — distroless's
  `nonroot` user is uid 65532, and `USER 65532:65532` avoids a `/etc/passwd`
  lookup entirely.
- If you need a shell to debug production, distroless is wrong. Use
  `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock nicolaka/netshoot`
  on the host instead, or copy a static busybox in. See
  `container-image-debugging`.
- Alpine's musl can break prebuilt native modules. If you see
  `Error loading shared library` or `symbol lookup error` after switching to
  alpine, that is musl, not your app — go back to `-slim`.
- Language-specific slim tags: `-alpine`, `-slim`, `-bookworm` exist for the
  official node/python/golang images. Check
  `docker run --rm <img> cat /etc/os-release` to see what you actually got; a
  `-slim` tag that is still 1 GB means you assumed wrong.

## Step 5 — prune in the same layer

Deleting in a later `RUN` does not shrink the image. The bytes are already in
the committed layer, and `docker history` will show a 0B layer for the delete
while the image on disk stays large. Verified: an `apk add` of `jq` added
1.23 MB in one step; the `/var/cache` cleanup that followed was not needed at
all because `--no-cache` already prevents it.

```dockerfile
# Debian/Ubuntu
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Alpine
RUN apk add --no-cache ca-certificates curl

# Python: no pip cache, no bytecode, no compilers
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
 && find /usr/local -name '__pycache__' -type d -exec rm -rf {} +

# npm: no dev deps in the runtime stage
RUN npm ci --omit=dev && npm cache clean --force

# Go: strip the binary
RUN go build -ldflags="-s -w" -o /out/app ./cmd/app
```

Cache mounts do the same job better, because the cache never enters any layer:

```dockerfile
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev
```

See `buildkit-and-cache`.

## Step 6 — what NOT to do

- **Do not use one giant `RUN` to "share layers" between unrelated things.** It
  does not reduce final size; it only destroys cache granularity.
- **Do not squash with a from-scratch rewrite of history unless you have a
  reason.** A squashed image is unreproducible and unlayered, and BuildKit
  already gives you the small final image through multi-stage.
- **Do not chase the last 5 MB on a 40 MB image** at the cost of debuggability.
  Stop when the image is not the bottleneck.
- **Do not strip a base image to the point that `docker run … sh` no longer
  works** without a documented alternative. That cost shows up as an incident
  during an outage.

## Gotchas

- **Removing a file in a later `RUN` frees zero bytes.** Only `RUN` steps that
  install-then-delete *in the same instruction*, multi-stage, or a leaner base
  actually reduce size. This is the most common false optimisation.
- **Squashing removes the ability to cache** and to `docker pull` a base layer
  shared with other images. Layer sharing across services on the same base is
  worth more than the bytes you saved.
- **A tiny image can have more CVEs than a big one** if you installed
  `curl`, `openssl`, and `tar` on a distroless-equivalent base. Count CVEs, not
  bytes.
- **Reproducible builds and digest-pinning interact with size:** a digest pin
  means you must deliberately bump it, and an un-bumped base is how images
  silently grow. Watch for that in review.
- **`docker images` size is the sum of unique layers.** Two images sharing a
  1 GB base both show 1.1 GB but pull far less on a node that already has the
  base. Do not compare raw sizes across different bases.
- **Adding a file in one stage and copying the whole `/` in the next** undoes
  the multi-stage. Copy specific paths.
- **Alpine's `apk` and Debian's `apt` need different flags**; copying a
  Dockerfile between them silently changes behaviour.
- **Cross-compilation changes the size calculus.** Building a `linux/arm64`
  binary inside an `amd64` container with qemu can produce a different-size
  artifact and is very slow. Use native or a proper builder node — see
  `buildkit-and-cache`.

## Validation loop

```sh
docker build -t app:opt .
docker images --format '{{.Repository}} {{.Size}}' app:opt
docker history app:opt
dive --ci app:opt
docker run --rm -d --name v app:opt && sleep 2 \
  && docker exec v <app's own health command> \
  && time docker stop -t 5 v          # still stops fast
trivy image --severity HIGH,CRITICAL --ignore-unfixed app:opt
```

Record the before/after numbers in the PR. A "this should be smaller" claim
without a measurement gets reverted the next time someone adds a layer.

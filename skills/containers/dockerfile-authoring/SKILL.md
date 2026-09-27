---
name: dockerfile-authoring
description: Write Dockerfiles that build fast, stay small, and shut down cleanly - multi-stage builds, layer ordering and cache invalidation, COPY over ADD, .dockerignore, digest-pinned base images, non-root USER, and exec-form CMD/ENTRYPOINT so PID 1 receives SIGTERM. Use when creating or fixing a Dockerfile, when a build is slow or the cache never hits, when a container ignores docker stop or gets SIGKILLed, or when auditing an image for reproducibility. Triggers on "Dockerfile", "multi-stage build", "layer caching", "docker stop hangs", "image is too big", "ARG vs ENV", "non-root container", "distroless".
compatibility: Examples use Docker with BuildKit (# syntax=docker/dockerfile:1) and `docker build`. Distroless images come from gcr.io/distroless. Pin the base image by digest in CI.
metadata:
  version: "1.0"
---

# Dockerfile Authoring

A Dockerfile is a cache-invalidation contract. Most bad Dockerfiles are not
"wrong" - they are ordered so that a one-character source change re-runs
`npm install`, or they use shell form so the process never gets `SIGTERM`.

## The default shape

Start from this and deviate only with a reason:

```dockerfile
# syntax=docker/dockerfile:1
ARG BASE=node:22-bookworm-slim

FROM ${BASE} AS deps
WORKDIR /app
# Manifests are copied alone so a source edit does not reinstall dependencies.
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --omit=dev

FROM ${BASE} AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${BASE} AS runtime
ENV NODE_ENV=production
WORKDIR /app
RUN groupadd -r app && useradd -r -g app -u 10001 app
COPY --from=build --chown=app:app /app/dist ./dist
COPY --from=deps --chown=app:app /app/node_modules ./node_modules
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "dist/server.js"]
```

Note what is *not* in it: no `apt-get install` in the runtime stage, no `ENV`
secrets, no `ADD`, no shell-form `CMD`, no `root`.

## Workflow

- [ ] 1. Pick the base image: `-slim` (Debian) by default; `alpine` when size
      dominates; `distroless` for the runtime stage of a static binary or a
      language with no runtime shell needs. Pin by digest.
- [ ] 2. Copy dependency manifests first, install, then copy source.
- [ ] 3. Use BuildKit cache mounts for package managers.
- [ ] 4. Multi-stage: build with toolchains, ship only the artefacts.
- [ ] 5. `USER` a numeric non-root uid in the final stage.
- [ ] 6. Exec-form `ENTRYPOINT`/`CMD`.
- [ ] 7. Verify: `docker history`, `docker run --init` stop timing, `docker inspect`
      for `User`.

## Layer ordering and cache

Each instruction is a layer. A layer is a cache hit only if **every** preceding
instruction is also a hit and that instruction's inputs are unchanged. Two
consequences, both of which people get wrong:

- **Order by change frequency, slowest first.** Anything expensive or rarely
  changing goes high; anything that changes per commit goes low.
- **`COPY . .` above an install invalidates the install.** This is the single
  most common reason a 4-minute build takes 4 minutes on every commit.

```dockerfile
# WRONG: every source edit reinstalls everything
COPY . .
RUN npm ci
RUN npm run build

# RIGHT: manifests alone gate the install
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
```

"Copy manifests alone" needs judgement per ecosystem:

| Ecosystem | Gate on | Install command |
|---|---|---|
| Node | `package.json` + lockfile | `npm ci` (never `npm install` — it rewrites the lockfile) |
| Go | `go.mod` + `go.sum` | `go mod download` |
| Rust | `Cargo.toml` + `Cargo.lock` | `cargo fetch --locked` |
| Python | lockfile only (`requirements.txt` with hashes, `poetry.lock`, `uv.lock`) | `pip install --require-hashes -r ...` |
| Maven | `pom.xml` | `mvn -B dependency:go-offline` |
| Gradle | `build.gradle*`, `settings.gradle*` | `./gradlew dependencies` |

`ARG` values also participate in the cache key: changing a build arg before a
`RUN` invalidates every layer after it. Put frequently-changing args as late
as possible.

### Combining RUN instructions

```dockerfile
# WRONG - the .deb files are deleted in a later layer, so they still ship
RUN apt-get update && apt-get install -y curl
RUN curl -fsSL -o /usr/local/bin/app https://example.com/app
RUN rm -rf /var/lib/apt/lists/*

# RIGHT - one layer, cache is clean
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
```

But combining does not mean "one giant RUN". Balance it: combining everything
into one layer means *any* change re-runs the whole thing. Keep logically
distinct, rarely-changing steps separate.

Use a multi-line `RUN` with `\` continuations (BuildKit also honours heredocs).
If you must run shell logic, remember `set -euo pipefail` is **not** the
default in `/bin/sh` for the build shell — use `SHELL ["/bin/bash", "-o", "pipefail", "-c"]`
before any `RUN` containing a pipeline, or write it out explicitly.

## `COPY` vs `ADD`

Use `COPY`. It does one thing: copy files. `ADD` additionally:

- **Extracts local tar archives** automatically (verified: `ADD app.tar /opt/`
  unpacks into `/opt/`). A build that unexpectedly unpacks is usually an
  `ADD` of a tarball.
- **Fetches URLs**, with no checksum and no cache-busting, so it silently
  changes your build inputs.
- Sets different ownership (uid/gid 0 by default) than `COPY`, which preserves
  the host's ownership.

If you genuinely need an unpack, use `ADD` for the tar and then a `RUN tar -xf`
so the contents appear as a normal layer you can reason about. Never `ADD` a
remote URL for anything that ends up in a shipped artifact.

`COPY --chown=1000:1000 . /app` is preferred over a `RUN chown -R` in a
separate layer: it does not duplicate the data and it does not depend on a
`useradd` having run in an earlier layer. `COPY --from=build --chown=…` combines
the two.

## `.dockerignore`

Without one, the whole build context is uploaded to the daemon on every build.
Verified behaviour: with a `.dockerignore` containing `ignored/`, the `ignored/`
directory is absent from `/app` in the resulting image.

```
.git
node_modules
dist
build
coverage
*.log
.env
.env.*
**/__pycache__
**/*.pyc
Dockerfile*
docker-compose*.yml
```

Gotchas:

- Patterns are Go `filepath.Match`-style with `**` support. A leading `!`
  negates.
- **`.dockerignore` only affects the build context sent to the daemon.** It does
  not remove files from an already-built image, and it does not apply to
  `docker run -v` mounts.
- A `.dockerignore` that excludes the `Dockerfile` itself is legal and often
  done; it does not break the build, because the Dockerfile is read separately.
- Include secrets. A `.env` in the context can be copied into an image by a
  `COPY . .`.

## Base image pinning

`FROM node:22` is a moving pointer. `node:22.11-bookworm-slim` is fixed until
they delete the tag. A **digest** is the content itself and never moves:

```dockerfile
ARG BASE_DIGEST=sha256:2f2c…
FROM node:22.11-bookworm-slim@${BASE_DIGEST} AS build
```

Resolve a digest to fill it in, and automate it (Renovate/Dependabot update the
digest and open a PR):

```sh
docker buildx imagetools inspect node:22-bookworm-slim --format '{{json .Manifest}}'
```

Caching: to get a new base image without rebuilding everything locally, use
`docker build --pull` (re-pulls, then re-uses cached layers whose digests
match) or `--no-cache` when you actually suspect stale layers.

### slim vs alpine vs distroless

| Base | Size | Runtime shell | glibc/musl | Use when |
|---|---|---|---|---|
| `debian` | ~120 MB | yes | glibc | never, in a runtime stage |
| `debian-slim` | ~80 MB | yes | glibc | **default**; need `apt-get` or a shell to debug |
| `alpine` | ~8 MB | yes | musl | size dominates; pure-Go/Node/Python apps; watch native deps |
| `distroless/*` | ~2-30 MB | **no** | glibc (or static) | shipped runtime; you can debug by copying a shell in |

Alpine's musl libc breaks prebuilt native binaries and some JVM/Python wheels;
if you hit `not found` or `symbol lookup error` on a compiled dependency,
switch to `-slim` rather than debugging musl.

Distroless has no shell, no package manager, and no `ls`. That is the point,
but it means `docker exec … sh` fails with `exec: "sh": executable file not
found in $PATH` — that is expected, not a broken image. See
`container-image-debugging` for how to get in anyway.

## `USER` and PID 1

### Non-root

```dockerfile
USER 10001:10001
```

Use a **numeric** uid, not a name: a name requires `/etc/passwd` lookup, and a
numeric uid works with an arbitrary OpenShift-assigned uid (OpenShift forces a
random uid in the high range, so the app must not assume it is 0 or a fixed
value — group 0 access is the common accommodation). Verify with
`docker inspect --format '{{.Config.User}}' img`.

If the base has no non-root user, create one in the build stage
(`groupadd`/`useradd` on Debian, `addgroup -S`/`adduser -S` on Alpine) and then
`COPY --chown=`. A `USER` in a *build* stage does not carry to later stages —
set it again in the final stage.

Kubernetes `runAsNonRoot: true` + `allowPrivilegeEscalation: false` enforce
this. See `container-runtime-security`.

### `CMD`/`ENTRYPOINT`: shell form vs exec form

Shell form wraps the command in `/bin/sh -c`, which means **your process is not
PID 1 — the shell is.** A shell that is PID 1 does not forward `SIGTERM` to its
child by default, so the app never gets a chance to close connections and
flush, and the runtime escalates to `SIGKILL` after the grace period.

Measured on this machine, same app, `docker stop -t 3`:

| Dockerfile | `docker stop` wall time | Exit code |
|---|---|---|
| `CMD ["sh","-c","sleep 300 & wait"]` | 3.2 s (full grace, then SIGKILL) | 137 |
| `CMD ["sleep","300"]` | 3.2 s, exit 137 | 137 |
| `CMD ["sleep","300"]` with `--init` | **0.19 s** | 143 (SIGTERM) |

Read that table carefully — it is more subtle than the usual folklore:

- Shell form (`sh -c "app & wait"`) is the classic trap: the child never sees
  `SIGTERM` even if it would handle it.
- **Exec form is necessary but on its own it was not sufficient here** —
  `sleep` as PID 1 still died by SIGKILL. Modern kernels and runtimes do not
  deliver default-action signals to PID 1 unless an init/signal-forwarding
  layer is present. `--init` (inject `docker-init` / `tini` as PID 1) makes
  exec form behave: fast stop, clean exit 143.
- Therefore: **exec form + an init.** In Docker use `--init` or
  `docker run --init`; in Kubernetes this is built in for pods (see
  `kubernetes-manifests`). If you cannot use `--init` (distroless, a base with
  no init), handle the signal in the app and make sure the app is exec-form —
  many runtimes are now fine, but do not rely on it.

Rules:

- `ENTRYPOINT` = the app. `CMD` = default args, overridable by the caller.
- Exec form (`["node","server.js"]`) unless you genuinely need shell features
  (variable expansion, globbing, `&&`). If you do need them, exec-form
  `["/bin/sh","-c","…"]` and `exec` the last command so the app replaces the
  shell and keeps PID 1.
- `CMD` in shell form silently ignores `docker run img some-args` in confusing
  ways; exec form is predictable. Verified: `docker run ovtest "echo x"`
  replaces the CMD entirely; `--entrypoint` replaces ENTRYPOINT and still
  passes the args.

## Reproducibility

- Pin the base by digest; commit the lockfile; install from it with the
  lockfile-enforcing command (`npm ci`, `cargo --locked`, `go mod download`).
- Set `SOURCE_DATE_EPOCH` to the commit timestamp and `TZ=UTC` if the build
  embeds time.
- Do not `apt-get install` an unpinned package if you can vendor the binary.
- Avoid `curl … | sh`; fetch, verify a checksum, then run.
- Sort file lists and set explicit mtimes when you produce a tar inside the
  build.

Related skill: `ci-cd/reproducible-builds` has the full treatment; this
section is the container-specific subset.

## Gotchas

- **`RUN` layer output is committed even if the command "fails" visually.** A
  `curl` that writes a partial download and then fails to connect leaves the
  file in a committed layer if it was in a previous `RUN`.
- **`ENV` in a Dockerfile is public.** Anything in `ENV` or `ARG` can show up in
  `docker history --no-trunc` and `docker inspect`. Never put a secret in
  either — use `--mount=type=secret` (BuildKit) or mount at runtime.
  Verified: `docker history --no-trunc` prints the full command line including
  `NPM_TOKEN=hunter2`. That is a credential leak in the image, permanently.
- **`ARG` values also land in the image config** for that build stage, and in
  the legacy builder's history. Treat any build arg as public.
- **`FROM` inherits `ENV`, `WORKDIR`, `USER`, `CMD`** from the stage it is
  based on. Copying from a stage inherits its `WORKDIR`, which surprises people
  who `COPY --from=build ./dist /usr/share/` and get a doubled path.
- **`WORKDIR` creates the directory if missing** — and creates it owned by
  whatever `USER` is current at that point. Set `USER` before `WORKDIR` if the
  app must write there.
- **`EXPOSE` does nothing** except document; it does not publish a port. You
  still need `-p` / `ports:`. It also does not force the app to listen there.
- **`COPY` fails on a directory rename but silently merges on overwrite** —
  an old file left in a base image can survive a `COPY` of a new directory
  into the same path.
- **`apk add --no-cache`** — the flag is the cleanup. Without it, `/var/cache/apk`
  ships in the layer.
- **A `HEALTHCHECK` in a Dockerfile is ignored by Kubernetes** (which uses
  probes) and by most PaaS. Keep it for `docker run` and compose, and add
  probes in the orchestrator.
- **`docker build` without BuildKit** silently ignores `# syntax=` lines,
  `RUN --mount`, heredocs, and `--platform`. If a build suddenly fails on an
  unknown flag, you fell back to the legacy builder (check for an error
  mentioning `buildx`). See `buildkit-and-cache`.

## Validation loop

```sh
docker build -t app:test .
docker history --no-trunc app:test | sort -h -r -k1 | head -10   # biggest layers
docker inspect app:test --format 'User={{.Config.User}} Cmd={{json .Config.Cmd}}'
docker run --rm --init -d --name sig app:test          # then:
time docker stop -t 5 sig                              # must be fast, exit 143
docker logs sig
```

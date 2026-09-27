---
name: container-runtime-security
description: Harden container workloads at build and run time - non-root users, read-only root filesystems, dropped capabilities, seccomp and AppArmor profiles, no-new-privileges, resource limits, secrets that never touch a layer, and image scanning with Trivy or Grype. Use when reviewing an image or a Kubernetes securityContext, when a scan fails on a critical CVE, when a container runs as root, or when a credential was committed and needs assessing. Triggers on "container security", "runs as root", "privileged", "capabilities", "read-only rootfs", "secret leaked into image", "Trivy", "Grype", "container escape", "securityContext".
compatibility: Examples use Docker/OCI runtime flags (`--read-only`, `--cap-drop`, `--security-opt`, `--user`), Kubernetes `securityContext`, Trivy and Grype. Some hardening flags (`--userns-remap`, userns-remap in daemon.json) require a recent Docker Engine; verify with `docker run --help`.
metadata:
  version: "1.0"
---

# Container Runtime Security

A container is a security boundary with a known set of holes. This skill covers
the boundary you can actually control: who runs the process, what it can do
inside, what it can reach, and what ends up in the image forever.

Threat model in one line: **assume the attacker has code execution in your
container and wants to read other containers' memory, the host filesystem, or
your CI credentials.** Every control below is aimed at that.

Read `references/oci-spec-reference.md` when you need the exact field names in
an OCI image config or a `docker inspect` output, or the seccomp/capability
background.

## The layered controls, strongest first

| Layer | Control | Effort |
|---|---|---|
| Image | non-root, no secrets, minimal base, scanned | low |
| Runtime | dropped caps, read-only rootfs, no-new-privileges | low |
| Isolation | seccomp/AppArmor, user namespaces, rootless daemon | medium |
| Platform | NetworkPolicy, PodSecurity Admission, restricted SA | medium |
| Supply chain | digest pinning, signed images, SBOM, provenance | medium |

You want every layer present. Missing one is not fine, but the order matters:
a minimal image with default capabilities is better than a hardened runtime
configuration on top of a 1 GB root image full of vulnerable packages.

## Step 1 — the build: no root, no secrets, small surface

```dockerfile
# syntax=docker/dockerfile:1
FROM gcr.io/distroless/static-debian12 AS build
ARG TARGETOS TARGETARCH
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,target=/go/pkg/mod go mod download
COPY . .
RUN --mount=type=secret,id=npmtoken,required=true \
    CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -o /out/app ./cmd/app

FROM gcr.io/distroless/static-debian12
COPY --from=build /out/app /app
USER 65532:65532
ENTRYPOINT ["/app"]
```

- `USER 65532:65532` — numeric, non-root, and no `/etc/passwd` lookup. Verify:
  `docker inspect --format '{{.Config.User}}' img` must not be empty or `0`/`root`.
- Distroless means no shell and no package manager in the shipped image, so
  "log in as root and fix it" is not an option during an incident. Have a
  documented alternative (see `container-image-debugging`).
- Scan before pushing, in CI, on the **final** image — not the build stage:
  ```sh
  trivy image --exit-code 1 --severity HIGH,CRITICAL --ignore-unfixed -f table app:tag
  grype app:tag
  syft app:tag -o spdx-json > sbom.spdx.json     # optional, attach to the release
  ```
  `--ignore-unfixed` is the pragmatic gate: a base image with an unfixed kernel
  CVE in `linux` will otherwise block every build forever. Note the policy in
  the repo so it is a decision, not a habit.
- Prefer `-slim`/distroless over `alpine` if you need a shell for an incident;
  prefer both over full images. Package count drives CVE count.

## Step 2 — secrets that never enter a layer

**The rule: a secret in a Dockerfile is a permanently published credential.**

Verified: this Dockerfile

```dockerfile
FROM node:22-bookworm-slim
ARG NPM_TOKEN
RUN echo "//registry:_authToken=${NPM_TOKEN}" > /root/.npmrc
```

built with `--build-arg NPM_TOKEN=hunter2` produces an image whose
`docker history --no-trunc` output contains the literal line:

```
… |1 NPM_TOKEN=hunter2 /bin/sh -c echo "//registry:_authToken=${NPM_TOKEN}" > /root/.npmrc
```

The token is in the image's layer metadata, readable by anyone who can pull the
image, forever, even though the *file* is deleted in a later layer. `ENV` is
worse: it lands in the image config JSON (`docker inspect --format
'{{json .Config.Env}}'`) and is trivially greppable.

What leaves secrets in image history:

| Mistake | Why it leaks |
|---|---|
| `ARG SECRET` + `RUN` using it | ARG values are recorded in history and the build cache key |
| `ENV SECRET=...` | lands in image config, visible in `docker inspect` and `docker history` |
| `RUN git clone https://token@github.com/org/private.git` | literal token in the command line |
| `RUN curl -H "Authorization: Bearer $TOKEN" …` | `ARG`/`ENV` expansion happens before the shell sees it |
| `RUN echo "$SECRET" > file` then `RUN rm file` | the `rm` does not shrink the earlier layer |
| `COPY . .` with a `.env` or `*.pem` not in `.dockerignore` | the file is a committed layer |
| `ENV` for a value that also appears in the base image | your build made a copy |
| A build cache export (`--cache-to`) of a stage that took a secret | the secret is in the exported cache blob |

Correct mechanisms, in order of preference:

1. **BuildKit secret mounts** — the value exists only in a tmpfs bind during
   that `RUN`, and never in a layer or the build cache:
   ```sh
   docker build --secret id=npmtoken,src=$HOME/.npmrc .
   # in CI: --secret id=npmtoken,env=NPM_TOKEN   (env= avoids a file on disk)
   ```
   ```dockerfile
   RUN --mount=type=secret,id=npmtoken \
       cp /run/secrets/npmtoken /root/.npmrc && npm ci && rm /root/.npmrc
   ```
   `required=true` makes the build fail if the secret is not supplied, which is
   the behaviour you want in CI. Do not `echo` or `cat` the secret, even to a
   log.
2. **SSH mounts** for private git deps:
   `--mount=type=ssh` with `--ssh default`, and `git clone git@github.com:…`.
3. **Registry/runtime secrets**: `docker run --env-file` from a file outside
   the repo, or `--mount type=bind` of a tmpfs. In Kubernetes, use a `Secret`
   and mount it as a file — see `kubernetes-manifests`.
4. **Never** in a `docker buildx build --build-arg` for anything sensitive,
   even with a private registry: the registry does not scrub history.

**If a secret was committed:** assume it is compromised. Rotate/revoke first,
then rewrite history (`git filter-repo` / BFG) to remove it from the log. Then
scan the image and the CI logs, and tell the user the rotation is the real fix.
Never "just delete the file in a new commit".

## Step 3 — runtime hardening

At run time, in Docker:

```sh
docker run -d --name app \
  --user 10001:10001 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop=ALL \
  --cap-add=NONE \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=myprofile.json \
  --pids-limit 512 \
  --memory=512m --cpus=1 \
  --ulimit nofile=4096:4096 \
  app:tag
```

Verified: `--read-only --cap-drop=ALL --security-opt no-new-privileges` on
alpine works fine for a normal process; writing to `/` fails with
`Read-only file system`, which is the point. Make anything that needs to write
use a `tmpfs` or an explicit volume.

What each control does:

- `--user` — run as non-root. The single biggest win. A root-in-container
  process with default capabilities is much closer to host root.
- `--read-only` — an immutable root filesystem. Malware and accidents cannot
  persist. Requires tmpfs/volumes for `/tmp`, `/var/run`, and any writable
  workdir. Test the app before enforcing: some runtimes write to
  `/usr/lib`, some apps write to `$HOME`.
- `--cap-drop=ALL` — Linux capabilities are how a non-root process can still
  do root things (`CAP_CHOWN`, `CAP_SETUID`, `CAP_NET_RAW`,
  `CAP_DAC_OVERRIDE`). Drop all, add back only what the app needs (most need
  none; a debug tool may need `CAP_NET_BIND_SERVICE` for port 80, or
  `CAP_SYS_PTRACE` to attach).
- `--security-opt no-new-privileges:true` — sets `PR_SET_NO_NEW_PRIVS`; a setuid
  binary or a dropped capability cannot be regained. Cheap and safe.
- `--security-opt seccomp=<file>` — a syscall allowlist. The **default** Docker
  seccomp profile already blocks ~44 syscalls including `ptrace`, `mount`,
  `keyctl`, `bpf`, and `perf_event_open`. Do not disable it
  (`seccomp=unconfined` is a finding in any review). Write a profile only when
  a legitimate syscall is blocked — the error is usually `EPERM` from an
  unusual syscall at startup.
- `--security-opt apparmor=<profile>` or the distro default. `unconfined` is a
  finding.
- `--pids-limit` — stops a fork bomb from taking down the host.
- `--memory`, `--cpus` — a container that OOMs itself must not take the node
  with it; the memory limit also caps what it can be used to leak.
- `--ulimit nofile` — a low limit is cheap DoS protection.
- `userns-remap` in `daemon.json` (or rootless Docker) — user-namespace
  remapping gives you a real uid boundary between the container and the host.
  Strongest single isolation control available in plain Docker; costs bind-mount
  uid semantics and some volume edge cases. Test before enabling fleet-wide.

In Docker Compose, the same fields are the same keys: `user:`, `read_only: true`,
`tmpfs:`, `cap_drop: [ALL]`, `cap_add:`, `security_opt: [no-new-privileges:true]`,
`pids_limit:`, `mem_limit:`, `cpus:`.

In Kubernetes these move into `securityContext` — see
`kubernetes-production-safety` and the snippet in `references/oci-spec-reference.md`.

**`--privileged` is the single most dangerous flag in this domain.** It grants
all capabilities, disables seccomp and AppArmor, and (on most drivers) gives
the container the host's devices — which is effectively root on the host. It
appears in tutorials, in `--cap-add=SYS_ADMIN` debug advice, and in CI runners.
Treat any `privileged: true` in a manifest as requiring explicit sign-off, and
never ship it.

## Container escape: the honest risk summary

Containers are not a VM boundary. Known classes of escape, in rough order of how
real they are in a default config:

| Class | What it is | Mitigation |
|---|---|---|
| `CAP_SYS_ADMIN` + `mount` | mount host devices, is basically host root | drop all caps; never privileged |
| Kernel CVEs | container escape primitives live in the kernel first | patch the host kernel; pin/gate base images on CVEs |
| Writable `/proc` or `/sys`, `hostPID`/`hostNetwork` | exposes host namespaces and devices | do not set them; `--pid=host` is a privilege escalation by design |
| Exposed container runtime socket | mounting `/var/run/docker.sock` = **host root** | never mount it into a container, especially not a CI or build container |
| `ptrace` / `CAP_SYS_PTRACE` across containers | read another process's memory, scrape credentials | default seccomp blocks `ptrace`; do not add the cap |
| Docker socket in a "debug" sidecar | same as the above | use an ephemeral debug container without the socket, or a restricted proxy |
| Volume mounts of `/`, `/etc`, `/var/run` | direct host filesystem access | never; use narrow, read-only subpaths |
| Env/args leakage | secrets visible in `docker inspect` / `ps` | secret mounts, no `ENV` secrets |

The two that actually show up in incidents: **the Docker socket mounted into a
container**, and **`privileged: true`**. Both are one line and both are host
root. Everything else is defence in depth.

## Verification loop

```sh
# who am I
docker inspect app:tag --format 'User={{.Config.User}}'
# no secrets in the metadata
docker history --no-trunc app:tag | grep -iE 'token|password|secret|key' || echo clean
docker inspect app:tag --format '{{json .Config.Env}}'
# runtime posture of a running container
docker inspect app --format '{{json .HostConfig.CapDrop}} {{.HostConfig.Privileged}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.SecurityOpt}}'
# CVEs
trivy image --severity HIGH,CRITICAL --ignore-unfixed app:tag
# filesystem is read-only at run time
docker exec app sh -c 'touch /x' 2>&1 | grep -i "read-only" || echo "WRITABLE"
```

## Gotchas

- **`--cap-drop=ALL --cap-add=NONE` is stricter than `--cap-drop=ALL` alone** in
  some Docker versions. Use `--cap-drop=ALL` and add back explicitly what you
  need; verify with `docker inspect --format '{{json .HostConfig.CapAdd}}'`.
- **`--read-only` is not enough on its own**: writable `/proc`, `/sys`, and
  `hostPID` are separate mount namespaces. `--read-only` blocks the container
  rootfs, not the kernel interfaces.
- **`seccomp=unconfined` and `apparmor=unconfined` are red flags** in any
  Dockerfile, compose file, or `securityContext`. They are usually there because
  a default profile blocked a syscall and someone removed the whole profile
  instead of adding one syscall.
- **`privileged` and `--privileged` also disable the seccomp profile**, so a
  privileged container has *both* removed. Do not grant it to "fix" a
  permission error.
- **`--user 1000` on a bind mount** is the fix for host permission errors, not a
  security control you can skip — see the UID trap in `docker-compose-workflows`.
- **Scanning the base image is not scanning your image.** Your final image's
  CVEs are the union of every layer you kept. Scan the final image.
- **`--ignore-unfixed` is a policy decision, not a free pass.** Record it in the
  repo and review it periodically; otherwise the flag quietly becomes permanent.
- **`ENV` set in a base image is inherited.** `docker image inspect
  <base> --format '{{json .Config.Env}}'` before assuming a value is yours.
- **Kubernetes `allowPrivilegeEscalation: false` and `readOnlyRootFilesystem:
  true` are the enforcement points**; the Dockerfile `USER` is a default, not a
  guarantee, because a `securityContext` overrides it.
- **A distroless image has no shell, so `docker exec … sh` fails.** That is
  correct behaviour, not a broken image. See `container-image-debugging`.
- **Capabilities live in the *bounding set*; dropping them at start works, but
  `capsh --print` inside the container is the verification**, and many distroless
  images lack `capsh`. Inspect the host-side `CapAdd`/`CapDrop` instead.
- **Never mount `/var/run/docker.sock` into anything, ever.** It is the most
  common "temporary debug" in a repo and it is host root.

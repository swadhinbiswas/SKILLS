# OCI image spec and container runtime reference

Field-level reference for what is actually in an image, and what the runtime
flags map to. Read this when a `docker inspect` field or an OCI manifest field
name matters and you do not want to guess.

## OCI image layout

An image is a set of blobs plus an index. Verified layout of
`docker save -o img.tar app` (Docker writes the OCI layout now; the `manifest.json`
alongside it is the legacy Docker format for the same content):

```
blobs/sha256/<digest>      # content-addressed: the layer tars and the config JSON
index.json                 # top-level index -> manifests
oci-layout                 # {"imageLayoutVersion":"1.0.0"}
manifest.json              # legacy Docker-format manifest, same content
```

- A **blob** is named by its own digest. Changing one byte changes the digest
  and every reference to it.
- A **layer** is a gzipped tar of a filesystem diff, with an accompanying
  `/blobs/sha256/<digest>.json` "diff" file recording `created`, `author`, and
  the `docker diff`-style `added`/`changed`/`removed` file list. `dive` reads
  these; that is why it can find files you added and deleted.
- The **config blob** is the image configuration: env, entrypoint, cmd, user,
  exposed ports, labels, history (the `CreatedBy` lines `docker history` shows).
- The **manifest** points at the config and the ordered layer list. The **index**
  points at manifests, one per platform for a multi-arch image. The `index.json`
  annotation `org.opencontainers.image.ref.name` is where `:tag` is stored.

Tags are not in the image; they are a mutable pointer in the registry's tag
list. Digests are the content. `FROM img@sha256:…` is immutable; `FROM img:tag`
is not.

Layer order matters: each layer is applied over the previous one, and a file
present in two layers resolves to the later one at runtime while **both** bytes
still exist in the tar. That is the whole basis of the "delete in a later RUN"
no-op.

## Image config JSON: fields that matter

`docker image inspect img --format '{{json .Config}}'`

| Field | Meaning | Pitfall |
|---|---|---|
| `Config.User` | uid, uid:gid, or name, applied to `docker run` unless overridden | `""` means root; a name requires `/etc/passwd` |
| `Config.Env` | baked environment; shows secrets if you put them there | greppable via `docker inspect` |
| `Config.Entrypoint` / `Config.Cmd` | see override semantics below | `null` entrypoint means it runs `Cmd` directly |
| `Config.WorkingDir` | cwd for the process; Docker creates it if missing | created owned by the current `USER` |
| `Config.ExposedPorts` | documentation only; does not publish | needs `-p` to be reachable from the host |
| `Config.Healthcheck` | `Test`, `Interval`, `Timeout`, `Retries`, `StartPeriod` | in nanoseconds; ignored by Kubernetes |
| `Config.Labels` | `org.opencontainers.image.*` for provenance | useful for SBOM tooling |
| `Config.ArgsEscaped` / `OnBuild` / `Shell` | legacy / inheritance hooks | rarely relevant |

`docker inspect <container>` (not `image inspect`) is the *instance*:
`.State` (Status, Running, ExitCode, OOMKilled, Error, StartedAt, FinishedAt),
`.Path`/`.Args` (resolved entrypoint+cmd), `.RestartCount`,
`.HostConfig` (the run-time flags: `CapAdd`, `CapDrop`, `Privileged`,
`ReadonlyRootfs`, `SecurityOpt`, `PidsLimit`, `Memory`, `NanoCpus`, `RestartPolicy`),
`.NetworkSettings` (ports, networks), `.Mounts`.

## ENTRYPOINT and CMD override semantics

Verified with an image built as `ENTRYPOINT ["/bin/sh","-c"]` + `CMD
["echo default-cmd"]`:

| Command | Result |
|---|---|
| `docker run img` | `/bin/sh -c "echo default-cmd"` -> `default-cmd` |
| `docker run img "echo arg-replaced-cmd"` | CMD is **replaced** by the arg -> `arg-replaced-cmd` |
| `docker run --entrypoint sh img -c "echo x"` | ENTRYPOINT is **replaced**; the remaining args are the new command |
| `docker run --entrypoint echo img hello` | runs `echo hello` |

Rules that follow:

- `ENTRYPOINT` + `CMD` are concatenated: `exec(Entrypoint..., Cmd...)`.
- Any positional argument after the image **replaces `CMD` entirely**; it is not
  appended.
- `--entrypoint` replaces `ENTRYPOINT` and is itself run with the following args
  (the old `CMD` is not appended, unless the new entrypoint script references
  it).
- Shell form wraps in `/bin/sh -c`, so `$VAR`, globs, and `&&` work, but the
  process is a child of the shell, and PID 1 signal forwarding is the trap
  described in `dockerfile-authoring`.
- A common pattern that *is* correct: `ENTRYPOINT ["docker-entrypoint.sh"]` plus
  `CMD ["postgres"]`, so a script can do setup and then `exec "$@"` — the `exec`
  is what makes the app PID 1.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | clean exit |
| 1 | generic application error |
| 2 | shell misuse (bad flags to `/bin/sh`) |
| 125 | `docker run` itself failed (bad flags, no such image) |
| 126 | container command found but not executable (wrong architecture, bad shebang, no +x) |
| 127 | container command not found (typo, missing binary, wrong `PATH`) |
| 128+N | killed by signal N |
| 137 | 128+9, SIGKILL — `docker stop` after the grace period, **or OOMKilled** |
| 139 | 128+11, SIGSEGV |
| 143 | 128+15, SIGTERM — the app received and handled the stop |

Distinguishing 137-means-`docker stop` from 137-means-OOM: check
`.State.OOMKilled` (`docker inspect --format '{{.State.OOMKilled}}' c`) — it
was verified `false` after a `docker kill`, and `true` for a real OOM. Under
Kubernetes, check `containerStatuses[].lastState.terminated.reason` =
`OOMKilled`.

Exit 126 on an image that works elsewhere is almost always an architecture
mismatch: you built `linux/amd64` and ran on `arm64` (Apple Silicon), or vice
versa. Fix with `--platform linux/amd64` or a proper multi-arch build.

## Capabilities

Linux capabilities split a root uid's power into ~40 bits. A container starts
with a **bounding set** and an **ambient/permitted set**; `--cap-drop=ALL` and
`--cap-add=X` shape the bounding set, and the runtime drops the ones not in it
from the process's effective/permitted/inheritable sets.

The ones that matter for escape or privilege gain:

- `CAP_SYS_ADMIN` — mount, namespaces, many syscalls. Effectively host root.
  The single most dangerous cap.
- `CAP_SYS_PTRACE` — read/attach to any process, including other containers on
  the same node.
- `CAP_SYS_MODULE`, `CAP_SYS_RAWIO` — kernel module load, raw I/O port.
- `CAP_DAC_OVERRIDE` / `CAP_DAC_READ_SEARCH` — bypass file permission checks,
  i.e. read any file in the container's mount namespace regardless of owner.
- `CAP_SETUID` / `CAP_SETGID` — escalate back to root from non-root.
- `CAP_CHOWN` — take ownership of files; enables many persistence tricks.
- `CAP_NET_RAW` — raw sockets: packet sniffing, ARP spoofing, and the classic
  Docker bridge escape via raw ICMP.
- `CAP_NET_ADMIN` — configure interfaces, routes, and iptables.
- `CAP_FOWNER`, `CAP_KILL`, `CAP_MKNOD` — needed by some package managers and
  by process managers, rarely by a server.

Most application containers need **none** of these. A drop-all posture with
`no-new-privileges` is the default to aim for.

## Seccomp and AppArmor

- **seccomp** filters syscalls by number and argument. Docker ships a default
  profile that blocks around 44 of them, including `ptrace`, `mount`, `kexec_load`,
  `bpf`, `perf_event_open`, `userfaultfd`, `process_vm_readv`, `add_key`,
  `request_key`, `clone` with certain namespace flags, and `sethostname`/`setns`
  in some versions. Denied calls fail with `EPERM`. The failure usually looks
  like a library call returning "operation not permitted" at startup.
  `seccomp=unconfined` disables it entirely — never ship that.
  A custom profile is JSON: `defaultAction: SCMP_ACT_ERRNO`, an allowlist of
  `syscalls` with argument filters for the dangerous ones. Build on the
  container's default, not from zero.
- **AppArmor** is a path-and-capability based MAC, mandatory-path based; the
  profile names the executable and the file rules. `apparmor=unconfined`
  disables it. The interaction to remember: a container with a private mount
  namespace and a non-default AppArmor profile can be harder to start (the
  profile must allow the binaries), and that start-up failure is a common
  reason people "fix" it by setting `unconfined` — which is worse than not
  having the profile tuned.
- `no-new-privileges` (`PR_SET_NO_NEW_PRIVS`) is neither seccomp nor AppArmor:
  it is a process flag that makes setuid binaries and file capabilities
  ineffective, and it is a prerequisite for unprivileged user namespaces.
  Always cheap, always on.

## User namespaces

- `--userns-remap` (daemon-level, or per-container) maps container uids onto a
  high host range, so uid 0 in the container is not uid 0 on the host. It
  strengthens the boundary and changes bind-mount ownership (files appear owned
  by the remapped uid on the host). Rootless Docker (`dockerd-rootless.sh` or
  Docker Desktop's rootless mode) combines user-namespace remapping with a
  non-root daemon.
- OpenShift and similar platforms ignore the image's `USER` and force an
  arbitrary high uid. Images that "work as root" fail there. A numeric
  `USER` plus making group 0 own the app directory is the accommodation
  (`chgrp -R 0 /app && chmod -R g=u /app`).

## What the runtime actually does with these flags

`docker run --read-only --cap-drop=ALL --security-opt no-new-privileges` — all
verified working on a stock engine: a normal process runs fine, a write to `/`
fails with `Read-only file system`, and the process is still `uid=0(root)` inside
the container (read-only rootfs and dropped capabilities are *independent* of
the uid; run with `--user` too).

`docker inspect <c> --format '{{json .HostConfig.CapDrop}} {{.HostConfig.Privileged}} {{.HostConfig.ReadonlyRootfs}} {{.HostConfig.SecurityOpt}}'`
is the host-side verification, and it is the one to use for distroless images
that have no `capsh` inside.

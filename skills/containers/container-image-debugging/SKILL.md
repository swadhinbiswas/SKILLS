---
name: container-image-debugging
description: Debug containers from the outside in - docker exec versus docker run, getting a shell into a distroless or scratch image, reading docker inspect state, why a container exits immediately, OOMKilled and exit 137, ENTRYPOINT and CMD override semantics, and the OCI image layout. Use when a container will not start, exits with code 1 or 137, drops its connection, or a command behaves differently under docker run than in production. Triggers on "container exits immediately", "exit code 137", "OOMKilled", "no shell in distroless", "docker exec", "exec /bin/sh not found", "container keeps restarting", "ImagePullBackOff".
compatibility: Examples use Docker CLI 20.10+ (`docker inspect`, `docker logs`, `docker events`, `docker save`). Some fields moved between versions (`GraphDriver` -> `Driver`); check with `docker inspect` on your engine. Kubernetes equivalents live in `kubernetes-debugging`.
metadata:
  version: "1.0"
---

# Container Image Debugging

Debug a container from the outside: what did the runtime try to run, what
code did it exit with, and what did it write before it died. Get a shell
*inside* only as a last resort — most container problems are visible from
`inspect` and `logs`.

## Triage order

Do these in order. Each one eliminates a class of cause.

- [ ] 1. `docker ps -a` — is it running, exited, or restarting?
- [ ] 2. `docker logs <c>` — the last thing it printed. Usually the answer.
- [ ] 3. `docker inspect <c>` — `.State` (ExitCode, OOMKilled, Error, Path,
      Args) and `.Config` (Entrypoint, Cmd, User, Env)
- [ ] 4. `docker inspect <c> --format '{{.Path}} {{.Args}}'` — what was *actually*
      executed, after override
- [ ] 5. Reproduce the exact command with `docker run --entrypoint …` locally
- [ ] 6. Only then: get a shell, or `docker cp` the filesystem out

## Step 1 — is it running?

```sh
docker ps -a --filter name=app --format '{{.Names}}\t{{.Status}}\t{{.Image}}'
```

Status strings and what they mean:

| Status | Meaning |
|---|---|
| `Up 3 minutes` | running |
| `Exited (0) 2 seconds ago` | exited cleanly — the process finished, your `CMD` is wrong (it is a one-shot, not a server) |
| `Exited (1) 2 seconds ago` | application error — read the logs |
| `Exited (137)` | SIGKILL: `docker stop` grace expired, **or** OOMKilled. Check `.State.OOMKilled`. |
| `Exited (139)` | segfault — usually a native module/architecture mismatch |
| `Exited (143)` | SIGTERM handled; a clean stop |
| `Exited (125)` | `docker run` itself failed (bad flags, image missing) |
| `Exited (126)` | command found but not executable (wrong arch, no `+x`, bad shebang) |
| `Exited (127)` | command not found — wrong `PATH`, typo, or a base image without the binary |
| `Restarting (1) 3 seconds ago` | restart policy looping; this is Kubernetes' `CrashLoopBackOff` in miniature |

`Exited (0)` on a server is the most common surprise: you built the image, ran
it, it "worked" (printed nothing and returned). Look at `Config.Cmd` — a `CMD`
that runs a build step, a test, or a REPL exits 0 immediately.

## Step 2 — the logs

```sh
docker logs --tail 200 --timestamps app
docker logs -f app                      # follow
docker logs --since 10m app             # only the last 10 minutes
docker logs app 2>&1 | less             # stderr is a separate stream
```

- **stdout and stderr are separate streams.** An app that only writes to stderr
  shows nothing in a pipeline that drops it.
- **A log driver can swallow everything.** `--log-driver none` or a daemon-wide
  `log-driver` with an aggressive rotation means `docker logs` is empty. Check
  `docker inspect --format '{{.HostConfig.LogConfig}}'`.
- **A buffering runtime produces no logs until exit.** Python without `-u` or
  `PYTHONUNBUFFERED=1` block-buffers when stdout is a pipe; a JVM without a
  console appender does the same. The container looks hung with empty logs. Fix
  in the image (`ENV PYTHONUNBUFFERED=1`, `-XX:+UsePerfData`/`-Djava.util.logging`),
  not by `docker exec`.
- **Multi-line logs are preserved in `docker logs`** but get mangled by
  `docker-compose logs` (it re-emits line by line). For Compose use
  `docker logs $(docker compose ps -q svc)`.
- **No logs at all and an instant exit** usually means the failure is before the
  app starts: bad entrypoint path, missing binary, permission denied. Skip to
  `inspect`.

## Step 3 — read `docker inspect` usefully

The full JSON is a wall. Ask for the fields that matter:

```sh
docker inspect app \
  --format 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} err={{.State.Error}} restarts={{.RestartCount}} started={{.State.StartedAt}}'
```

```sh
docker inspect app \
  --format 'user={{.Config.User}} workdir={{.Config.WorkingDir}}'
docker inspect app --format 'entrypoint={{json .Config.Entrypoint}} cmd={{json .Config.Cmd}}'
docker inspect app --format '{{.Path}} {{.Args}}'          # ACTUAL resolved command
docker inspect app --format '{{json .Config.Env}}'
docker inspect app --format 'image={{.Config.Image}} platform={{.Os}}/{{.Architecture}}'
docker inspect app --format '{{json .HostConfig.RestartPolicy}} {{json .Mounts}}'
```

What to do with each:

- `State.ExitCode` + `OOMKilled` + `Error` together classify the exit. `Error`
  non-empty is a runtime-level failure (bad mount, OCI hook), not an app crash.
- `Path`/`Args` is the *resolved* command after every override. This is the
  fastest way to see that your `docker run --entrypoint sh` replaced the
  entrypoint, or that the image's `CMD` is a shell string.
- `Config.Env` — check `PATH`, `HOME`, and any `*_HOME` your app writes to.
- `Mounts` — a read-only mount where the app needs to write, or a bind mount
  pointing at a path that does not exist on the host (Docker creates a *directory*
  there, silently changing the semantics).
- `HostConfig.LogConfig` — explains empty logs.
- `Config.Healthcheck` — if present, `State.Health.Log` has the last probe
  output. Read it; it is often the only clue in an otherwise silent container.

In one shot, when you need the state of a *specific* field and nothing else:

```sh
docker inspect --format '{{json .State.Health}}' app
docker inspect --format '{{range .State.Health.Log}}{{.ExitCode}} {{.Output}}{{end}}' app
```

## OOMKilled / exit 137

The kernel OOM-killer killed the process; the runtime reports 137 (128+9).
Confirmed behaviour: `docker kill` also produces exit 137 with
`OOMKilled=false`, so **the exit code alone is ambiguous** — always check the
flag.

```sh
docker inspect app --format 'oom={{.State.OOMKilled}} exit={{.State.ExitCode}}'
docker events --filter event=oom --since 30m          # watch for the event
docker stats --no-stream                              # current usage
```

Causes, in order of likelihood:

1. **The app's own heap/per-process limit is bigger than the container limit.**
   A JVM with default ergonomics sizes `-Xmx` from *host* RAM, not the
   container limit, and will be OOM-killed the moment it touches it. A Node
   process and a Python process do the same thing. Set the runtime's limit
   explicitly (see `kubernetes-resource-tuning` for the exact numbers).
2. **A memory limit that is too low for the workload at peak** — e.g. a batch
   job that loads the whole file.
3. **A leak** — memory grows across restarts. `docker stats` twice, a minute
   apart, tells you.
4. **Host-wide OOM** — the kernel killed your process because the *host* ran
   out, not the cgroup. `dmesg | tail -50` (or `journalctl -k`) shows the
   out-of-memory killer's page counts and which process it chose.

Distinguishing 3 from 4: a cgroup OOM appears in `docker events` as an `oom`
event for that container; a host OOM appears in the kernel log with the
process's cgroup in the dump.

## Getting into a container

`docker exec` needs a shell **and** the process to be running.

```sh
docker exec -it app sh                     # alpine/busybox
docker exec -it app bash                   # debian-based
docker exec -u 0 -it app sh                # as root inside, if the user is non-root
docker exec -it app sh -c 'ls -la /app; cat /etc/os-release'
docker exec -it app env | sort             # what the app actually sees
```

Failures and what they mean:

| Error | Meaning |
|---|---|
| `Error response from daemon: Container … is not running` | it exited; you cannot exec into a dead container. Use `docker run` with an override, or `docker cp`. |
| `OCI runtime exec failed: exec failed: unable to start container process: exec: "sh": executable file not found in $PATH: unknown` | **distroless/scratch image — there is no shell.** Expected. |
| `exec: /bin/sh: permission denied` | the exec user lacks permission, or the fs is `--read-only` and `/bin` is fine but something else is not |
| `no TTY allocated` when not using `-it` | cosmetic; drop `-it` for scripted execs |

### Getting a shell into a distroless image

Pick the least invasive option that works:

1. **Run the same image with a shell injected from outside** — start the app
   under a debugger image that shares the PID namespace:
   ```sh
   docker run --rm -it --pid container:app --entrypoint sh nicolaka/netshoot
   nsenter -t 1 -m -u -n -i sh          # PID 1 is the app's process
   ```
   Requires `--pid container:<id>`; the target must be running.
2. **Re-build a debug variant of the same image** — the correct answer for
   anything reproducible:
   ```dockerfile
   FROM gcr.io/distroless/static-debian12
   COPY --from=build /out/app /app
   COPY --from=busybox:1.36 /bin/busybox /busybox    # or: FROM busybox as a stage
   USER 0:0
   ENTRYPOINT ["/busybox", "sh"]
   ```
   A debug stage in the *same* Dockerfile as a `--target debug` you never push.
   Copy a static busybox in; do not `apt-get install` a shell into the shipped
   image.
3. **`docker cp` the filesystem out** for offline analysis:
   ```sh
   docker cp app:/etc/nginx/nginx.conf ./nginx.conf
   docker export app | tar -t | head -100
   docker export app | tar -xO app/etc/passwd
   ```
4. **`docker run --entrypoint … <image> <cmd>`** to run a specific tool from
   the image itself — often the right move for a one-off check
   (`--entrypoint /app/bin/healthcheck`).

Never add a shell to a production image "temporarily" and forget; that is how
remote-code-execution CVEs happen.

## Why a container exits immediately

Work down this list:

1. **`CMD` is not a server.** `docker ps` shows `Exited (0)`. Inspect
   `Config.Cmd` — a build command, a test, or an interactive REPL.
2. **The entrypoint script `exec`s nothing / uses `"$@"` wrongly.** A common
   bug: `ENTRYPOINT ["entrypoint.sh"]` with `CMD` unset, and the script ends
   without `exec`ing anything.
3. **Missing config → the app exits 1 immediately** (no `DATABASE_URL`, failed
   to read a mounted secret, a config file that is not there). The error is in
   the logs; if not, run it with the env visible:
   `docker run --rm --env-file .env -it app sh -c 'env | sort'`.
4. **The entrypoint path is wrong for the stage you built.** A multi-stage build
   that copies the binary to `/app/bin/server` while `ENTRYPOINT ["/app/bin"]`.
   Verified semantics: the entrypoint is resolved inside the *final* image, and
   a relative or wrong path gives exit 126/127.
5. **Architecture mismatch** → exit 126 or "exec format error" (running an
   `amd64` image on `arm64` or vice versa). `docker image inspect img
   --format '{{.Os}}/{{.Architecture}}'`.
6. **`--read-only` and the app needs to write** → usually a clear error, but
   some apps die quietly. Re-run without `--read-only` to confirm.
7. **The process was PID 1 and got SIGTERM immediately** (compose
   `depends_on` churn, orchestrator health check failing, or a `docker stop`
   that raced the start). Check `.State.StartedAt` vs `FinishedAt` and the
   restart count.

Isolate by running the same thing by hand with the overrides you need:

```sh
docker run --rm --entrypoint sh -it app:tag -c 'echo "PATH=$PATH"; id; ls -la /app'
```

## Reproducible debugging recipe

```sh
C=app
docker rm -f $C 2>/dev/null
docker run -d --name $C --init app:tag
sleep 3
docker ps -a --filter name=$C --format '{{.Status}}'
docker logs --tail 100 $C
docker inspect $C --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}} err={{.State.Error}}'
docker inspect $C --format '{{.Path}} {{.Args}}'
docker events --since 1m --until 0s --filter container=$C
```

`--init` matters for the diagnosis too: without it, a `SIGKILL` on stop looks
identical to an OOMKill. See `dockerfile-authoring` for the measured
stop-time difference.

## Gotchas

- **`Exited (0)` is not "the container is broken".** It is "the process
  returned success". A server that exits 0 is a configuration error, not a
  crash.
- **`docker logs` on a restarting container** shows only the current
  instance's output; the previous crash is gone unless the driver keeps it.
  Use `--since` and `docker inspect --format '{{.RestartCount}}'`.
- **A container that restarts loses `/tmp` and any write layer.** Crash reports
  written to the container filesystem are destroyed by the restart. Write them
  to a volume or stdout.
- **`docker cp` works on stopped containers; `docker exec` does not.** That
  asymmetry is the whole debugging strategy when the container will not stay up.
- **`--entrypoint` on `docker run` replaces `ENTRYPOINT` and passes remaining
  args; positional args replace `CMD` entirely.** Verified. If you expected
  your `CMD` to survive `--entrypoint sh`, it does not — you must type the
  command.
- **Env vars from `docker run -e` are not visible in `docker inspect` of the
  image**, only of the container. To reproduce a CI run exactly, use the same
  `--env-file` and `--mount` flags, not the image's defaults.
- **A `HEALTHCHECK` failing does not stop the container by itself** (no
  `restart` behaviour); only orchestrators act on it. `docker run` will happily
  leave a container marked `(unhealthy)` running forever.
- **`docker system prune` deletes stopped containers and their logs.** Take a
  `docker cp` or save the logs before cleaning up a failed deployment.
- **`docker inspect` on a name that matches several containers** requires the
  id; use `$(docker compose ps -q svc)`.
- **A multi-stage `--target` built in dev is not the shipped image.** When the
  behaviour differs, compare `docker history` between the two.
- **`no such file or directory` on `docker run` of a locally-built image** after
  a rebuild with a different base means a stale image id; `docker image prune`
  and rebuild. The name resolves to the old dangling image.
- **Windows line endings in an entrypoint script** produce
  `/entrypoint.sh: not found` (the `\r` becomes part of the interpreter path).
  Keep scripts LF. This looks exactly like a missing file.

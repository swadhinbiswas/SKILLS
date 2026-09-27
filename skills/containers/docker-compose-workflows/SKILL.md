---
name: docker-compose-workflows
description: Build real local development environments with Docker Compose - the file structure that scales, named volumes versus bind mounts and the Linux UID permission trap, healthchecks with depends_on service_healthy, profiles, .env handling, networks, and one-shot seed containers. Use when someone needs Postgres plus Redis plus a mail catcher on a laptop, when a dev environment takes minutes to start, or when a container writes files the host cannot read. Also covers when Compose is the wrong tool. Triggers on "docker-compose.yml", "compose file", "local dev environment", "seed data", "profiles", "permission denied bind mount", "depends_on".
compatibility: Examples use Docker Compose v2 (`docker compose`, the plugin, not `docker-compose` v1). v2 ignores the v1 `version:` key. Requires Docker Engine 20.10+ for most examples; healthcheck `start_period` and `service_healthy` need a recent engine.
metadata:
  version: "1.0"
---

# Docker Compose Workflows

Compose is a local development and CI harness, not an orchestrator. Its whole
job is: start these services in the right order, on one network, with the right
env, fast, and tear down cleanly.

## The file structure that scales

Compose merges in a defined order; later files win. Use one file per concern
and an `override` file that is *not* committed.

```
compose.yaml            # base: images, ports, volumes, healthchecks, networks
compose.override.yaml   # local-only (gitignored): bind mounts, expose, fast refresh
```

```sh
docker compose up -d                      # auto-loads compose.override.yaml
docker compose -f compose.yaml up -d     # CI / production-shaped: no overrides
docker compose config                     # merged, resolved, interpolated YAML
```

`docker compose config` is the single most useful command: it shows the
effective config after interpolation, profile handling, and override merging, and
it fails loudly on a typo'd key. Use it in CI.

Base file:

```yaml
name: myapp                # project name; keeps container/volume names stable

x-common: &common          # YAML anchor: shared env, reusable without duplication
  restart: unless-stopped
  logging:
    driver: json-file
    options: { max-size: "10m", max-file: "3" }

services:
  db:
    image: postgres:16-alpine
    <<: *common
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: ${DB_PASSWORD:-devpass}
      POSTGRES_DB: app
    command:
      - postgres
      - -c
      - max_connections=200
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 2s
      timeout: 2s
      retries: 30
      start_period: 10s
    volumes:
      - pgdata:/var/lib/postgresql/data
    networks: [backend]

  api:
    build:
      context: .
      target: development       # multi-stage: the dev target has hot reload
    <<: *common
    command: npm run dev
    ports: ["3000:3000"]
    environment:
      DATABASE_URL: postgres://app:${DB_PASSWORD:-devpass}@db:5432/app
      NODE_ENV: development
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - .:/app
      - /app/node_modules          # anonymous volume: keeps container node_modules
    networks: [backend, frontend]

  mailcatcher:
    image: sj26/mailpit:latest
    <<: *common
    ports: ["8025:8025", "1025:1025"]
    networks: [frontend]

volumes:
  pgdata:

networks:
  frontend:
  backend:
    internal: true          # no egress to the outside world
```

Override file (gitignored):

```yaml
services:
  api:
    volumes:
      - .:/app
    environment:
      LOG_LEVEL: debug
  db:
    ports: ["5432:5432"]     # so host tools can connect
```

Gotcha in the anchor above: YAML merge keys do not deep-merge. `<<: *common`
followed by your own `volumes:` replaces the anchor's `volumes` entirely; it
does not append. Verified with `docker compose config`.

## Ordering: healthchecks, not sleep

`depends_on` with the short form only waits for the container to **start**, not
to be usable. Postgres is listening 2-3 s after "started", and your app will
crash with `connection refused` on first boot.

```yaml
depends_on:
  db:
    condition: service_healthy    # waits for the healthcheck to pass
  migrate:
    condition: service_completed_successfully   # v2.20+; for one-shot jobs
```

`service_healthy` requires a `healthcheck` on the target service. Without one,
Compose warns and the dependency is effectively ignored.

Compose v2.20+ adds `condition: service_completed_successfully`, which is the
correct way to express "run migrations before the app starts":

```yaml
  migrate:
    build: { context: ./migrate }
    command: ["alembic", "upgrade", "head"]
    environment:
      DATABASE_URL: postgres://app:${DB_PASSWORD:-devpass}@db:5432/app
    depends_on:
      db: { condition: service_healthy }
    restart: "no"
  api:
    depends_on:
      migrate: { condition: service_completed_successfully }
```

`restart: "no"` matters — a failing migration that restarts forever hides the
error. Leave the failed container in place so you can `docker compose logs
migrate`.

## Named volumes vs bind mounts

| | Bind mount `- ./src:/app` | Named volume `- nodemod:/app/node_modules` |
|---|---|---|
| Who owns the files | host UID | root (or the image's `USER`) |
| Host sees container writes | yes, immediately | no |
| Survives `down -v` | yes | no (deleted) |
| Good for | source code, live reload | databases, package installs, caches |

**The Linux UID trap.** Your host user is uid 1000. A container running as
uid 1000 (from `USER 1000:1000` in the Dockerfile) writes files owned by 1000 —
fine. A container running as **root** writes as uid 0, and the bind-mounted host
directory now contains root-owned files you cannot edit or delete without
`sudo`. Symptom: `permission denied` on the host immediately after `docker
compose up`, or `EACCES: permission denied, open '/app/node_modules/.cache'`
inside the container.

Three fixes, in order of preference:

1. **Run the container as the host uid.** Declare the uid as a build arg so the
   image matches whoever is building it:
   ```dockerfile
   ARG UID=1000
   ARG GID=1000
   RUN groupadd -g ${GID} app && useradd -u ${UID} -g ${GID} -m app
   USER ${UID}:${GID}
   ```
   ```sh
   docker compose build --build-arg UID=$(id -u) --build-arg GID=$(id -g)
   ```
   Pin the same values in `compose.yaml` so `docker compose up` alone is
   correct: `build: { args: { UID: "${LOCAL_UID:-1000}", GID: "${LOCAL_GID:-1000}" } }`.
2. **Named volume for the writable-but-not-source directories** (the
   `node_modules` trick above) so the container never writes to the host tree.
3. **Fix up after the fact** — last resort, and it is not idempotent:
   `sudo chown -R "$(id -u):$(id -g)" ./src`.

macOS and Windows Docker Desktop run Linux containers in a VM and present a
consistent owner regardless, so this problem is Linux-host-only. Do not spend
engineering on it for a mac-only team, and do assume it exists for a Linux CI
runner.

## `.env` handling

Compose loads `.env` from the **project directory** (next to the compose file,
or the `--project-directory` value) automatically, and uses it for
`${VAR}` interpolation in the compose file. It is *not* automatically injected
into containers — that needs `env_file:` or explicit `environment:`.

```yaml
services:
  api:
    env_file: [.env]              # every key in .env becomes a container env var
    environment:
      LOG_LEVEL: ${LOG_LEVEL:?set LOG_LEVEL in .env}   # ${:?} = hard error if unset
```

- `${VAR:-default}` uses a default; `${VAR:?message}` **fails the whole
  command** if unset. Use `${VAR:?…}` for anything required in a real
  environment — a silent empty string is how a service comes up pointed at
  localhost.
- `.env` is a file, not a secret store. Commit `.env.example` with the keys and
  empty values; gitignore `.env`. Real secrets go in the orchestrator's secret
  store — see `kubernetes-manifests` and `container-runtime-security`.
- `env_file: .env` **overrides nothing** by default; if a key is in both
  `env_file` and `environment:`, `environment:` wins.
- `docker compose config` prints the *interpolated* result — that means
  **secrets from `.env` are printed to your terminal and into CI logs.** Do not
  paste the output of `docker compose config` into a bug report.

## Profiles

Profiles let you keep optional services in the same file without starting them.

```yaml
services:
  db:      # no profile: always starts
  api:     # no profile: always starts
  debugger:
    image: nicolaka/netshoot
    profiles: ["debug", "tools"]
    cap_add: [SYS_PTRACE]      # needed to attach to other containers
  testrunner:
    profiles: ["ci"]
    command: ["pytest", "-q"]
```

```sh
docker compose up -d                      # db + api only
docker compose --profile debug up -d      # + debugger
docker compose --profile debug config --services   # verify before starting
```

Verified: a service with `profiles: ["debug"]` is absent from
`docker compose config --services` by default and present with
`--profile debug`. This is the clean way to have one compose file serve
"minimal laptop", "I need to debug", and "CI".

Gotcha: if a profiled service is a dependency of a non-profiled one, it does
**not** get auto-enabled — you must pass the profile explicitly.

## Networks

Every service joins a default network automatically and resolves every other
service **by service name as a hostname** (`db`, not `localhost`). That is why
`DATABASE_URL=postgres://…@db:5432` works.

```yaml
networks:
  frontend:
  backend:
    internal: true     # no outbound internet: nothing inside can exfiltrate
```

`internal: true` on the db network is a genuinely useful default: a database
with an egress path to the internet is a one-command exfiltration. It also
means the db cannot pull packages at runtime, which is what you want anyway.

Rules:

- Split frontend (web-exposed) from backend (data) and put the db only on
  backend. Verified default: `docker compose config` materialises
  `<project>_default` and attaches every service to it.
- `ports:` publishes to the host; `expose:` documents intra-network access
  only. Use `expose` for anything the host does not need.
- Hostname resolution is only for services on a **shared** network. Two
  services on disjoint networks cannot resolve each other by name — that is the
  point of the split, but it surprises people.
- On Linux, the host reaches published ports on `127.0.0.1`; on Docker Desktop
  the same port is on the VM and forwarded. If something works on a Mac and not
  on a Linux CI runner, check the bind address.

## One-shot jobs: seeding, migrations, fixtures

```yaml
  seed:
    build: { context: ./seed }
    profiles: ["seed"]
    environment:
      DATABASE_URL: postgres://app:${DB_PASSWORD:-devpass}@db:5432/app
    depends_on:
      db: { condition: service_healthy }
    restart: "no"
```

```sh
docker compose --profile seed run --rm seed
```

`run --rm` (not `up`) for a job: it runs once, exits, and removes the container.
`restart: "no"` prevents a restart loop. `--build` on `run` rebuilds the image
first if you changed the seed code. A migration that must run on **every** `up`
needs its own version table — that is the difference between "idempotent" and
"works on a fresh database only".

## Daily commands

```sh
docker compose up -d --wait          # wait for healthchecks before returning
docker compose ps                     # STATUS shows (healthy) / (unhealthy)
docker compose logs -f --tail=100 api
docker compose exec api sh            # shell into a running service
docker compose run --rm --entrypoint sh api    # shell in a one-off container
docker compose restart api
docker compose up -d --build api      # rebuild + recreate
docker compose down                   # remove containers + networks, KEEP volumes
docker compose down -v                # DESTRUCTIVE: also deletes named volumes
docker compose config --services
docker compose config --quiet         # syntax/interpolation check for CI
```

`--wait` (v2.17+) blocks until every service with a healthcheck is healthy or
the timeout expires. Use it in any script; it removes an entire class of flake.

Logs: `docker compose logs` re-emits multi-line application logs as single
lines, which destroys stack traces. For raw logs, use
`docker logs $(docker compose ps -q api)`.

## When Compose is the wrong tool

| Need | Use instead |
|---|---|
| Restart a crashed container on a server you cannot babysit | systemd unit, or a real orchestrator |
| Rolling updates with zero downtime | Kubernetes / ECS / Nomad |
| Horizontal scaling across hosts | anything but Compose |
| Resource limits, quotas, network policy at scale | Kubernetes (`kubernetes-manifests`) |
| Secrets that must not be in a file on disk | orchestrator secret store / cloud KMS |
| Production hosting | Compose is not a production orchestrator. It has no rescheduling, no self-healing across reboots, and `--wait` is a convenience, not a health guarantee. |

The honest summary: Compose is excellent for "six services on my laptop, started
in the right order, in 20 seconds" and inadequate for everything else. Do not
present a `docker compose up` on a single VM as a deployment strategy without
saying that out loud.

## Gotchas

- **`version:` key** is obsolete in v2 and is ignored. Its presence signals an
  old file; remove it.
- **The v1/v2 split bites twice**: `docker-compose` (v1, Python) and
  `docker compose` (v2, plugin) do not have identical `up` flags. Use v2
  throughout; check `docker compose version`.
- **`container_name:` breaks scaling and `--scale`** and forces one project per
  host. Let Compose name containers `<project>_<service>_<n>` and address them
  as `<service>` on the network.
- **A bind mount of the whole repo shadows the image's own files.** If the image
  has `/app/dist` built in and you mount `.:/app`, the `dist` disappears. Mount
  subdirectories, or use anonymous volumes for the build output.
- **Named volumes survive `down`, so a "fresh" dev environment is not fresh.**
  When a migration breaks, `docker compose down -v` is the reset — and it
  destroys data, so gate it.
- **Rebuild is not automatic.** Editing a `Dockerfile` or `compose.yaml` does
  not change a running container. `up -d` may also not rebuild if the image
  name and tag are unchanged — use `up -d --build`.
- **Compose reads `.env` from the project directory, not the CWD**, and the
  project directory is where the *first* compose file lives. Running
  `docker compose -f path/to/compose.yaml` from elsewhere can silently
  interpolate a different `.env`. Use `--project-directory` to pin it.
- **`depends_on` is not a health dependency by default** and, importantly, it
  does nothing about the *app* being ready — only about its dependency.
- **Every service on the default network can reach every other service**,
  including your database's admin port, unless you split networks.
- **Healthcheck `CMD-SHELL` runs `/bin/sh -c`** — a distroless-based service
  cannot have one, and the `test` must be a *single* argument when exec-form.
  `test: ["CMD", "curl", "-f", "http://localhost/"]` runs curl directly, with
  no shell.

## Local dev environment checklist

- [ ] Every long-running service has a `healthcheck` and its dependents use
      `condition: service_healthy`
- [ ] Jobs (migrate/seed) use `restart: "no"` and are invoked with
      `run --rm`
- [ ] No root-owned files appear in the bind-mounted tree
- [ ] The db is on an `internal: true` network, not the frontend one
- [ ] `.env.example` committed, `.env` ignored, and no `docker compose config`
      output pasted anywhere
- [ ] `compose.override.yaml` is gitignored
- [ ] `docker compose config --quiet` passes in CI
- [ ] `docker compose up -d --wait` is what CI runs, not `sleep 10`

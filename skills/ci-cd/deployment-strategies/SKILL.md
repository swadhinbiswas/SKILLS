---
name: deployment-strategies
description: Choose and implement a deployment strategy - rolling, blue/green, canary, recreate, and feature flags - including how each handles rollback, why schema changes break rolling deploys, and the expand-contract pattern for zero-downtime migrations. Use when planning a deploy, when planning a rollback, when a deploy caused downtime or a partial outage, when a migration is about to run against a live app, or when someone asks "how do we deploy without downtime". Triggers on "blue/green", "canary", "rolling deploy", "zero downtime", "rollback", "feature flag", "expand-contract", "backward compatible migration", "downtime".
compatibility: Strategy-agnostic; examples use Kubernetes, Alembic/Django, Flyway, and general SQL. Verify the exact flags for your platform's tooling (kubectl, helm, your CI deploy step) with --help.
metadata:
  version: "1.0"
---

# Deployment Strategies

Every strategy is a trade between **blast radius**, **rollback speed**, and
**resource cost**. Pick with the failure mode in mind, not the marketing.

## Choosing

| Strategy | Blast radius | Rollback | Cost | Use when |
|---|---|---|---|---|
| Rolling | Full, gradual | Fast (revert to previous ReplicaSet) | 1× | Default for stateless HTTP services. Capacity must absorb one surge |
| Blue/green | Instant cutover | Instant (flip the service selector back) | 2× | You need an all-or-nothing switch and can pay for double capacity |
| Canary | Tiny (1% then ramp) | Very fast, and the exposure was tiny | ~1.1× | High-traffic, where a bad release would be visible immediately; release confidence |
| Recreate | Total downtime | Redeploy old | 1× | Single-instance apps that cannot run two versions at once (stateful, schedulers) |
| Feature flag | Zero (code deployed, off) | Flip the flag | 1× | Decoupling release from deploy; risky changes; gradual rollout by user segment |

House defaults:

- **Stateless service with ≥2 replicas → rolling deploy.** It is the cheapest and
  the rollback is a `kubectl rollout undo`. Move to canary only when the
  traffic justifies the extra machinery.
- **Anything stateful or with a schema → expand-contract, plus a blue/green or
  canary for the code.** See "Database compatibility" below.
- **Risky behaviour change → ship it behind a flag**, deploy the flag off. This
  is the only strategy whose rollback does not need a deploy at all.

Read `references/strategy-matrix.md` when you need the per-platform mechanics
(kubernetes manifests, Nginx/ALB weights, Flyway/Alembic sequences) or the
decision tree for a specific situation.

## Rolling deploys

New pods come up, readiness gates them, old pods drain. The subtlety that
breaks most teams: **during the rollout, two versions of the code are serving
traffic simultaneously**, and they are talking to the same database, the same
cache, and each other.

Requirements for a safe rolling deploy:

- **Readiness must mean "can serve", not "process started".** A readiness probe
  that passes before the app can reach the database produces errors the whole
  time the pod is being registered.
- **Overlapping versions must be compatible** with each other *and* with the
  schema. Old code will still be running when the new code lands. If the new
  version changes a response shape the old version's clients cannot parse, or
  the reverse, you will get intermittent errors that look like flakiness.
- **Capacity must absorb the surge.** If 100% of pods are serving and you start
  more, the service degrades before it improves. Use `maxSurge: 1, maxUnavailable:
  0` (or a surge percentage) so the new pod is ready before an old one goes.
- **`preStop` hook + `terminationGracePeriodSeconds`** to drain in-flight
  requests. Without it, pods are killed mid-request on every deploy and users
  see sporadic 502s — a "deploy-related" flake that engineers learn to ignore.
- **Never restart everything at once.** A `kubectl delete pods --all` is a
  self-inflicted outage, not a deploy.

```yaml
# Kubernetes rolling update (fields verified against the Deployment spec)
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  template:
    spec:
      terminationGracePeriodSeconds: 45
      containers:
        - name: app
          readinessProbe:
            httpGet: { path: /readyz, port: 8080 }
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 10
            periodSeconds: 10
            failureThreshold: 3
```

(`/readyz` checks dependencies; `/healthz` checks only "the process is alive" —
conflating them means a database blip restarts every pod instead of removing
them from the load balancer.)

## Blue/green

Two identical environments, only one receives traffic. The cutover is a single
switch.

- **Instant rollback:** flip back. This is its main advantage over rolling, and
  the reason it is chosen for high-stakes deploys.
- **Cost: 2× capacity** while both are up.
- **State must be shared or compatible.** Blue and green write to the same
  database, so a schema change must satisfy *both* versions. Same expand-contract
  rule.
- **Sessions and caches:** sticky sessions mean cutting over drops in-flight
  sessions unless state is externalised (shared session store, signed cookies).
- **Cold start:** green has empty caches and cold JIT. Warm it with a shadow
  request or accept a slow first few minutes behind a lower weight.

## Canary

A small slice of traffic on the new version, ramped on metrics.

- **Shard by something meaningful**: a header, a cookie, a user id hash, or a
  geography. Sharding by a *request* attribute can put one user's session on two
  versions.
- **Ramp on a metric, not a timer.** "1% for 5 min, 10% for 15 min, 50%, then
  100%" gated on error rate and latency vs the baseline. Automated analysis
  (Flagger, Argo Rollouts, Kayenta) is the house default for services with
  enough traffic; manual gates are fine below a few hundred rps.
- **Watch the right metrics:** error rate, p95/p99 latency, saturation (CPU,
  queue depth), and one business metric (checkout completion). A canary that
  only watches 5xx will happily pass a version that is functionally broken.
- **The canary's sample size must be sufficient.** At 1% of 10 rps, 5 minutes
  is 3000 requests — enough for an error-rate signal, not enough for a rare
  path. Raise the percentage for low-traffic services or accept a long ramp.
- **Database migrations run once, not per canary.** The migration is not
  canaried; the code is.

## Recreate

Stop everything, deploy, start. For single-instance or stateful apps (a
scheduler, a consumer, a database, a legacy monolith that writes to local disk).

- **Accept the downtime, but minimise it**: pre-pull the image on the node, warm
  caches, make startup fast, and do it in a maintenance window.
- **Do not use it for a service with more than one instance that can be
  drained** — you are choosing downtime you do not need.

## Feature flags

Deploy the code, control the behaviour separately.

- **The flag is the rollback.** Turning it off is instant and does not need a
  deploy. This is why flags are the right tool for a risky refactor even when
  the deploy strategy is rolling.
- **Flags accumulate.** Every flag is a permanent branch. Set an expiry date and
  a cleanup ticket at creation; delete the flag and the dead branch when the
  rollout completes.
- **Types:** release flags (off at deploy, on at launch), ops flags (kill
  switch, on by default), experiment flags (targeting, on by default for
  control), permission flags (on by default, granting a new capability). Each
  has a different default and a different removal criterion.
- **Never nest flags** — a flag inside an `if` inside an `if` is untestable and
  unremovable.
- **Evaluate flags on the server** for anything sensitive; a client-side flag is
  visible in the browser and is not a security control. Permission and kill
  switches belong on the server.
- **Flags need a fast read path** with a local default: if the flag service is
  down, the app must use the cached/last-known value, not fail. A flag service
  that is a hard dependency converts a flag outage into a full outage.
- **Cache the evaluation, not just the value** (a local TTL of seconds to
  minutes) so the flag service is not on the hot path of every request.

## Database compatibility with rolling deploys — the rule that breaks things

**Never ship a schema change and a code change that requires it in the same
deploy.** During a rolling deploy, old and new code run at the same time
against the same database. Therefore, for the duration of the rollout, **both
versions must work with the schema as it exists after the migration**.

The two safe patterns:

### Expand-contract (the default; works with every strategy)

Every schema change is split into steps that are each independently safe, with
the code deployed **in the middle**:

1. **Expand** — additive only. Add the new column (nullable, or with a
   constant default), add the new table, add the new index. Nothing the old code
   depends on changes.
2. **Migrate** — deploy code that can read the old shape, write both, and read
   the new shape when present. Dual-write. Backfill in batches. This deploy is
   safe with the old code running because the old code ignores the new column.
3. **Contract** — after all instances are on the new code and the backfill is
   verified, drop the old column/table/constraint. This is a separate deploy,
   and it can be a MAJOR version if the old shape was public.

```sql
-- Expand: additive, instant, safe with the old code still running.
ALTER TABLE orders ADD COLUMN total_cents bigint;         -- nullable
CREATE INDEX CONCURRENTLY idx_orders_total_cents ON orders (total_cents);

-- Migrate: in batches, in separate transactions, never one big UPDATE.
UPDATE orders SET total_cents = (price_cents * qty) / 100
WHERE id IN (SELECT id FROM orders WHERE total_cents IS NULL LIMIT 5000);

-- Contract: only after every instance reads and writes the new column.
ALTER TABLE orders ALTER COLUMN total_cents SET NOT NULL;
ALTER TABLE orders DROP COLUMN price_cents;
```

For Django/Alembic, that is three separate migrations (expand, no-op code, drop)
with an app deploy between them. Never combine the `add` and the `drop` in one
migration.

### Backward-compatible changes only

If you can only do one deploy, the change must satisfy **all** of these:

- Adding anything (column, table, endpoint, optional parameter) — additive.
- Never rename. Rename = add + dual-write + drop, three deploys.
- Never drop in the same deploy. Drop a full release later.
- Never tighten a constraint the old code might violate. `NOT NULL` requires
  `ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT` (Postgres takes a
  weak lock for the validate).
- Changing a type is a drop+add: write a new column, backfill, switch reads,
  drop old.
- Adding a required request parameter: add it optional with a default first;
  make it required a release later.

**Migration ordering within a deploy:** schema first, then code. If the code
needs the new column, the column must exist before any new pod starts. The
reverse order fails on the first new pod.

## Rollback

Know the rollback before the deploy, not during the incident.

| Situation | Rollback |
|---|---|
| Bad code, no schema change | Redeploy the previous artifact (by digest). Rolling/blue-green: fast |
| Bad code, expand-contract | Redeploy the previous artifact. The new column is harmless — the old code ignores it. **This is why expand-contract works** |
| Bad migration, still reversible | `down` migration, but check the code contract first: if the new code already wrote rows the old code cannot read, revert the code first, then the schema |
| Bad migration, not reversible | Restore from backup (you have data loss) or forward-fix. **Assume every migration is irreversible in your planning** |
| Bad behaviour behind a flag | Turn the flag off. No deploy |
| Bad deploy, schema already dropped something | You cannot roll back the code. Forward-fix only. **This is the state expand-contract exists to avoid** |

Rules:

- **Rollback the code, not the data, by default.** Data rollback is a restore,
  and a restore is an incident of its own.
- **The previous artifact must still exist.** Immutable tags/digests retained
  for N releases. A "rollback" that cannot find yesterday's image is not a
  rollback.
- **Backwards migrations are rarely tested.** Test the `down` path in CI, or
  write forward-only migrations and accept that rollback is "deploy the old
  code", which only works with expand-contract.
- **When to roll back vs forward-fix:** roll back when the fix is known and the
  previous version was healthy. Forward-fix when the previous version has a
  known bug the new release was fixing, or when the rollback would lose data
  written in the new format. Do not debug in production: if it is unclear
  within a few minutes, roll back and investigate off-production.
- **A canary aborts automatically**; a rolling deploy does not. If you cannot
  automate the abort signal, you need a human watching the dashboard — which is
  a real operational cost of the strategy.

## Safety notes

- **Never deploy on a Friday afternoon** with no one watching, and never
  deploy a migration and a code change together on a day you cannot roll back.
- **Never run a destructive migration without a verified backup and a
  rehearsed restore.** A backup you have not restored from is a hypothesis.
- **Never combine a schema change with a data backfill in one statement** on a
  large table. The backfill holds locks and bloats WAL; batch it.
- **A canary ramp that is not monitored is a slow full deploy.** If nobody is
  watching the metric, the canary is theatre.
- **Dry-run destructive deploys** (irreversible migrations, a first-time
  platform change) against a production-shaped snapshot in a non-production
  environment first. The cost of a rehearsal is hours; the cost of skipping it is
  an outage.

## Gotchas

- **Readiness vs liveness:** a liveness probe that checks dependencies will
  restart every pod during a downstream outage, turning a partial degradation
  into a full outage (restart loops, cold caches, no capacity). Liveness =
  process; readiness = can serve.
- **`maxUnavailable: 0` + a bad new version = a stuck rollout**, not a failure:
  no old pod is removed until new pods are ready, so the deploy hangs while the
  old version keeps serving. That is the *correct* failure mode, but you need a
  timeout that rolls it back, or the deploy sits forever.
- **A rolling deploy with sticky sessions** sends a returning user to a pod
  that may be the other version mid-request. Externalise session state or use
  a version-aware cookie.
- **Long-lived connections** (websockets, gRPC streams) are not drained by
  `preStop`; clients see a drop on every deploy. Expect it and make reconnect
  cheap and fast.
- **Connection pool churn:** each new pod opens a fresh pool at startup, so a
  rolling deploy can exceed the database's `max_connections` mid-rollout. Size
  `pool_size × max_pods + old_pods` under the limit, or use a pooler.
- **A migration that takes a lock** will block for the whole rolling deploy,
  because old pods' queries queue behind it — and the deploy's health checks may
  time out and roll back mid-migration, leaving a partial state. Long-running
  migrations need `lock_timeout` and a separate operational procedure, not the
  deploy pipeline.
- **Feature flags read per-request from a remote service** add latency and a
  failure mode. Cache locally with a short TTL and a last-known-good default.
- **Canary + sticky sessions** can put a single user's requests on two versions
  and produce errors that no aggregate metric shows. Shard by user, not
  session, or the canary's own results are unreliable.
- **"Recreate" on a service with an external queue** silently drops the in-flight
  messages the old instance held. Recreate needs a drain/ack step first.
- **Rollback is not the same as roll-forward.** A deploy that requires a
  forward-only migration cannot be rolled back; the expand-contract pattern is
  what makes rollback real.

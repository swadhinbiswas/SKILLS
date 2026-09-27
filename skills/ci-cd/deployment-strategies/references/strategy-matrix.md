# Strategy matrix and per-platform mechanics

## Decision tree

1. **Is the change reversible without a deploy (feature flag, config, or
   weight shift)?** → Use the flag/weight. Nothing else compares.
2. **Can two versions run simultaneously against the same data store?** If no
   (stateful app, scheduler, a schema change that is not expand-contract), then
   blue/green and canary do not help: you must use **recreate** or fix the
   compatibility first.
3. **Is the traffic high enough for a canary signal?** Below a few hundred rps,
   a 1% canary has too few requests; use a larger percentage, a longer ramp, or
   blue/green with a smoke check.
4. **Is the cost of 2× capacity acceptable for a risky release?** If yes →
   blue/green (instant cutover and instant rollback). If no → rolling with a
   canary phase.
5. **Is the change data-mutating or schema-touching?** → expand-contract, always.
   The strategy choice is secondary to getting the compatibility right.

## Kubernetes: the manifests

### Rolling

```yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: api }
spec:
  replicas: 6
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  template:
    spec:
      terminationGracePeriodSeconds: 45
      containers:
        - name: api
          image: registry.example.com/api@sha256:<digest>
          ports: [{ containerPort: 8080 }]
          readinessProbe:
            httpGet: { path: /readyz, port: 8080 }
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /healthz, port: 8080 }
            periodSeconds: 10
            failureThreshold: 3
          lifecycle:
            preStop:
              exec: { command: ['sleep', '10'] }   # let endpoints deregister first
          resources:
            requests: { cpu: 500m, memory: 512Mi }
            limits:   { memory: 1Gi }             # no CPU limit: it causes throttling
```

```bash
kubectl apply -f deploy.yaml
kubectl rollout status deployment/api --timeout=5m
kubectl rollout undo deployment/api          # instant rollback
kubectl rollout history deployment/api
kubectl rollout restart deployment/api       # restart after a config change
```

Note the memory-only limit. A CPU limit makes the kubelet throttle the
container, which looks exactly like an application slowdown and breaks
canary analysis.

### Blue/green

Blue and green are two Deployments (`api-blue`, `api-green`) behind one Service.
The cutover is changing the Service selector, which is why rollback is instant.

```yaml
# service.yaml — the selector is the switch
apiVersion: v1
kind: Service
metadata: { name: api }
spec:
  selector: { app: api, slot: blue }     # <-- flip to green to cut over
  ports: [{ port: 80, targetPort: 8080 }]
```

```bash
kubectl apply -f deploy-green.yaml
kubectl rollout status deployment/api-green --timeout=5m
kubectl run smoke --rm -it --image=curlimages/curl --restart=Never -- \
  curl -fsS https://staging.example.com/readyz        # smoke before the flip
kubectl patch service api -p '{"spec":{"selector":{"app":"api","slot":"green"}}}'
# rollback: patch the selector back to blue
```

Keep both slots until the new version has been healthy for a full business
period, so the rollback target is warm and present.

### Canary (native)

Native Kubernetes has no weighted routing; use a service mesh or an ingress
that supports weights. Argo Rollouts and Flagger wrap this with metric-driven
analysis.

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata: { name: api }
spec:
  replicas: 6
  strategy:
    canary:
      canaryService: api-canary
      stableService: api-stable
      trafficRouting:            # requires a mesh (Istio/Linkerd) or a capable ingress
        istio: { virtualService: { name: api, routes: [primary] } }
      analysis:
        interval: 2m
        count: 10
        successCondition: result.http.success-rate > 0.995
        failureLimit: 3
        metrics:
          - name: success-rate
            successCondition: result.http.success-rate > 0.995
          - name: latency
            successCondition: result.http.p95 < 300
          - name: business            # one non-infrastructure metric
            successCondition: result.custom.checkout_completion > 0.98
      steps:
        - setWeight: 1
        - pause: { duration: 5m }
        - setWeight: 10
        - pause: { duration: 15m }
        - setWeight: 50
        - pause: { duration: 15m }
```

## Nginx and AWS ALB: weight-based canary

```nginx
# Nginx: split by a request header set by a canary client, or by a cookie.
upstream api_stable { server 10.0.1.10:8080; server 10.0.1.11:8080; }
upstream api_canary { server 10.0.2.10:8080; }

map $http_x_canary $backend_pool {
    default   api_stable;
    "true"    api_canary;
}
server {
    location / { proxy_pass http://$backend_pool; }
}
```

```bash
# AWS ALB target groups: two TG attachments on the same listener rule.
aws elbv2 modify-target-group-attributes \
  --target-group-arn "$TG_STABLE" --attributes Key=weight,Value=99
aws elbv2 modify-target-group-attributes \
  --target-group-arn "$TG_CANARY" --attributes Key=weight,Value=1
# Roll back: set both weights back, or detach the canary TG.
```

Weighted targets require weighted target groups on the target group, not the
load balancer — check your provider's current model before scripting it.

## Migrations as part of the deploy

### Ordering rule

**Schema first, then code.** The new column must exist before the first new
pod starts; if the code needs it and it is not there, the new pods crash-loop.
Roll back the code if the deploy fails — the additive schema is harmless.

### Django / Alembic: three migrations, not one

```python
# 0042_expand_total_cents.py — additive only, instant
def upgrade():
    op.add_column("orders", sa.Column("total_cents", sa.BigInteger(), nullable=True))
    op.create_index_concurrently("idx_orders_total_cents", "orders", ["total_cents"])

# 0043 is a no-op migration whose purpose is to be the deploy boundary:
# the app code that dual-writes lands here.
def upgrade():
    pass

# 0044_contract_total_cents.py — only after every instance runs the new code
def upgrade():
    op.alter_column("orders", "total_cents", nullable=False)   # after the backfill
    op.drop_column("orders", "price_cents")
```

`create_index_concurrently` cannot run inside a transaction; on Postgres that
means the migration must set `atomic = False` (Django) or be run outside a
transaction (Alembic: `op.get_context().autocommit_block()`), and it can leave
an `INVALID` index behind on failure that you must drop before retrying.

### Flyway / Liquibase

The same three-phase discipline, expressed as ordered, repeatable migrations.
Use *repeatable* migrations only for views and functions (they re-run on checksum
change); use *versioned* migrations for schema changes, and never edit an applied
versioned migration — add a new one (Flyway validates checksums of applied
migrations and will fail on a rewritten one).

### Backfill batching

```sql
-- Repeat in separate transactions until zero rows remain. A single big UPDATE
-- holds locks, bloats WAL, and can trigger a long vacuum.
UPDATE orders SET total_cents = price_cents * qty
WHERE id IN (SELECT id FROM orders WHERE total_cents IS NULL LIMIT 5000);
```

Run it from a separate job (or a scheduled task), not from the deploy pipeline:
it may take hours, and a deploy that takes hours is a deploy that blocks a
rollback. Track progress (`SELECT count(*) … WHERE total_cents IS NULL`) and
make the migration's "contract" step conditional on it reaching zero.

## Feature flag implementation notes

```python
class Flags:
    """Local cache with a last-known-good default: a flag service outage must
    not become an application outage."""
    def __init__(self, client, default_ttl=30):
        self._client, self._ttl, self._default = client, default_ttl, {}
        self._cache = {}

    def enabled(self, name: str, default: bool = False) -> bool:
        now = time.monotonic()
        if name not in self._cache or self._cache[name][0] < now:
            try:
                value = self._client.get_bool(name)     # may raise
            except Exception:
                value = self._default.get(name, default)
            self._cache[name] = (now + self._ttl, value)
        return self._cache[name][1]
```

- **Server-side evaluation** for permission and kill-switch flags. A client-side
  flag is visible and forgeable; it is a UI toggle, not a control.
- **Every flag gets a ticket and a removal date at creation.** `flags.yml`:
  ```yaml
  new_checkout_flow:
    type: release
    default: false
    owner: payments-team
    ticket: PAY-4821
    remove_by: 2026-03-01
  ```
- A CI check that fails when a flag is older than its `remove_by` date is the
  only thing that actually removes them.
- **Kill switches default to on.** When the switch is off, the new behaviour
  runs; when the flag service dies, `enabled()` returns the default, which for a
  kill switch is the *old, safe* path.

## Rollback decision table

| Deploy state | Rollback action | Data risk |
|---|---|---|
| Expand migration done, new code partly rolled out | Roll back code only. Leave the new column | None |
| Contract migration done (column dropped) | **No code rollback possible.** Forward-fix | Old code will fail on the dropped column |
| New code writes a format old code cannot read | Roll back code **and** the format change is not reversible; roll forward if data exists | Data written in the new format is unreadable by the old code |
| Flag off, code deployed | Flip the flag | None |
| Canary at 5%, errors climbing | Abort the analysis; it rolls back automatically with Argo Rollouts/Flagger | None (small exposure) |
| Rolling deploy stuck (new pods not ready) | The old version is still serving — this is a safe stall. Investigate; do not force-delete the new ReplicaSet | None |
| Migration partially applied (failed mid-backfill) | The backfill is idempotent; fix the cause and re-run. Do not "roll back" the additive schema | None |

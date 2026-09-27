---
name: kubernetes-manifests
description: Write Kubernetes manifests that schedule correctly and do not take a service down - requests versus limits and how they drive QoS and scheduling, liveness versus readiness versus startup probes, choosing between Deployment, StatefulSet, DaemonSet, Job and CronJob, labels and selectors, and ConfigMap versus Secret handling. Use when authoring or reviewing a manifest, when a deployment is stuck at 0/N ready, when a rolling update drops traffic, or when a pod is evicted or OOMKilled. Triggers on "Deployment yaml", "requests vs limits", "readinessProbe", "livenessProbe", "startupProbe", "QoS class", "StatefulSet", "CronJob", "ConfigMap", "Secret", "kubectl apply".
compatibility: Examples target Kubernetes 1.27+ (stable: sidecar containers, `seccompProfile`, ephemeral volumes). Probe and HPA field names are stable across 1.20+. Verify version-specific fields with `kubectl explain` and `kubectl api-resources`.
metadata:
  version: "1.0"
---

# Kubernetes Manifests

Most Kubernetes outages are not bugs in the code. They are a wrong probe, a
missing request, or a selector that did not match. Write manifests with the
scheduler, the kubelet, and the rollout controller in mind, not just the app.

## The default Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
  labels: { app: api }        # deployment-level label; not used as a selector
spec:
  replicas: 3
  revisionHistoryLimit: 5
  strategy:
    type: RollingUpdate
    rollingUpdate: { maxSurge: 1, maxUnavailable: 0 }
  selector:                    # IMMUTABLE after creation
    matchLabels: { app: api }  # must match the pod template labels
  template:
    metadata:
      labels: { app: api, tier: backend }
    spec:
      serviceAccountName: api
      automountServiceAccountToken: false
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        fsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      topologySpreadConstraints:
        - maxSkew: 1
          topologyKey: topology.kubernetes.io/zone
          whenUnsatisfiable: DoNotSchedule
          labelSelector:
            matchLabels: { app: api }
      containers:
        - name: api
          image: ghcr.io/org/api@sha256:<digest>
          imagePullPolicy: IfNotPresent
          ports: [{ containerPort: 8080, name: http }]
          envFrom:
            - configMapRef: { name: api-config }
            - secretRef: { name: api-secrets }
          resources:
            requests: { cpu: 500m, memory: 512Mi }
            limits:   { memory: 1Gi }     # no cpu limit: see below
          startupProbe:                 # gates the other two
            httpGet: { path: /healthz, port: http }
            periodSeconds: 5
            failureThreshold: 30        # 150s to start
          readinessProbe:
            httpGet: { path: /ready, port: http }
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 3
          livenessProbe:
            httpGet: { path: /healthz, port: http }
            periodSeconds: 10
            timeoutSeconds: 2
            failureThreshold: 3
          lifecycle:
            preStop:
              exec: { command: ["/app/drain.sh"] }   # see kubernetes-production-safety
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            - { name: ca, mountPath: /etc/ssl/certs, readOnly: true }
      volumes:
        - name: tmp
          emptyDir: { sizeLimit: 64Mi }
        - name: ca
          configMap: { name: ca-bundle }
---
apiVersion: v1
kind: Service
metadata:
  name: api
spec:
  type: ClusterIP
  selector: { app: api }
  ports: [{ port: 80, targetPort: http, name: http }]
```

Validate before applying:

```sh
kubectl apply --dry-run=server -f deploy.yaml   # server-side: catches admission + schema
kubeconform -strict -summary deploy.yaml         # local, offline; if you have it
kubectl diff -f deploy.yaml                      # what would change
```

`--dry-run=server` is the one that matters: it runs the API server's validation
and any admission webhooks (PodSecurity, quota, policy) without persisting
anything.

## requests vs limits, and why requests drive everything

| | request | limit |
|---|---|---|
| used for | **scheduling** (how much fits on a node) and cgroup **floor** | cgroup **ceiling** (CPU throttling, memory OOM) |
| over-request | wasted, unschedulable on small nodes | — |
| exceed | — | CPU throttled; memory OOMKilled |
| default when omitted | copied from limit, else 0 (BestEffort QoS) | none (unlimited) |

QoS classes, determined by comparing requests and limits across all containers
and init containers in the pod:

| Class | Condition | Under pressure |
|---|---|---|
| `Guaranteed` | every container has request == limit for both cpu and memory | last evicted |
| `Burstable` | some requests set, not all equal to limits | middle |
| `BestEffort` | no requests or limits at all | **first evicted** |

Consequences that bite in production:

- **A pod with no memory request is a BestEffort pod and gets evicted first**
  under node memory pressure — before your requests-bearing neighbours. The
  event is `Evicted` with reason `The node was low on resource: memory.`
- **Set memory request == limit** for anything you cannot afford to have
  throttled or evicted, and that gives you Guaranteed (if CPU matches too).
- **Set a CPU request; leave the CPU limit unset** for latency-sensitive
  services. A CPU limit throttles the container with CFS quota even when the
  node is idle (see the `container_cpu_cfs_throttled_seconds_total` counter),
  which shows up as unexplained p99 latency at steady load. If a cluster
  policy *requires* CPU limits, set `limit == request` so the container is
  Guaranteed and throttling equals its guaranteed share.
- **Memory limits are a hard kill, not a throttle.** Exceed it and the kernel
  OOM-kills the process: `OOMKilled`, exit 137, CrashLoopBackOff. See
  `kubernetes-resource-tuning`.

## Probes: the outage generator

Three probes, three different questions, and mixing them up is the single
biggest self-inflicted outage in Kubernetes.

| Probe | Question | Failure action | Wrong probe = outage |
|---|---|---|---|
| `startupProbe` | has it finished booting? | keeps the other two from running | absent → liveness can kill a slow starter |
| `readinessProbe` | can it serve traffic **right now**? | removed from Service endpoints | absent → traffic goes to a broken pod |
| `livenessProbe` | is it wedged beyond recovery? | **container killed and restarted** | too aggressive → restart loop |

Rules that are not negotiable:

1. **Liveness must not check anything a dependency can break.** If liveness
   calls your database and the database is slow, the kubelet restarts every pod
   in the fleet — a database blip becomes a total outage. Liveness checks only
   "is this process alive and not deadlocked": a local endpoint that does not
   touch the network.
2. **Readiness is where dependency checks go.** "Am I connected to the DB and
   can serve?" — a failing readiness probe removes the pod from the Service
   without restarting it. That is the graceful response.
3. **Always set a `startupProbe` for anything that is not instant**, or set
   `livenessProbe.initialDelaySeconds` generously. A JVM or a large warm-up that
   takes 90s with a 30s liveness delay is a crash loop on a healthy app.
4. **`timeoutSeconds` must exceed the probe's real latency.** Default 1s. A
   readiness endpoint that takes 1.2s under load will flap.
5. **`periodSeconds × failureThreshold` is your detection time**, and
   `initialDelaySeconds`/`startupProbe.failureThreshold × periodSeconds` is your
   start allowance. Write both down next to the numbers.
6. **`successThreshold` is 1 for liveness/startup and may be >1 for
   readiness** (the only probe that allows it).
7. **A failing `startupProbe` restarts the container** once its
   `failureThreshold` is hit — it is not a soft gate.

Probe styles, in order of preference:

```yaml
          startupProbe:
            httpGet: { path: /healthz, port: http }   # or tcpSocket, or exec
          readinessProbe:
            httpGet: { path: /ready, port: http }
            httpHeaders: [{ name: X-Probe, value: kubelet }]  # cheap route, no auth
```

- `httpGet` — cheapest and most common. Give the health endpoints a
  **dedicated, unauthenticated, cheap** path; do not put them behind a JWT
  check or a rate limiter.
- `tcpSocket` — use when the app has no HTTP server, or as a startup-only probe
  for a gRPC/DNS listener.
- `exec` — only when there is no other way. It forks a process in the container
  on every probe, so it counts against your CPU request and fails on
  distroless images. `grpc` probes exist for gRPC health checking; verify
  support with `kubectl explain pod.spec.containers.livenessProbe`.

## Workload selection

| Kind | Use for | Key gotcha |
|---|---|---|
| `Deployment` | stateless services | no stable identity; pods are interchangeable |
| `StatefulSet` | stateful: DB, broker, anything needing stable identity + ordered rollout + stable storage | ordinal identity, PVC per pod, rollout is **reverse** ordinal order and blocks on each pod |
| `DaemonSet` | one per node: log shipper, metrics agent, CNI | consumes resources on every node; plan the request |
| `Job` | one-shot work to completion | `backoffLimit`, `ttlSecondsAfterFinished` |
| `CronJob` | scheduled Jobs | **`concurrencyPolicy: Forbid`**, `startingDeadlineSeconds`, `successfulJobsHistoryLimit` |
| `Pod` | one-off debug only | never for anything real |

CronJob minimum that is actually safe:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: nightly-report }
spec:
  schedule: "0 2 * * *"
  concurrencyPolicy: Forbid        # a slow run must not stack up
  startingDeadlineSeconds: 300     # skip if we missed the window by >5min
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  suspend: false
  jobTemplate:
    spec:
      backoffLimit: 2
      ttlSecondsAfterFinished: 86400
      template:
        spec:
          restartPolicy: Never      # required for a Job
          containers:
            - name: report
              image: ghcr.io/org/report@sha256:<digest>
              resources: { requests: { cpu: 500m, memory: 512Mi }, limits: { memory: 1Gi } }
```

Default `concurrencyPolicy: Allow` lets an overrunning job overlap the next one
and can take the database down. `Allow` is only right for a job that is
genuinely parallelisable and cheap.

StatefulSet essentials:

```yaml
spec:
  serviceName: pg          # governing Service; required
  replicas: 3
  podManagementPolicy: OrderedReady
  updateStrategy:
    type: RollingUpdate
    rollingUpdate: { partition: 0 }   # partition = manual canary; set 1, verify, then 0
  volumeClaimTemplates:
    - metadata: { name: data }
      spec:
        accessModes: [ReadWriteOnce]
        storageClassName: fast-ssd
        resources: { requests: { storage: 100Gi } }
```

`serviceName` needs a **headless** Service (`clusterIP: None`) for stable
per-pod DNS (`pg-0.pg-headless.ns.svc`). Without it, peers cannot find each
other and the StatefulSet is misconfigured.

## Labels and selectors

- `spec.selector` on a Deployment/StatefulSet is **immutable**. Changing it
  requires deleting and recreating the workload. Get it right the first time;
  see `helm-chart-authoring` for the Helm equivalent.
- `spec.selector.matchLabels` must be a subset of `spec.template.metadata.labels`.
  If they do not match, the controller creates pods that no Service selects and
  you get "0/N ready" with pods that look healthy.
- `Service.spec.selector` selects **pods**, not Deployments. It is usually
  `app: <name>` matching the *template* labels, not the Deployment's own
  labels.
- Use a consistent, domain-scoped set:
  `app.kubernetes.io/name`, `/instance`, `/version`, `/component`,
  `/part-of`, plus `app: <name>` if your platform tooling expects the short
  form. Verify what your cluster's tooling (service meshes, dashboards, policy
  engines) expects before inventing a scheme.
- **Never use a mutable label in a selector.** A `version` label in a
  StatefulSet's `volumeClaimTemplates` selector is fine; in a Deployment
  selector, changing it is a delete. Select on identity; version on a
  non-selected label.
- Add a `pod-template-hash` style label (Helm does this) or use a checksum
  annotation on the pod template to roll pods when a ConfigMap changes:
  ```yaml
  template:
    metadata:
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
  ```
  Otherwise a ConfigMap change does nothing until the next unrelated rollout.

## ConfigMap vs Secret

| | ConfigMap | Secret |
|---|---|---|
| base64? | plain | base64 (an encoding, **not** encryption) |
| size limit | 1 MiB | 1 MiB |
| at-rest encryption | depends on etcd encryption | same, depends on etcd encryption |
| from a value | `data:` or `configMapGenerator` in Helm | `stringData:` or `secrets.SecretGenerator` |
| watchable | yes (can be mounted and updated live) | no, a Secret volume never updates in place |

- **Base64 is not encryption.** Anyone with `get secret` has the plaintext. The
  protection is RBAC + etcd encryption at rest + not committing it to git.
  Enable etcd encryption at rest; it is a cluster-level decision and belongs in
  your cluster hardening review, not in a manifest.
- `stringData` is plain text in the manifest and is the right choice for
  `kubectl apply`-style manifests and for anything generated at apply time
  (External Secrets, Sealed Secrets, SOPS). `data` expects base64.
- **Env vars vs mounted files.** Env vars appear in `kubectl describe pod`, in
  `/proc/<pid>/environ`, in crash dumps, and in any child process. Mount
  secrets as files with a `defaultMode` of `0400`:
  ```yaml
          volumeMounts:
            - { name: creds, mountPath: /etc/creds, readOnly: true }
      volumes:
        - name: creds
          secret:
            secretName: api-secrets
            defaultMode: 0400
            items: [{ key: db-password, path: db-password }]
  ```
- **A mounted Secret updates eventually but not instantly** (kubelet syncs
  periodically, minutes). If the app must pick up a new value, it has to
  re-read the file. A mounted ConfigMap behaves the same way; a projected
  volume with both (`projected:`) is the pattern when the app needs the pair to
  change together.
- **Do not put a whole `.env` file in a Secret and `envFrom` it** in a
  Deployment that a developer can read. Scope by service account and namespace.
- For real secret management, prefer an external operator (External Secrets,
  Secrets Store CSI Driver, SOPS+SealedSecrets) over committing a Secret
  manifest. Never commit the rendered Secret to git even in an "encrypted"
  form without checking what key decrypts it.
- **`configMapGenerator` / `secrets.SecretGenerator` in Helm** produce a
  content-hashed name, so a value change rolls the Deployment. That is the
  correct pattern; see `helm-chart-authoring`.

## Gotchas

- **A ConfigMap or Secret mounted with `subPath` never updates.** The kubelet
  resolves that file once and there is no symlink swap for the app to follow, so
  a rotated value leaves stale content in the pod until it restarts. Mount the
  whole directory, or roll the workload.
- **Exit code 137 is not proof of an OOM kill.** 137 is SIGKILL, which is exactly
  what the kubelet sends after a liveness failure too. `kubectl describe pod` is
  the only tell: `Last State: Terminated, Reason: OOMKilled` versus
  `Reason: Error` with `Exit Code: 137`.
- **`maxUnavailable: 0` and `maxSurge: 0` together are rejected.** The rollout may
  neither create nor delete a pod, so it can never progress; the API server says
  `maxSurge: Invalid value: 0: may not be 0 when maxUnavailable is 0`.
- **The `preStop` hook runs inside `terminationGracePeriodSeconds`, not before
  it.** A `preStop: sleep 30` with a 30s grace period is SIGKILLed mid-drain; set
  the grace period above the hook *plus* your longest in-flight request.
- **Do not serve health checks from the same worker pool as requests.** Under
  load the probe times out, every pod flips unready within one `periodSeconds`,
  the Service drops to zero endpoints, and a traffic spike becomes a
  self-inflicted outage while the app is actually fine.
- **An HPA on CPU utilization needs a CPU request.** Without one it reports
  `failed to get cpu utilization: missing request for cpu in container ...` and
  silently never scales.
- **`defaultMode: 0400` on a Secret volume is unreadable to a non-root
  container.** The file is owned `root:root`, and 0400 gives the group nothing;
  with `runAsNonRoot` plus `fsGroup` the app gets `permission denied` on read.
  Use `0440` and let `fsGroup` do the work, or chown in an init container.
- **`runAsNonRoot` with `capabilities: { drop: [ALL] }` cannot bind a port below
  1024**, whatever `containerPort` claims — the process dies with
  `listen tcp :80: bind: address permission denied`. Listen on 8080, put the
  Service on 80.
- **A PDB with `minAvailable` equal to `replicas` blocks node drains forever:**
  `cannot evict pod as it would violate the pod's disruption budget`. Express the
  budget as `maxUnavailable: 1` for maintenance work.
- **`kubectl apply` never removes a field you deleted from the file**; the value
  from the last-applied state persists. `kubectl apply --prune -l app=api --all`
  is required, and `--all` is what deletes rather than just reporting orphans.
- **`kubectl rollout undo` reverts the pod template, not the ConfigMap or Secret
  it references.** Rolling back a bad image leaves the bad configuration in
  place and looks like a successful rollback until the next restart.

## Checklist before you apply

- [ ] `requests` set for cpu and memory; memory `limit` set
- [ ] `startupProbe` on anything with a slow boot; liveness checks nothing
      external; readiness checks dependencies
- [ ] `selector.matchLabels` ⊆ template labels; selector unchanged from the
      existing object (it is immutable)
- [ ] image pinned by digest; `imagePullPolicy: IfNotPresent`
- [ ] ConfigMap/Secret references exist and the app reads them as files, not
      env vars, for anything sensitive
- [ ] `securityContext` at pod and container level: non-root, no privilege
      escalation, capabilities dropped
- [ ] `resources` and the app's own memory ceiling agree (see
      `kubernetes-resource-tuning`)
- [ ] `kubectl apply --dry-run=server -f` passes
- [ ] `kubectl diff -f` reviewed by a human before apply

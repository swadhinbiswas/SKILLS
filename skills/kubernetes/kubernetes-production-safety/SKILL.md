---
name: kubernetes-production-safety
description: Operate Kubernetes without causing outages - PodDisruptionBudgets, graceful shutdown with terminationGracePeriodSeconds and preStop hooks, rolling update strategy tuning, resource quotas, and the delete commands that are never safe to run casually. Includes what requires human approval and a production change checklist. Use when planning a deploy, a node drain, a scale-down, or a cluster upgrade, when a release is dropping connections, or before any destructive kubectl command. Triggers on "PodDisruptionBudget", "preStop", "terminationGracePeriodSeconds", "maxUnavailable", "graceful shutdown", "kubectl drain", "kubectl delete", "resource quota", "production change", "node upgrade", "cordon".
compatibility: Examples target Kubernetes 1.27+ with `policy/v1` PodDisruptionBudget, `scheduling.k8s.io/v1` PriorityClass, and `networking.k8s.io/v1` NetworkPolicy. Drain behaviour and `terminationGracePeriodSeconds` defaults are stable; verify with `kubectl explain`.
metadata:
  version: "1.0"
---

# Kubernetes Production Safety

Kubernetes will happily do the wrong thing fast. This skill is about the
operations where the wrong thing is an outage, a data loss, or an
unrecoverable deletion.

**Default posture: draft and surface, do not execute.** Present the command, the
blast radius, and the rollback, and let a human run it. Never run a destructive
production command as a side effect of "fixing" something.

## What requires explicit human approval

Do not run these on a production cluster without a named human saying yes, in
this session, for this specific change:

| Operation | Why |
|---|---|
| `kubectl delete namespace` | deletes every object in it; RBAC objects and CRDs included |
| `kubectl delete pod` on a singleton/StatefulSet | the only replica; causes a gap |
| `kubectl delete` with `--all` or `-l <broad selector>` | one wrong label selector deletes a fleet |
| `kubectl drain` (any node) | evicts pods; respects PDBs but a wrong PDB means downtime |
| `kubectl delete pvc` | the data, unless the PV has a reclaim policy of `Retain` and a backup |
| `kubectl apply -f` of a manifest changing `selector`, `clusterIP`, or shrinking a PVC | immutable-field or one-way changes |
| `helm upgrade` with `--force` | deletes and recreates resources |
| `kubectl edit` on a live Deployment in production | an unsaved, unreviewed change with no diff |
| `kubectl scale` | a scale-to-zero is data-adjacent for stateful workloads |
| `kubectl cordon` on more than one node at a time | capacity loss compounds |
| Anything with `--force --grace-period=0` | skips the shutdown contract entirely |

Before any of these, state: **what it does, what it breaks, how long the
break lasts, and how to reverse it.** If the rollback is "restore from backup",
say so and get that backup verified first.

## PodDisruptionBudgets

A PDB limits how many of a workload's pods a *voluntary* disruption (drain,
node upgrade, cluster autoscaler scale-down) may take at once. It does **not**
protect against involuntary events: a node failure, an OOMKill, a crash loop,
or a rolling update (the Deployment controller ignores PDBs).

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api
spec:
  minAvailable: 2          # or maxUnavailable: 1
  selector:
    matchLabels: { app: api }
```

- **Use `minAvailable` (or `maxUnavailable`), never both.** The API server
  rejects it otherwise.
- **`minAvailable: 2` with `replicas: 2` blocks all drains forever.** A drain
  hangs with `error when evicting pods... cannot evict pod as it would violate
  the pod's disruption budget`. That is the PDB doing its job, and the fix is
  a replica count that has headroom, not removing the PDB.
- **A percentage is relative to the replica count**, and rounds **up**:
  `minAvailable: 50%` of 3 pods is 2. With 1 replica, `50%` is 1, so a
  single-replica Deployment is undrainable — which is correct, and a good
  signal that it should not be a single replica.
- **The selector must match the pod template labels.** A PDB that matches
  nothing protects nothing and reports no error.
- **A PDB with no matching healthy pods blocks nothing useful** and can wedge
  a node upgrade. Check with `kubectl get pdb` and read
  `DISRUPTIONS ALLOWED`.
- **Scale down and the PDB still applies.** Reducing `replicas` below
  `minAvailable` leaves the workload in a state where drains are blocked. Check
  the PDB after every scale change.
- **Every stateful or singleton-critical workload needs a PDB, and every
  PDB needs a replica count above `minAvailable`.** Both, or one of them is
  theatre.

```sh
kubectl get pdb
kubectl get pdb api -o jsonpath='{.status.disruptionsAllowed}{"\n"}'
kubectl drain node-3 --ignore-daemonsets --delete-emptydir-data --timeout=10m
```

`--ignore-daemonsets` and `--delete-emptydir-data` are needed for almost every
drain (DaemonSet pods and emptyDir pods are not managed by a controller and
would otherwise block the drain). `--force` is only for pods with no
controller — read that list before using it.

## Graceful shutdown

Two fields, and they only work together.

```yaml
spec:
  terminationGracePeriodSeconds: 45     # pod-level; default 30
  containers:
    - name: api
      lifecycle:
        preStop:
          exec:
            command: ["/app/drain.sh"]
      ports: [{ containerPort: 8080, name: http }]
```

**The sequence on deletion, exactly:**

1. The API server sets `deletionTimestamp`. The endpoint is removed from
   EndpointSlices asynchronously — **this takes a moment and is not
   instantaneous.**
2. The kubelet runs the `preStop` hook, if any, and starts the grace period
   timer. **`preStop` runs before SIGTERM.**
3. The grace period (`terminationGracePeriodSeconds`, default 30s) is the total
   budget. If `preStop` sleeps, that time comes out of the same budget.
4. After `preStop` returns, **SIGTERM** is sent to PID 1.
5. When the budget expires, **SIGKILL**.

Consequences:

- **Your app must handle SIGTERM.** If it does not, it dies at step 5 and every
  in-flight request is cut. Handle SIGTERM, stop accepting new connections,
  finish in-flight work, then exit. This is the single most important
  application-side change for zero-downtime deploys.
- **A `preStop` sleep buys propagation time.** This is the standard fix for
  "the first requests after a deploy hit a pod that is shutting down":
  ```yaml
          preStop:
            exec: { command: ["sleep", "10"] }
  ```
  Ten seconds of sleep, during which the pod is already out of the endpoints,
  keeps the load balancer from sending anything new. It costs 10 seconds of
  deploy time and removes the class of bug.
- **A `preStop` that calls your own `/drain` endpoint is better than a sleep**
  when the app can switch itself to unready immediately — but the endpoint
  removal and the probe result are asynchronous, so keep a sleep of at least a
  few seconds even then.
- **`terminationGracePeriodSeconds` must exceed** your app's longest in-flight
  request plus the `preStop` time. A 5-second-terminating pod that takes 30
  seconds to drain connections is a pod that gets SIGKILLed at 5 seconds.
- **`preStop` does not run if the kubelet cannot start the hook** (for example
  a `httpGet` hook against an app that is already gone). It is a best-effort
  hook, not a guarantee.
- **A pod deleted with `--force --grace-period=0`** skips steps 2-4 entirely.
  Every in-flight request dies. That is what makes it a last resort.
- **A `preStop` `exec` requires a shell and binaries in the image.** In a
  distroless image, `preStop: exec` fails and the pod terminates with no grace
  at all. Use a `preStop: httpGet` against your own drain endpoint, or a
  `sleep` binary you copied in, or — best — handle it in the app and use a
  short `preStop: sleep`.

## Rolling updates

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1          # extra pods above replicas during the rollout
      maxUnavailable: 0    # never drop below replicas
  minReadySeconds: 10     # a pod must be Ready 10s before it counts as available
  progressDeadlineSeconds: 600
```

- **`maxUnavailable: 0` (with `maxSurge > 0`) is the zero-downtime setting.**
  A pod is only removed after a replacement is Ready. It requires cluster
  capacity for the surge.
- **`maxSurge: 0` with `maxUnavailable: 1` is a rolling *replacement***: it
  needs no extra capacity but drops a pod's capacity during the rollout. Fine
  for a non-critical internal service, wrong for a latency SLO.
- **Readiness gates the rollout, not liveness.** A pod that is running but not
  Ready is neither available (so it does not count for `maxUnavailable`) nor
  removed (so it is not counted down) — the rollout stalls. A readiness probe
  that passes too early (before the app can actually serve) sends traffic to a
  pod that will fail the first few requests.
- **`minReadySeconds`** is what stops a pod that flaps Ready from being counted
  as available. Set it to at least 2-3 probe periods.
- **`progressDeadlineSeconds`** (default 600) is how long before the controller
  declares the rollout failed. A stuck rollout leaves both old and new pods
  running and consuming resources; `kubectl rollout status` is how you notice.
- **A resource quota can deadlock a rollout.** If the namespace is at
  `requests.cpu` quota and `maxSurge: 1` needs one more pod's worth of
  requests, the new pod cannot be created and the rollout hangs. Size the
  quota with surge headroom, or the rollout will sit at `N-1/N` forever.
- **`kubectl rollout undo deployment/api`** reverts to the previous
  ReplicaSet. `kubectl rollout history` shows the revisions. `kubectl rollout
  pause` freezes a rollout that is going wrong, and `resume` continues it.

```sh
kubectl rollout status deploy/api --timeout=5m
kubectl rollout history deploy/api
kubectl rollout undo deploy/api
kubectl rollout pause deploy/api
kubectl get rs -l app=api     # which ReplicaSet is where
```

## Resource quotas and limits

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: production
spec:
  hard:
    requests.cpu: "40"
    requests.memory: 80Gi
    limits.memory: 160Gi
    persistentvolumeclaims: "20"
    pods: "500"
    count/deployments.apps: "50"
```

- **A namespace with a quota has a pod-count budget that a rollout consumes.**
  A `maxSurge` of 25% on a 200-replica Deployment needs 50 spare quota
  headroom, or it will not roll.
- **A quota with `requests.cpu` and no `limits` on pods** is fine, but a
  namespace where pods *may* set limits and *may* not creates confusing
  `exceeded quota` failures. Pair a ResourceQuota with a LimitRange so pods
  get defaults and caps automatically.
- **`count/*` quotas are the cheap defence against a runaway deployment
  script.** A `count/deployments.apps: 50` limit means a bad `for` loop fails
  fast instead of creating 5000 objects.
- **Quota is namespace-wide and cumulative.** A `LimitRange` default plus the
  quota means every pod in the namespace consumes quota the moment it is
  created — including a surge pod.
- **Check the state before you change anything**:
  ```sh
  kubectl describe quota
  kubectl get quota production -o yaml
  ```

## Never run `kubectl delete` casually

The specific mistakes that lose data:

| Command | What actually happens | Safer form |
|---|---|---|
| `kubectl delete pods --all` | deletes every pod in the namespace, all at once, ignoring PDBs | `kubectl delete pod -l app=api --field-selector ...` one namespace, one label, one owner |
| `kubectl delete deploy api` | deletes the Deployment, which **cascades and garbage-collects its ReplicaSet and pods**; a StatefulSet's PVCs survive, a Deployment's do not exist | `kubectl scale deploy/api --replicas=0` if you just want it down |
| `kubectl delete ns prod` | deletes the Namespace object; the namespace controller then deletes **everything** in it, including resources that no controller owns | there is no undo. Export everything first |
| `kubectl delete pvc data-pg-0` | deletes the claim; the PV goes with it unless the reclaim policy is `Retain` | set the reclaim policy to `Retain` *before* you ever need it |
| `kubectl delete cm config` | pods that mount it keep running; new pods fail with `CreateContainerConfigError` | `kubectl patch` or apply instead |
| `kubectl delete secret db-creds` | every pod that mounts it starts failing | rotate and update, do not delete first |
| `kubectl delete --selector app=api` | a label you did not mean to select, in every namespace unless you passed `-n` | always add `-n <namespace>` and confirm the selector first |
| `kubectl delete pod <p> --force --grace-period=0` | no SIGTERM, no preStop, in-flight requests die, the process keeps running on the node until the kubelet notices | plain `kubectl delete pod` |

Before any delete:

```sh
# 1. What exactly does this selector match?
kubectl get <kind> -n <ns> -l <selector> -o name
# 2. What is the owner? (a controller will recreate it)
kubectl get <kind> -n <ns> -l <selector> -o custom-columns=NAME:.metadata.name,OWNER:.metadata.ownerReferences[0].name
# 3. Is there a controller? (then delete is temporary; scale is what you want)
kubectl get deploy,sts -n <ns>
```

Then, for anything data-bearing: confirm the backup exists and has been
restored at least once, or confirm the reclaim policy is `Retain` and a
snapshot exists. Say which in your report.

## Node maintenance

```sh
kubectl cordon node-3                                  # no new pods
kubectl drain node-3 --ignore-daemonsets --delete-emptydir-data --timeout=10m
# upgrade the OS / kubelet
kubectl uncordon node-3
```

- **`cordon` first, then `drain`, always.** Cordoning alone is safe and
  reversible instantly (`uncordon`).
- **Drain respects PDBs.** A pod that would violate a PDB is not evicted and the
  drain stalls with an error naming the PDB. That is correct; the answer is
  capacity or a replica bump, not removing the PDB.
- **Drain one node at a time on a small cluster.** Two nodes cordoned plus a
  deployment's `maxSurge` can exceed the cluster's capacity, and the new pods
  stay `Pending` — which is a worse outage than the maintenance.
- **`--delete-emptydir-data` destroys data in emptyDir volumes.** It is
  required for most drains (an unmanaged pod with an emptyDir will block one).
  Confirm nothing in the workload stores state in an emptyDir.
- **`--force` deletes pods with no controller.** Read the list it prints.
- **A `PodDisruptionBudget` with `minAvailable` equal to the replica count
  makes the cluster permanently un-upgradeable.** Check PDBs before a node
  pool upgrade, not during.
- **A `podAntiAffinity` spread constraint plus a drain plus low capacity** is
  how a routine upgrade becomes an outage: the rescheduled pod cannot be
  placed because the anti-affinity forbids the only two free nodes. Leave
  headroom.

## Other production-safety points

- **`kubectl edit` on a live object** is an unreviewed change with no diff and
  no record. In production, use `kubectl diff` + `kubectl apply` from a file.
  If you must edit, `kubectl edit --save-config` and diff the result.
- **Service account tokens**: set
  `automountServiceAccountToken: false` on any pod that does not call the API
  server. A default token in every pod is a credential in every container.
- **RBAC least privilege**: a `ServiceAccount` per workload, no
  `cluster-admin`, no `system:masters` binding, and
  `automountServiceAccountToken: false` on the ServiceAccount where possible.
- **Namespace deletion is the one operation with no undo.** `kubectl get -o
  yaml` every object in the namespace (and the CRs of every CRD in it) and
  store it, before anyone proposes deleting it.
- **`kubectl apply --prune -l <selector>`** is `kubectl delete` with extra
  steps and an extra label. It has removed live resources in the wild. Treat it
  as a destructive command requiring the same review.
- **Admission and policy**: PodSecurity Admission (`pod-security.kubernetes.io/enforce`
  labels on the namespace), Kyverno/Gatekeeper for extra rules. A cluster
  without admission control will happily accept a `privileged` pod.

## Production change checklist

Before any production change, all of these are answered in writing:

- [ ] What exactly will change? A rendered diff, not a description.
- [ ] What is the blast radius? How many pods, services, or users.
- [ ] What is the rollback, and has it been rehearsed?
- [ ] Is there a PDB on every affected workload, with
      `DISRUPTIONS ALLOWED >= 1`?
- [ ] Is there quota headroom for `maxSurge`?
- [ ] Does the app handle SIGTERM, and is
      `terminationGracePeriodSeconds` long enough?
- [ ] Is there a `preStop` hook, and does the image have a shell for it?
- [ ] Are probes correct — liveness local-only, readiness checks dependencies,
      `startupProbe` for slow boots?
- [ ] Is the image pinned by digest, and scanned?
- [ ] Is this a one-at-a-time change (one node, one pod, one canary)?
- [ ] Who is watching, and what is the abort signal?
- [ ] For a deletion: what is the owner, and is there a controller that will
      recreate it?
- [ ] For anything data-bearing: is there a backup, and has it been restored
      once?

Do not proceed if any answer is "I think so".

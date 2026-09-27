---
name: kubernetes-resource-tuning
description: Size Kubernetes CPU and memory so workloads do not get OOMKilled or throttled - sizing JVM, Node and Python heaps against the cgroup limit, fixing the OOMKill restart cycle, configuring and stabilising HPAs, using VPA, and understanding QoS-based eviction priority. Use when a pod is OOMKilled, when latency spikes under load despite spare CPU, when an HPA is flapping, or when deciding what to put in requests and limits. Triggers on "OOMKilled", "exit 137", "Java heap in container", "MaxRAMPercentage", "memory limit", "CPU throttling", "HPA flapping", "requests too low", "evicted", "quota".
compatibility: Examples target Kubernetes 1.27+ and the `autoscaling/v2` HorizontalPodAutoscaler (v1 is deprecated). Runtime flags for JVM, Node and Python vary by version; verify with `--help` or the runtime's docs for your exact version.
metadata:
  version: "1.0"
---

# Kubernetes Resource Tuning

The container's memory limit is a **hard ceiling enforced by the kernel**, and
most runtimes size themselves from the *host's* memory, not the limit. That
single fact causes most OOMKills in Kubernetes. Get it right and the rest is
measurement.

Read `references/memory-sizing.md` when computing the memory limit for a JVM,
Node, or Python process, or when interpreting a cgroup memory event.

## The trap: the runtime does not know about the limit

| Runtime | Default behaviour | Result |
|---|---|---|
| JVM (Java 8u191+, all 10+) | sizes `-Xmx` from **max heap = 1/4 of the machine's RAM** unless container-aware ergonomics are on | a 64 GB node gives a 16 GB heap; a 512 Mi limit is ignored and the process is OOMKilled at 512 Mi |
| Node.js | default old-space is ~1.5-4 GB depending on version and available memory, and V8 is container-aware from v12+ but still not limit-aware | OOM at the cgroup limit, not at the V8 limit; the error is `JavaScript heap out of memory` only after V8's own limit |
| Python | no per-process cap by default; a runaway allocation is bounded only by the cgroup | OOMKill with no Python-level error |
| Go | `GOGC` and `GOMEMLIMIT`; without `GOMEMLIMIT` the GC is workload-driven | OOMKill when the live set exceeds the limit |

The fixes, explicitly:

```yaml
          env:
            - name: JAVA_TOOL_OPTIONS       # or: JVM_OPTS / JAVA_OPTS, app-specific
              value: "-XX:MaxRAMPercentage=70 -XX:+ExitOnOutOfMemoryError"
          resources:
            requests: { cpu: 500m, memory: 1Gi }
            limits:   { memory: 1536Mi }     # 70% of 1536Mi = ~1075 Mi heap
```

```yaml
          env:
            - name: NODE_OPTIONS
              value: "--max-old-space-size=768"   # must be < limit
          resources: { requests: { memory: 768Mi }, limits: { memory: 1Gi } }
```

```yaml
          env:
            - name: GOMEMLIMIT
              value: "900MiB"                 # Go 1.19+; GC works harder near the limit
          resources: { requests: { memory: 512Mi }, limits: { memory: 1Gi } }
```

`MaxRAMPercentage` is the *sane* JVM knob: it derives the heap from the cgroup
limit, not a hard-coded number, so the same image works at any limit.
`-XX:+ExitOnOutOfMemoryError` makes a heap exhaustion a clean exit with a
stack trace instead of an unkillable JVM that the kernel has to SIGKILL. Use
`JAVA_TOOL_OPTIONS` (or whatever your base image's entrypoint reads) so the
setting applies no matter how the JVM is launched.

Python: set an explicit guard where the process is the only consumer, and
watch the process's RSS with a metrics exporter rather than hoping.

## The OOMKill cycle

Symptom: `CrashLoopBackOff` with exit 137, `lastState.terminated.reason:
OOMKilled`, and often a very short uptime (seconds to a couple of minutes).

```sh
kubectl get pod <p> -o jsonpath='{.spec.containers[*].resources}{"\n"}'
kubectl get pod <p> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}{"\n"}' | jq
kubectl describe pod <p> | sed -n '/Last State/,/^  Ready/p'
kubectl top pod <p> --containers
```

Distinguish three causes:

1. **The app exceeded its limit** (`OOMKilled`, time-to-crash proportional to
   workload). Fix: raise the limit *and* find what grew. Check for a leak
   (memory climbing across restarts), a batch that loads everything, or a
   runtime sizing itself above the limit.
2. **The node ran out of memory** and the kernel picked your process. The pod
   shows `OOMKilled` too, but `kubectl describe node` shows
   `MemoryPressure: True` and the `Evicted`/`MemoryPressure` events name other
   pods. Fix: fix the node's capacity or the other workloads, not this pod's
   limit.
3. **The cgroup limit is below the app's floor** — the app needs more just to
   boot. A JVM with a 256 Mi limit and default ergonomics, or a Python process
   importing a 400 MB ML model, will be killed before it can serve a request.
   The fix is a bigger limit, and a runtime flag that tells the runtime about
   it.

Do not "fix" an OOMKill by only adding memory. A genuine leak will just consume
the larger limit too, a little later.

The other memory failure mode: **eviction**, not OOMKill. `Evicted` with
`The node was low on resource: memory.` means the kubelet evicted the pod
because the *node* was short on an evictable resource. The fix is node
capacity or QoS — see below.

## CPU: request vs limit

- **The request is a guarantee** and the scheduler's contract: the kubelet
  shares the node among containers in proportion to their requests. Set it to
  the p95 usage at steady state.
- **The limit is a throttle, not a reservation.** With CFS quota, a container
  that uses 200% CPU against a 1000m limit is throttled for the remainder of
  each 100ms period, even on a completely idle node. This is invisible in
  `kubectl top` (which shows usage, not throttling) and shows up as unexplained
  p99 latency.
- **Measure throttling directly**:
  ```sh
  kubectl exec <p> -- cat /sys/fs/cgroup/cpu.stat   # throttled_usec, nr_throttled
  ```
  or export `container_cpu_cfs_throttled_seconds_total` from cAdvisor and
  alert on a non-zero rate.
- **Default: set a CPU request, leave the limit unset** for latency-sensitive
  services. Under contention the request gives the container a fair share, and
  there is no artificial ceiling.
- **If a cluster policy requires CPU limits**, set `cpu.limit == cpu.request`
  for latency-sensitive services. The container then has exactly its guaranteed
  share, and equals its limit only under contention.
- **`Guaranteed` QoS** requires request == limit on **both** cpu and memory for
  every container. Getting memory `limit == request` and CPU limit == request
  is the way to get a pod into Guaranteed, which is the last thing evicted.
- CPU is compressible: exceeding the limit slows the pod. Memory is not: it
  kills the process. This asymmetry is why a memory limit is set conservatively
  and a CPU limit is often not.

## Requests: how to pick

- **Measure, do not guess.** `kubectl top pod <p> --containers` over a
  representative week, or read `container_memory_working_set_bytes` and
  `container_cpu_usage_seconds_total` from your metrics. Take p95, not the
  max.
- **CPU request** = p95 steady-state CPU. Under-provisioning gets you
  throttling under contention; over-provisioning makes the cluster
  unschedulable (and `FailedScheduling: Insufficient cpu`).
- **Memory request** = working set at peak, plus headroom for the runtime's
  own overhead (JVM metaspace, thread stacks, Python interpreter, native
  libraries). Requests are what the scheduler reserves; under-reserving memory
  is how you get a node that cannot schedule and then evicts.
- **Memory request == limit** for anything with a predictable footprint
  (a stateless API). This is the single most effective thing you can do for
  eviction priority and it makes the pod Guaranteed (with CPU matching).
- **Do not set requests to the same value across every service** because you
  do not know better. A wrong request is a scheduling problem that surfaces as
  `Pending` pods under fragmentation, which is much harder to debug than
  having measured once.

## HorizontalPodAutoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: api }
  minReplicas: 3
  maxReplicas: 30
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: 70 }
    - type: Resource
      resource:
        name: memory
        target: { type: Utilization, averageUtilization: 80 }
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30      # react fast
      policies: [{ type: Percent, value: 100, periodSeconds: 30 }]
      selectPolicy: Max
    scaleDown:
      stabilizationWindowSeconds: 300     # shrink slowly
      policies: [{ type: Pods, value: 2, periodSeconds: 60 }]
      selectPolicy: Min
```

- **The HPA needs a CPU *request* to compute utilization.** With no CPU
  request, a CPU-utilization HPA cannot scale and reports
  `<unknown>/50%` in `kubectl describe hpa`. This is the number one HPA
  mistake.
- **The controller computes `usage / request`.** A pod requesting 500m that
  uses 350m is "70% utilized" and hits a 70% target. If the request is
  unrealistic, the HPA scales for the wrong workload.
- **`memory` as a metric is noisy** (GC pauses, a cache filling, a spike in
  one pod). It is a poor primary signal; use it as a second metric or not at
  all.
- **Custom and external metrics** (`type: Pods`, `type: Object`, `type:
  External`) need a metrics adapter (Prometheus Adapter, KEDA, Datadog). A
  custom metric with no adapter gives `FailedGetResourceMetric` and no
  scaling.
- **A `behavior` section is what stops flapping.** `scaleDown`'s
  `stabilizationWindowSeconds` is the important one: it makes the HPA use the
  *highest* recommendation over the window, so a brief dip does not scale you
  to zero and back.
- **Scaling to zero requires an external trigger** (KEDA, a queue-depth
  metric); the built-in HPA cannot do it.

### Flapping

```
ScalingActive False  <unknown>/70%  <unknown>  70%  True  False
FailedGetResourceMetric  failed to get cpu utilization: unable to get metrics
```

| Symptom | Cause | Fix |
|---|---|---|
| `<unknown>` | no metrics-server, no CPU request, or the adapter is down | check `kubectl -n kube-system get pods -l k8s-app=metrics-server`; add a CPU request |
| `FailedGetResourceMetric` | adapter missing for a custom metric | install the adapter or switch to a resource metric |
| `ScalingActive False` with `<unknown>` | the same, plus a target that cannot be computed | as above |
| Up and down every few minutes | no `behavior`, or a metric that is noisy | add `scaleDown.stabilizationWindowSeconds: 300`; use a queue/latency metric, not CPU |
| Scale-up never reaches `maxReplicas` | the `behavior` policies cap it, or the target is a `Low` type with too many pods | check `behavior.scaleUp.policies` |
| HPA and a fixed `replicas` fight each other | `spec.replicas` is reset by the HPA; a GitOps tool re-applies `replicas` on every sync | remove `replicas` from the manifest, or set `minReplicas` and let the HPA own it |

**The GitOps fight** is the most common operational HPA bug: Flux/Argo CD
re-applies `spec.replicas: 3` every reconciliation, and the HPA scales to 20,
and the next sync puts it back to 3. Symptom: pods scale up and immediately
return to the declared number. Fix: omit `replicas` from the desired state
(Helm: only set it when `autoscaling.enabled` is false), or use an
`ignoreDifferences` rule for `spec.replicas` on the Deployment.

## VPA (VerticalPodAutoscaler)

VPA adjusts **requests** (and optionally limits) per pod from observed usage.
It does not change the number of pods.

- `updateMode: Off` (default): recommendations only, nothing changes. **Start
  here** — it is how you find out what your requests should be.
- `updateMode: Auto`: VPA evicts pods to apply new requests. Each eviction is
  a pod restart; with a low `minReplicas` that is a self-inflicted outage.
- `updateMode: Recreate`: only on a scale-down to zero, then all at once.
- **VPA in `Auto` mode conflicts with the HPA** if both target the same
  Deployment: HPA changes replica count, VPA changes requests, and the
  utilization target shifts under the HPA as requests move. Pick one per
  workload, or accept that VPA must be conservative.

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: api
spec:
  targetRef: { apiVersion: apps/v1, kind: Deployment, name: api }
  updatePolicy:
    updateMode: "Off"          # recommendations only
  resourcePolicy:
    containerPolicies:
      - containerName: api
        minAllowed: { cpu: 100m, memory: 256Mi }
        maxAllowed: { cpu: "2",  memory: 4Gi }
        controlledResources: [cpu, memory]
```

Start with `updateMode: Off` for every workload, read the recommendations for a
week, put the numbers in the manifest, and only then consider `Auto`. Note that
VPA's `controlledResources: [memory]` with a memory limit will also resize the
limit — which for a JVM means the heap moves with it, and a rolling restart is
required.

## QoS and eviction priority

| QoS | Eviction order | When |
|---|---|---|
| `Guaranteed` | last | request == limit on cpu and memory |
| `Burstable` | middle | some requests set |
| `BestEffort` | **first** | no requests, no limits |

Under node pressure the kubelet evicts in QoS order. So:

- **A `BestEffort` pod is the first thing to die on a busy node**, which is why
  "the pod with no resources keeps disappearing" happens. Every pod you care
  about gets a request.
- **A `Guaranteed` pod is evicted last**, which is the cheapest way to protect
  a critical service. For a stateful database, `request == limit` on cpu and
  memory is not optional.
- **`priorityClassName` orders pods within the same QoS class** for eviction and
  scheduling. `system-cluster-critical` is reserved for control-plane
  components; define your own classes and set `preemptionPolicy: Never` for
  the ones that must not be preempted.
- **ResourceQuota limits a namespace's total requests and limits.** A namespace
  at quota produces `FailedCreate`/`exceeded quota` on new pods, and — with
  `hard` limits on requests — can make a rolling update unable to create the
  surge pod. `kubectl describe quota` names the specific resource. Size the
  quota with headroom for `maxSurge` × pod size, or a rollout will deadlock
  waiting for quota.
- **LimitRange** supplies defaults for pods that do not set resources (so they
  are not `BestEffort`) and caps what they may set. It is the cheap way to stop
  a forgotten `resources:` block from producing `BestEffort` pods.

## Gotchas

- **A memory limit is a kill, not a throttle.** No amount of CPU left on the
  node helps; the process dies at the limit.
- **`kubectl top` shows usage, not throttling or OOM risk.** A pod at 95% of
  its limit looks fine there.
- **The JVM's container-aware ergonomics read the cgroup limit, not the
  request.** `MaxRAMPercentage=70` with a 1 Gi limit gives a ~700 Mi heap; with
  a 4 Gi limit, ~2.8 Gi. That is what you want — but it means the *same* image
  behaves differently at different limits, so pin the limit.
- **Metaspace, thread stacks, and direct buffers are outside the heap.** A JVM
  with `-Xmx512m` in a 768 Mi container will still OOMKill. `MaxRAMPercentage`
  accounts for this; a hard `-Xmx` you picked by hand usually does not.
- **`-XX:+ExitOnOutOfMemoryError`** turns a heap exhaustion into a clean exit
  with a message. Without it, the JVM may hang in a failing GC and the kernel
  kills it with no diagnostic.
- **Memory limits and `memory` HPA metrics interact badly**: a container at
  99% of its limit for a legitimate reason (a cache that is *supposed* to fill)
  will be OOMKilled before the HPA reacts.
- **HPA on `cpu` with no request gives `<unknown>`**, always. Add the request.
- **Scaling down too fast is an outage**: each scale-down is a pod termination,
  and with `maxUnavailable` unset it can drop below the desired count while
  requests are in flight. Use `scaleDown.stabilizationWindowSeconds` and a low
  `policies` value.
- **VPA `Auto` and HPA on the same target fight.** Requests changing under the
  HPA change the utilization denominator.
- **`kubectl top` needs metrics-server.** Without it, `kubectl top` errors and
  every HPA reports `<unknown>`; the HPA has no fallback.
- **Eviction events expire quickly.** A pod that was evicted an hour ago shows
  `Evicted` in `status.reason` but the `describe` event is gone. Check
  `kubectl get pod -o jsonpath='{.status.reason}{" "}{.status.message}'`.
- **Requests drive `Pending`; limits drive `OOMKilled`.** A pod that is
  `Pending` with `Insufficient memory` has a request problem, not a limit
  problem, and raising the limit will not help.

## Measuring loop

```sh
# current usage
kubectl top pod <p> --containers
# throttling (cpu)
kubectl exec <p> -- cat /sys/fs/cgroup/cpu.stat
# memory breakdown (cgroup v2)
kubectl exec <p> -- cat /sys/fs/cgroup/memory.stat
kubectl exec <p> -- cat /sys/fs/cgroup/memory.max
# restart history and OOM reason
kubectl get pod <p> -o jsonpath='{.status.containerStatuses[*].restartCount}{" "}{.status.containerStatuses[*].lastState.terminated.reason}{"\n"}'
# HPA state
kubectl describe hpa <name> | sed -n '/Events/,$p'
```

Take the measurements **before** changing numbers, and again after. "Raised the
limit to 2 Gi" without a before/after is not a tuning change, it is a guess.

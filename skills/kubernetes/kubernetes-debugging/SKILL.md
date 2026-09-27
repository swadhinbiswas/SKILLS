---
name: kubernetes-debugging
description: Diagnose failing Kubernetes workloads from kubectl triage commands - CrashLoopBackOff, ImagePullBackOff, ErrImagePull, OOMKilled, Pending pods, Init container failures, and a pod that starts then dies. Covers reading kubectl describe events, getting into a pod that will not start, ephemeral debug containers, and copying logs out of a dead pod. Use when a pod will not become ready, when a deployment is stuck, when someone pastes describe output, or when a container keeps restarting. Triggers on "CrashLoopBackOff", "ImagePullBackOff", "OOMKilled", "pod pending", "Init:Error", "kubectl describe", "container not running", "readiness probe failing", "kubectl debug".
compatibility: Examples target kubectl 1.27+ and Kubernetes 1.27+. `kubectl debug` with ephemeral containers needs 1.25+ and `kubectl alpha` on older versions. Verify with `kubectl explain` and `kubectl version`.
metadata:
  version: "1.0"
---

# Kubernetes Debugging

Work from the outside in, and read the **events** — they carry the answer in
almost every case, and they are the first thing people skip.

## Triage, in order

- [ ] 1. `kubectl get pods -o wide` — status, restarts, node
- [ ] 2. `kubectl describe pod <p>` — Events section, last state, exit code
- [ ] 3. `kubectl logs <p> [-c container] [--previous]` — the crash output
- [ ] 4. `kubectl logs <p> --previous` — **the logs from the crash**, not the
      current attempt
- [ ] 5. Get a shell: ephemeral debug container, or a copy of the image with a
      shell, or `kubectl cp`
- [ ] 6. Escalate to the node only if all of the above are clean

## The one command that starts everything

```sh
kubectl get pods -o wide
```

Read `STATUS` and `RESTARTS` together:

| STATUS | Meaning | Next step |
|---|---|---|
| `Running` | up; maybe not ready | check `READY` column and readiness probe |
| `Pending` | never scheduled or waiting | see Pending section |
| `ContainerCreating` | image pull / volume mount / sandbox | events say which |
| `Init:0/2` | init containers running | `kubectl logs <p> -c <init>` |
| `Init:CrashLoopBackOff` | init container crashing | `kubectl logs <p> -c <init> --previous` |
| `ImagePullBackOff` | pull failing | see below |
| `ErrImagePull` | first failure of a pull | the event has the reason |
| `CrashLoopBackOff` | container keeps exiting | `--previous` logs + exit code |
| `CreateContainerConfigError` | config/secret referenced but missing | events name the object |
| `Error` | other | `kubectl describe` |

`RESTARTS > 0` with `Running` means it started, failed, and was restarted —
`--previous` holds the output from before that restart.

## `kubectl describe` and events

```sh
kubectl describe pod api-7d9f8b6c4-x2k9p
kubectl describe pod api-7d9f8b6c4-x2k9p | sed -n '/^Events:/,$p'
kubectl get events --sort-by=.lastTimestamp | tail -30
kubectl get events -n <ns> --field-selector involvedObject.name=<pod> --sort-by=.lastTimestamp
```

Describe gives you, in order: metadata, spec, status (with `Last State` showing
`Terminated` + exit code + reason + message), then the container logs' tail,
then Events. **Events are the diagnostic.** Read them in `AGE` order, oldest
first — the sequence matters (`Scheduled` → `Pulling` → `Created` → `Started` →
`Killing`/`Back-off`).

Useful status-only views:

```sh
kubectl get pod <p> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}' | jq
kubectl get pod <p> -o jsonpath='{r:.status.containerStatuses[*].restartCount,e:.status.containerStatuses[*].lastState.terminated.exitCode,rs:.status.containerStatuses[*].lastState.terminated.reason,m:.status.containerStatuses[*].lastState.terminated.message}{"\n"}'
kubectl get pod <p> -o jsonpath='{.status.conditions}' | jq
kubectl get pod <p> -o json | jq '.status.containerStatuses[].state.waiting.reason'
```

## The five states, and what to do

### CrashLoopBackOff

`Back-off restarting failed container`. The container starts and exits
repeatedly with an increasing delay (10s, 20s, 40s … up to 5 min).

```sh
kubectl logs <p> -c <container> --previous --tail=200
kubectl describe pod <p> | sed -n '/Last State/,/^Ready/p'
```

Read the exit code from `Last State` → `Terminated` → `Exit Code`:

| Exit code | Meaning |
|---|---|
| 0 | the process finished successfully — a `CMD` that is not a server, or a one-shot job |
| 1 | application error; the `--previous` log has the reason |
| 2 | shell misuse inside the command |
| 126 | found but not executable (bad `command`, missing exec bit, wrong arch) |
| 127 | not found (typo in `command`, missing binary, wrong `PATH` in the image) |
| 137 | SIGKILL: **OOMKilled** (reason says `OOMKilled`) or a liveness-probe kill or a node eviction |
| 139 | SIGSEGV — native crash, wrong arch, or a C extension |
| 143 | SIGTERM — someone/something deleted or scaled the pod; not a crash |

Distinguish OOMKill from a liveness kill:

```sh
kubectl get pod <p> -o jsonpath='{.status.containerStatuses[*].lastState.terminated.reason}{"\n"}'
# OOMKilled  -> memory limit exceeded
# Error      -> the runtime killed it; check the probe and events
```

`OOMKilled` means the memory **limit** was hit — see
`kubernetes-resource-tuning` for the sizing fix. An `Error` with exit 137 and
`Killing` events right after a liveness failure is a probe problem — see
`kubernetes-manifests`.

### ImagePullBackOff / ErrImagePull

```sh
kubectl describe pod <p> | sed -n '/^Events:/,$p'
```

| Event reason text | Cause | Fix |
|---|---|---|
| `Failed to pull image "x:v1"` + `manifest unknown` | tag does not exist | check the registry; did the build push? |
| `manifest for x:v1 not found` / `not found: manifest unknown` | typo'd tag or wrong registry path | `docker manifest inspect x:v1` |
| `rpc error: code = Unauthorized` / `401 Unauthorized` | bad or missing imagePullSecret | `kubectl get secret <name> -o jsonpath='{.data.\.dockerconfigjson}' \| base64 -d`; check the SA's `imagePullSecrets` |
| `toomanyrequests: You have reached your pull rate limit` | Docker Hub anonymous rate limit | authenticate, or mirror to your registry |
| `no matching manifest for linux/arm64` | single-arch image on a multi-arch node | build multi-arch, or pin the node |
| `ImagePullBackOff` with no event text | transient network/DNS | check the node's egress, registry mirror, `imagePullPolicy` |

`ErrImagePull` is the *first* failure; `ImagePullBackOff` is the retry loop
after it. Both are the same root cause. The event with the `Failed` reason has
the actual error text.

### Pending

`Pending` means no container has started. Two causes:

1. **Unschedulable** — no node fits. `kubectl describe pod` shows
   `FailedScheduling` with the reason and a message like
   `0/12 nodes are available: 3 Insufficient cpu, 9 node(s) didn't match Pod's node affinity/selector.`
   Read the numbers: they name the constraint. The fix is almost always the
   **request** being too large, not a scheduler bug.
2. **Waiting on a volume** — `FailedScheduling: pod has unbound immediate
   PersistentVolumeClaims` (a missing PVC or a StorageClass with no
   provisioner), or the pod is Pending while a StatefulSet's volume is being
   attached (`Multi-Attach error` → the PVC is still attached to another node).

```sh
kubectl describe pod <p> | sed -n '/^Events:/,$p'
kubectl get pvc
kubectl get pv
kubectl get storageclass
kubectl get events --sort-by=.lastTimestamp | grep -i -E 'schedul|volume|attach|provision'
```

### Init:Error / Init:CrashLoopBackOff

Init containers run to completion, in order, before the app containers start.
They are where migrations, secret fetching, and config generation live.

```sh
kubectl logs <p> -c <init-container-name> --previous
kubectl get pod <p> -o jsonpath='{.spec.initContainers[*].name}{"\n"}'
```

An init container that waits for a dependency creates a deadlock if that
dependency is a service in the same pod set that is waiting for this pod. Do
not have a migration init container wait on the database it is migrating.

### CreateContainerConfigError

The pod references a ConfigMap or Secret that does not exist, or a key that
does not exist. The event names the exact object:
`configmap "api-config" not found`. Check the namespace, the name, and the key
with `kubectl get cm api-config -o yaml`.

## Getting into a pod

In order of preference:

**1. Ephemeral debug container** (Kubernetes 1.25+, needs the
`ephemeralcontainers` subresource enabled on the cluster):

```sh
kubectl debug -it <pod> --image=nicolaka/netshoot --target=<container> -- sh
kubectl debug -it <pod> --image=busybox --share-processes -- ps aux
kubectl debug pod/<pod> -it --image=<registry>/<your-image>:debug --target=api
```

`--target` joins the target container's namespaces, so you see its filesystem
and processes. `--share-processes` shares the PID namespace. This does **not**
restart the pod and does not change the pod spec, which makes it safe in
production.

**2. A debug variant of your own image.** Add a `debug` target to the
Dockerfile (see `container-image-debugging`) and run it as an ephemeral
container. Keeps the exact libraries and filesystem of the real image.

**3. `kubectl cp`** — works on a *running* pod only, and needs `tar` in the
image:

```sh
kubectl cp <ns>/<pod>:/app/logs ./logs          # pod -> local
kubectl cp ./config.yaml <ns>/<pod>:/tmp/config.yaml   # local -> pod
```

It fails on a `CrashLoopBackOff` pod (nothing is running to copy from). For a
dead pod, you cannot get in at all — the container filesystem is gone. Logs are
the only evidence, and `--previous` is your friend.

**4. A same-image pod with a shell.** If the image is distroless, `sh` does not
exist. Then either use the debug target or copy a static busybox in — see
`container-image-debugging`.

**5. The node.** Last resort, and it needs SSH access to the node, which most
managed clusters do not give you. If you get there:
`crictl ps -a`, `crictl logs <id>`, `crictl inspect <id>`, `journalctl -u kubelet`.
For a containerd node, `ctr -n k8s.io containers list`; for Docker-runtime
nodes, `docker ps -a`. Prefer the ephemeral container.

## Logs

```sh
kubectl logs <p>                                  # current container
kubectl logs <p> --previous                       # previous instance — the crash
kubectl logs <p> -c sidecar                       # named container
kubectl logs -f -l app=api --all-containers=true   # by label, all pods, follow
kubectl logs -l app=api --since=1h --prefix        # aggregate
kubectl logs -l app=api --tail=1000 | grep -i error
kubectl logs <p> --timestamps=true | tail -50
```

Gotchas:

- **`--previous` only exists if the container has restarted at least once.** On
  a first crash you get the current attempt's logs, which may be empty.
- **`kubectl logs` cannot read a sidecar's logs after the main container
  exits** (a native sidecar keeps running). A sidecar that loops forever fills
  the node disk — set resource limits and consider whether it needs to be a
  sidecar at all.
- **Log rotation discards evidence.** `kubectl logs` reads the container log
  files; with aggressive rotation (or `log-driver: none`) the crash output is
  gone. Ship to a log aggregator.
- **Multi-line stack traces are one log line per line in JSON**; `kubectl logs`
  renders them awkwardly. Use the aggregator or `--timestamps` and jq.
- **`kubectl logs -l` needs a label selector, not a pod name**, and silently
  returns nothing if the label does not match.

## Other triage commands

```sh
kubectl get events --sort-by=.lastTimestamp | tail -40
kubectl get events -A --field-selector reason=FailedScheduling
kubectl top pods -n <ns> --sort-by=memory
kubectl get pod <p> -o wide                      # NODE, IP, NODE IP
kubectl describe node <node> | sed -n '/Conditions/,$p'   # pressure conditions
kubectl get rs -l app=api                        # rollout stuck? ReplicaSet
kubectl rollout status deploy/api
kubectl get deploy api -o jsonpath='{.status.conditions}' | jq
kubectl auth can-i get pods -n <ns>               # is it RBAC?
```

When a rollout is stuck, the ReplicaSet view tells you whether the new pods
ever became ready:

```sh
kubectl get rs -l app=api -o custom-columns=\
NAME:.metadata.name,DESIRED:.spec.replicas,CURRENT:.status.replicas,READY:.status.readyReplicas
```

`DESIRED == CURRENT` but `READY` lagging means the new pods are not passing
readiness — an application problem, not a rollout controller problem.

## Gotchas

- **Events expire.** They are kept for about an hour by default. If you are
  debugging a pod that has been Pending for two days, the useful event is
  gone; `kubectl describe` still shows the *conditions*, but not the original
  `FailedScheduling` message. Capture events as they happen.
- **`kubectl describe pod` for a deleted pod returns nothing.** Events survive
  for a while; `kubectl get events --field-selector involvedObject.name=<old>`
  may still have them.
- **`ImagePullBackOff` is not a network problem most of the time.** It is a
  nonexistent tag, a missing `imagePullSecret`, or a rate limit. The event text
  says which.
- **`CrashLoopBackOff` with exit 0 and no logs** is usually a `command` that
  runs a one-shot thing (`bash -c 'echo hi'`, a migration in the wrong place)
  and exits successfully. Read `.spec.containers[].command/args`.
- **Exit 137 is not automatically OOM.** Check `lastState.terminated.reason`;
  `OOMKilled` is the discriminator, and a liveness-probe kill also produces
  137 (with a `Killing` event immediately before).
- **A pod stuck in `Terminating`** is usually a finalizer, a hung preStop hook
  (still within `terminationGracePeriodSeconds`), or a volume that will not
  detach. `kubectl get pod -o jsonpath='{.metadata.finalizers}'` and wait out
  the grace period before considering `--force --grace-period=0`.
- **`--force` deletion leaves the process running** on the node until the
  kubelet notices. It is a last resort and it breaks the graceful-shutdown
  contract. See `kubernetes-production-safety`.
- **`kubectl exec` into a `CrashLoopBackOff` pod usually fails** with
  `container is waiting to start` or a race. Use an ephemeral debug container
  with `--target` or `--share-processes` instead; it is more reliable.
- **Init containers and app containers do not share a lifecycle for probes** —
  an app container's probe does not run until the init containers finish. A
  slow init container looks like a pod that is "stuck" with no probe events.
- **Same pod name, different container**: `kubectl logs <pod>` requires `-c` if
  the pod has more than one container, and the error is
  `a single container is required but 2 were found`.
- **`kubectl get pod -o yaml` after the fact shows `status` only; the `spec` is
  what you applied.** Use `kubectl get pod -o json | jq .metadata.annotations`
  to find the controller's revision and the original manifest hash.

## When you cannot get in at all

1. `--previous` logs and `describe` events.
2. Recreate the failure locally: `docker run --rm -it <image> <command>` with
   the same env from the ConfigMap/Secret. Most "won't start" bugs reproduce on
   a laptop in under a minute.
3. Ephemeral debug container with the same image at a `:debug` target.
4. If the pod is CrashLooping, scale the Deployment to 0 and run one pod with
   `--command` overridden to a shell — but only in a non-production namespace.
5. The node, via `crictl`, only if you have access and only with approval.

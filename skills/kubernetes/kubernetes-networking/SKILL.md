---
name: kubernetes-networking
description: Get Kubernetes networking right - choosing between ClusterIP, NodePort, LoadBalancer and Ingress, writing Services with correct selectors, resolving cluster DNS, writing default-deny NetworkPolicies, and configuring ingress controllers. Includes the "service works, ingress does not" troubleshooting path. Use when exposing a service outside the cluster, when 503 from an ingress, when a Service has no endpoints, or when segmenting workloads with network policy. Triggers on "Ingress 503", "service has no endpoints", "ClusterIP", "NodePort", "LoadBalancer", "NetworkPolicy", "ingress-nginx", "kebab-case host", "DNS resolution", "CORS", "TLS secret".
compatibility: Examples target Kubernetes 1.27+ with the ingress-nginx controller and Gateway API v1 for new installs. `spec.ingressClassName` replaces the deprecated `kubernetes.io/ingress.class` annotation. Cloud load balancer behaviour is provider-specific; verify with your provider's docs.
metadata:
  version: "1.0"
---

# Kubernetes Networking

Two separate problems that people conflate: **how pods reach each other** (the
Service) and **how traffic from outside reaches a pod** (Ingress or a cloud
load balancer). Almost every "the ingress is broken" report is a Service or
selector problem, not an Ingress problem.

## Choosing the exposure mechanism

| Mechanism | Reachable from | Use for | Cost |
|---|---|---|---|
| `ClusterIP` (default) | inside the cluster only | every internal service | none |
| `Headless` (`clusterIP: None`) | inside, returns pod IPs | StatefulSets, peer discovery, client-side LB | none |
| `NodePort` | any node IP, any port 30000-32767 | bare clusters, dev, a debug path | a port per service on every node |
| `LoadBalancer` | cloud LB → a Service | a real TCP/UDP endpoint | provider LB cost, slowest path |
| `Ingress` | HTTP(S) through a controller | 99% of external web traffic | needs a controller |
| Gateway API | HTTP(S) + TCP/UDP, multi-team | new installs | needs a controller that supports it |

Default: **ClusterIP behind an Ingress.** Use `LoadBalancer` for raw TCP, for a
service with no HTTP, or when the Ingress controller is not something you
control. `NodePort` only for debugging or in a cluster with no controller —
it is a permanent hole in the firewall, one port per service per node.

## Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: api
  labels: { app: api }
spec:
  type: ClusterIP
  selector:                # selects POD labels, not Deployment labels
    app: api
  ports:
    - name: http
      port: 80            # the port other pods connect to
      targetPort: http    # the CONTAINER's named port; "http" not 8080
      protocol: TCP
  # sessionAffinity: None   # or ClientIP
  # publishNotReadyAddresses: false  # true = include not-ready pods as endpoints
```

- **`targetPort` by name**, not number, so the Service and the container cannot
  drift apart. Verified behaviour: a Service whose `targetPort` does not match
  any container port produces a Service with **zero endpoints** and 503s from
  the Ingress.
- **`selector` is optional.** Without one, the Service is a stable DNS name
  that you populate yourself with `EndpointSlice`/`Endpoints` — this is how
  headless services for StatefulSets and how some service meshes work. With a
  wrong selector, the Service has zero endpoints and nothing errors.
- **`ports[].port` is the service port; `targetPort` is the container port.**
  Confusing them is the classic "why is nothing listening on 8080" — the
  container listens on 8080, the Service is 80, and `kubectl port-forward`
  against 8080 works while in-cluster clients on 8080 fail.
- `NodePort` and `LoadBalancer` both use the same `ports` list; the type only
  changes how the port is published.
- **`externalTrafficPolicy: Local`** on a NodePort/LoadBalancer preserves the
  client source IP but means only nodes running a pod answer, and other nodes
  forward or drop. `Cluster` (the default) hides the client IP but balances
  across all nodes.
- **`sessionAffinity: ClientIP`** pins a client to one backend for
  `sessionAffinitySeconds`. If you need this, you almost certainly need a
  better fix (sticky sessions at the Ingress, or a real session store).

### Check endpoints first — always

```sh
kubectl get svc api
kubectl get endpoints api                 # <ip>:<port> per ready pod; EMPTY means zero ready pods
kubectl get endpointslice -l kubernetes.io/service-name=api
kubectl describe svc api | sed -n '/Endpoints:/,$p'
```

`kubectl get endpoints` returning `<none>` is the single most useful
diagnostic in cluster networking. It means one of:

1. The `selector` does not match any pod label.
2. Pods exist but are **not Ready** (readiness probe failing, or not started).
3. `targetPort` does not match a container port name/number.
4. Pods are Ready but on a node whose networking is broken (CNI).

## DNS

Every namespace gets a DNS server; Service names resolve within the cluster.

| Name | Resolves to |
|---|---|
| `api` | the ClusterIP of `api` in the same namespace |
| `api.default` | same, explicit namespace |
| `api.default.svc.cluster.local` | fully qualified |
| `api.ns.svc.cluster.local` | across namespaces |

- **Short names only work in the same namespace.** A pod in `staging` must use
  `api.production.svc.cluster.local`. The most common cross-namespace bug.
- **Headless Service (`clusterIP: None`)** returns the **pod IPs** in DNS
  instead of a ClusterIP. That is how `pg-0.pg-headless.ns.svc.cluster.local`
  works for a StatefulSet. A headless Service also needs
  `publishNotReadyAddresses: true` for peers to find each other **before** they
  are ready (a database cluster's bootstrap depends on it).
- **Search domains** are `<namespace>.svc.cluster.local svc.cluster.local
  cluster.local`. A ConfigMap with `DB_HOST: api` in namespace `staging`
  resolves to `api.staging` — if you meant `api.production`, it silently goes
  to the wrong (or nonexistent) service.
- **CNI DNS issues** look like intermittent NXDOMAIN. If only some pods fail to
  resolve, compare the pods' `dnsConfig` and the node's resolv.conf; CoreDNS
  pods being evicted or the `kube-dns` Service having no endpoints is the usual
  cause. `kubectl -n kube-system get pods -l k8s-app=kube-dns`.
- **egress DNS from a pod** goes through the node's resolver unless you set
  `dnsPolicy`/`dnsConfig`. A NetworkPolicy that blocks egress to port 53 to
  *other* namespaces is a classic "DNS stopped working" incident.

## Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: api
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "10m"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "60"
    cert-manager.io/cluster-issuer: letsencrypt
spec:
  ingressClassName: nginx        # NOT the old kubernetes.io/ingress.class annotation
  tls:
    - hosts: [api.example.com]
      secretName: api-tls        # must exist in the SAME namespace
  rules:
    - host: api.example.com      # must be a DNS name, no scheme, no port, no path
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api
                port: { name: http }   # must match a Service port NAME
```

- **`ingressClassName` is the current field.** The `kubernetes.io/ingress.class`
  annotation is deprecated; a manifest with both is ambiguous. If the cluster
  has more than one controller, omitting `ingressClassName` means the default
  one and may be the wrong one.
- **The Ingress is a routing layer, not a proxy to your pod.** It needs a
  `Service` with an `Endpoint`/`EndpointSlice` behind it. An Ingress pointing
  at a Service with no endpoints gives 503 from the controller.
- **The controller is a separate workload.** If it is not installed,
  `kubectl get ingress` still works and the Ingress object is created, but
  nothing serves it. `kubectl -n ingress-nginx get pods` is the check.
- **`pathType` matters**: `Prefix` matches path segments (`/api` matches
  `/api/x` but not `/apix`); `Exact` is exact; `ImplementationSpecific` defers
  to the controller and is a compatibility hazard. Use `Prefix` unless you have
  a reason.
- **Host must be a bare hostname.** `https://api.example.com:443/path` in the
  `host` field produces a config the controller silently drops. Port goes in
  `nginx.ingress.kubernetes.io/backend-port` or the Service.
- **TLS secrets are per-namespace.** A `secretName` referencing a Secret in
  `ingress-nginx` is not found in your namespace, and the error
  `error: secrets "api-tls" not found` appears on the Ingress, not the pod.
- **Annotations are controller-specific.** `nginx.ingress.kubernetes.io/*` is
  ingress-nginx only; on Traefik or an ALB controller the same annotation is
  ignored silently.
- **Long-running requests** need
  `nginx.ingress.kubernetes.io/proxy-read-timeout` raised, or the connection
  is cut at the controller's default (often 60s) even though the app is fine.
- **Rate limits, body size, and timeouts on the annotation side** are where most
  "works in-cluster, fails through ingress" bugs live. A 413 means
  `proxy-body-size`; a 504 means `proxy-read-timeout`; a 502 means the
  controller cannot reach the backend Service.

### Gateway API (for new installs)

Gateway API is the successor to Ingress and separates the "load balancer"
(`Gateway`) from the "routes" (`HTTPRoute`). It supports TCP/UDP, header
matching, and per-team ownership. If your controller supports it (ingress-nginx
with Gateway enabled, Envoy Gateway, Cilium, cloud LB controllers), prefer it
for new multi-team clusters. Verify controller support before committing to it —
support varies.

## NetworkPolicy

NetworkPolicy is **additive**: a pod selected by *any* NetworkPolicy is
restricted to what the union of the policies allow. Once any policy selects a
pod, all traffic not explicitly allowed is **denied** (for the pod's ingress
and/or egress, per the policy's `policyTypes`).

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: production
spec:
  podSelector: {}            # every pod in the namespace
  policyTypes: [Ingress, Egress]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-dns
  namespace: production
spec:
  podSelector: {}
  policyTypes: [Egress]
  egress:
    - to: [{ namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: kube-system } } }]
      ports: [{ protocol: UDP, port: 53 }, { protocol: TCP, port: 53 }]
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: api-allow
  namespace: production
spec:
  podSelector: { matchLabels: { app: api } }
  policyTypes: [Ingress, Egress]
  ingress:
    - from:
        - namespaceSelector: { matchLabels: { kubernetes.io/metadata.name: ingress-nginx } }
        - podSelector: { matchLabels: { app: web } }     # same namespace
      ports: [{ protocol: TCP, port: 8080 }]
  egress:
    - to: [{ podSelector: { matchLabels: { app: db } } }]
      ports: [{ protocol: TCP, port: 5432 }]
```

Apply the default-deny in a namespace you can break safely first. The
`kube-system` namespace label `kubernetes.io/metadata.name` is set
automatically on every namespace and is the reliable way to select a namespace
by name.

Rollout order (non-negotiable):

1. `default-deny` (Ingress+Egress) for the namespace.
2. An `allow-dns` egress policy for **every** pod — otherwise DNS dies and
   every hostname lookup fails, which looks like a total outage.
3. Per-workload allow policies, one at a time, each verified with a real
   request.
4. Only then, restrict the deny policy's scope.

Gotchas:

- **A default-deny policy with no allow rules and no DNS allow rule breaks
  every DNS lookup in the namespace.** `nslookup` hangs and the symptom is
  "all connections time out" rather than "connection refused".
- **NetworkPolicy needs a CNI that enforces it.** `kube-proxy` alone does not.
  Check `kubectl get pods -n kube-system -o wide` for Calico, Cilium, Antrea, or
  a network-plugin pod. If your CNI is flannel or a basic bridge, the policies
  are accepted and **silently ignored**.
- **Egress to the internet** needs an explicit allow plus DNS: two rules, or
  the app cannot resolve anything.
- **Policies do not apply to `hostNetwork` pods** and do not apply to traffic
  to/from the node itself.
- **A policy selecting the ingress controller's namespace** is how you let
  ingress in. If you write `from: [{ podSelector: ... }]` only, the
  controller's pod is in `ingress-nginx` and does not match — the Ingress
  returns 503 with no useful event.

## Troubleshooting: service works, ingress does not

Work this list in order. Each step is cheap and eliminates a class.

- [ ] 1. **Endpoints exist?** `kubectl get endpoints <svc>`. Empty → the problem
      is the Service or the pods, not the Ingress.
- [ ] 2. **Pods Ready?** `kubectl get pods -l app=<name>`. `0/1 READY` → the
      readiness probe is failing; the Ingress correctly refuses to route to it.
- [ ] 3. **Is the Ingress object accepted?** `kubectl describe ingress <name>`.
      Look for events: `Failed to load certificate`, `Admission webhook denied
      the request`, or nothing at all (no controller → no events).
- [ ] 4. **Which controller?** `kubectl get ingress -A` — the `CLASS` column.
      A controller that is not running will not reconcile. `kubectl -n
      ingress-nginx get pods`.
- [ ] 5. **Does the host match the request?** The controller routes on the
      `Host` header. A request to the IP instead of the hostname hits the
      default backend (usually a 404, sometimes a 503).
- [ ] 6. **Is the Service port name right?** The Ingress `backend.port.name`
      must match a `ports[].name` in the Service. A number works only if the
      Service uses a number. Mismatch → the controller emits a rejection event
      and serves 503.
- [ ] 7. **What does the controller log say?**
      `kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --tail=200 | grep -i <svc>`.
      A backend health check failure, a missing upstream, or a rejected
      configuration is named there.
- [ ] 8. **NetworkPolicy?** If the namespace has policies, the controller's pod
      may be denied egress to your Service's port. The Ingress works and the
      controller logs an upstream connection error. Test from inside:
      `kubectl -n ingress-nginx run t --rm -it --image=busybox -- wget -qO- http://api.<ns>.svc.cluster.local/`.
- [ ] 9. **TLS?** `Failed to load certificate` event, or the secret missing in
      the Ingress's namespace. Test over plain HTTP first to isolate it.
- [ ] 10. **Does it work from inside the cluster?**
      `kubectl run t --rm -it --image=curlimages/curl -- curl -sv http://api:80/`.
      If that fails, stop debugging the Ingress — the problem is the Service,
      the pods, or the network.

Useful controller-specific checks:

```sh
kubectl -n ingress-nginx get svc ingress-nginx-controller    # is it LoadBalancer/NodePort?
kubectl -n ingress-nginx logs deploy/ingress-nginx-controller --since=10m
kubectl -n ingress-nginx describe pod -l app.kubernetes.io/component=controller
kubectl get ingress <name> -o jsonpath='{.status.loadBalancer.ingress}'   # has it been assigned?
kubectl describe ingress <name>
```

`.status.loadBalancer.ingress` empty means the controller has not yet
programmed the load balancer — either no controller, or the cloud LB
provisioning is still pending.

## Gotchas

- **`kubectl port-forward svc/api 8080:80`** forwards to a Service and picks a
  ready pod; `kubectl port-forward pod/<pod> 8080:8080` targets that exact pod
  and that exact container port. Using the Service port against a pod's
  container port is a common mismatch.
- **A Service with no selector and no Endpoints** is valid and looks healthy.
  `kubectl get endpoints` is the only way to know.
- **`NodePort` allocates a random port in 30000-32767** unless you set
  `nodePort:`. A firewall that only allowed the expected port will break when
  the port changes on recreate.
- **A `LoadBalancer` Service on a cluster with no cloud provider integration**
  stays `pending` forever (`EXTERNAL-IP: <pending>`). Use NodePort or an
  Ingress instead.
- **In-cluster clients must not use `localhost`** — that is the pod's own
  loopback. Use the Service DNS name.
- **Session affinity plus `maxUnavailable: 0` and short `terminationGracePeriod`**
  is a recipe for dropped sessions on every deploy.
- **An Ingress with `path: /` and no `host`** becomes the default backend for
  the whole controller. One such Ingress hijacks unmatched traffic cluster-wide.
- **`proxy-body-size` defaults to 1 MB** on ingress-nginx. A file upload over
  1 MB gets a 413 that looks like an app bug.
- **CORS and preflight `OPTIONS`**: the controller answers preflight itself if
  configured, otherwise it forwards it. A 502 on `OPTIONS` is a backend
  problem, not a CORS problem.
- **Internal traffic that goes out to the Ingress and back in** (hairpin)
  breaks client-IP preservation and can loop if the Ingress DNS points at
  itself. Prefer the ClusterIP for internal callers.
- **Service mesh sidecars change all of this**: an `mTLS` mesh means a plain
  `curl` to a pod IP will fail while the Service works. Test with the mesh's
  proxy in the loop before blaming the network.

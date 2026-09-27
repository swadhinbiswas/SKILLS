# Chart patterns and values reference

Longer reference material for `helm-chart-authoring`. Read this when designing
a new chart's `values.yaml`, adding a subchart, wiring in generators or hooks,
or when an upgrade is about to change something structural.

## A full values.yaml

```yaml
# -- Number of replicas. Ignored when autoscaling.enabled is true.
replicaCount: 3

# -- Override the generated name (changes every resource name; treat as a migration)
fullnameOverride: ""
nameOverride: ""

image:
  repository: ghcr.io/org/api
  # -- Image tag. Empty means .Chart.AppVersion.
  tag: ""
  # -- Pin by digest instead of tag; takes precedence over tag when set.
  digest: ""
  pullPolicy: IfNotPresent

imagePullSecrets: []
nameSuffix: ""

serviceAccount:
  create: true
  name: ""
  annotations: {}
  automountToken: false

podAnnotations: {}
podLabels: {}

podSecurityContext: {}      # rendered with toYaml; empty means "no opinion"
securityContext: {}         # container-level

service:
  type: ClusterIP
  port: 80
  # -- Container port name or number.
  targetPort: http
  annotations: {}

ingress:
  enabled: false
  className: nginx
  annotations: {}
  hosts: []                 # [{ host: api.example.com, paths: [{ path: /, pathType: Prefix, service: api }] }]
  tls: []                   # [{ secretName: api-tls, hosts: [api.example.com] }]

resources:
  requests: { cpu: 500m, memory: 512Mi }
  limits:   { memory: 1Gi }

livenessProbe: {}
readinessProbe: {}
startupProbe: {}

autoscaling:
  enabled: false
  minReplicas: 3
  maxReplicas: 20
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
  behavior: {}

pdb:
  enabled: false
  minAvailable: 2           # or maxUnavailable; never both

networkPolicy:
  enabled: false
  ingress: []
  egress: []

persistence:
  enabled: false
  size: 8Gi
  storageClass: ""
  accessModes: [ReadWriteOnce]

nodeSelector: {}
tolerations: []
affinity: {}
topologySpreadConstraints: {}

terminationGracePeriodSeconds: 45
preStopSleepSeconds: 10

config: {}                 # rendered into a ConfigMap and checksum-annotated

tests:
  enabled: true

# Escape hatches, rendered verbatim
extraEnv: []
extraEnvFrom: []
extraVolumes: []
extraVolumeMounts: []
extraContainers: []
extraObjects: []            # raw manifests appended to the release
```

Every key gets a `# --` comment: the comments are the chart's generated
README, and they are the only documentation a values file has.

## ConfigMaps, generators, and roll-triggering

A mounted ConfigMap does not restart the pods that mount it. Annotate the pod
template with the rendered content's hash:

```gotemplate
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
```

`configMapGenerator` produces a name with a content hash, so a value change
rolls the Deployment with no annotation needed:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: {{ include "mychart.fullname" . }}-meta
data: {}                    # required, even if empty
configMapGenerator:
  - name: app-config
    files:
      - app.yaml={{ .Files.Get "files/app.yaml" }}
    literals:
      - LOG_LEVEL=info
```

- The top-level object must have `kind: ConfigMap` (or `Secret`) and an empty
  `data:`; everything generated goes under `configMapGenerator:`. This is the
  one place Helm uses the manifest's own `kind` as a marker.
- `secrets.SecretGenerator` is the same for secrets. **Not for real
  credentials** — the rendered Secret is stored in the release Secret in the
  cluster and printed by `helm get manifest` and `helm get values --all`.
  Use External Secrets, Sealed Secrets, or the Secrets Store CSI Driver.
- `.Files.Get` on a file not in the chart is a hard template error. That is the
  behaviour you want; it just fails at render time, not install time.
- To include the app's own files (a `values.schema.json`-validated config
  template), keep them in `files/` and read them with `.Files.Get`.

## Hooks and NOTES.txt

```gotemplate
{{- if .Values.migration.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "mychart.fullname" . }}-migrate
  annotations:
    "helm.sh/hook": pre-upgrade,pre-install
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: 2
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: migrate
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          command: ["/app/migrate"]
{{- end }}
```

- `helm.sh/hook`: `pre-install`, `post-install`, `pre-upgrade`, `post-upgrade`,
  `pre-delete`, `post-delete`, `test`, `pre-rollback`, `post-rollback`. Multiple
  values are comma-separated.
- `helm.sh/hook-weight`: lower runs first. Use it to order a migration before a
  seed before a smoke test.
- `helm.sh/hook-delete-policy`: `before-hook-creation` (default behaviour is
  to keep), `hook-succeeded`, `hook-failed`. Without `before-hook-creation`, a
  failed hook's resources linger and the *next* run fails on `AlreadyExists`.
- **Hook resources are not part of the release.** `helm uninstall` does not
  delete them; `helm rollback` does not undo them. Know what every hook creates.
- **A failing `pre-upgrade` hook aborts the upgrade.** With `--atomic` the
  release is rolled back, but the hook's side effects (a migration that
  partially applied) are not. Migrations must be idempotent and
  forward-compatible — see the expand-contract pattern in
  `databases/postgres-query-tuning`.
- `NOTES.txt` is printed after install and upgrade. Put the verification steps
  there: the URL, the port-forward command, how to check the rollout, and where
  the credentials came from. It is the first thing a new user reads.
- `templates/tests/` with `helm.sh/hook: test` runs on `helm test`, not on
  install. That is the right home for a smoke test — it never blocks a deploy.

## Subcharts

`Chart.yaml`:

```yaml
apiVersion: v2
name: mychart
version: 0.3.0
appVersion: "1.8.2"
dependencies:
  - name: postgresql
    version: 15.5.0
    repository: https://charts.bitnami.com/bitnami
    condition: postgresql.enabled
  - name: redis
    version: 20.1.0
    repository: https://charts.bitnami.com/bitnami
    condition: redis.enabled
    tags: [cache]
```

```sh
helm dependency update ./mychart     # resolves, writes charts/ and Chart.lock
helm dependency build ./mychart      # installs from Chart.lock, reproducible
helm dependency list ./mychart
```

- **Commit `Chart.lock`.** `helm dependency build` from the lock is
  reproducible; `helm dependency update` re-resolves and can pick up a newer
  patch. In CI, use `build`.
- **Commit `charts/`** if your pipeline does not have network access to the
  upstream repositories. `.helmignore` must not exclude `charts/`.
- **`condition: <subchart>.enabled` is how a parent turns a subchart off.**
  Without it, every dependency installs unconditionally. `helm template` will
  show you: if a subchart's resources appear and you did not set its values,
  the condition is missing or mistyped.
- **Subchart values are namespaced.** `postgresql.auth.username`, not
  `username`. A parent values file that puts a subchart's key at the top level
  is silently ignored, and the subchart's own default applies.
- **`global:` is the only cross-subchart channel.** `global.imageRegistry`,
  `global.imagePullSecrets`, `global.securityContext` are the keys most
  subcharts honour. Check which ones a given subchart actually reads — there is
  no standard.
- **`tags:`** lets you `--set tags.cache=false` to exclude dependencies that
  share a tag, in addition to `condition`.
- **You cannot override a subchart's templates from the parent.** If the
  subchart does not expose the value you need, fork it. Fighting it with
  `postRenderer` or a global is how charts become unmaintainable.
- **A subchart's resources are installed and upgraded as part of the parent's
  release.** `helm uninstall` removes them. A StatefulSet subchart (a database)
  is a serious commitment — the PVCs and the data are outside Helm's model
  depending on the subchart's `volumeClaimTemplates` and reclaim policy.

## values.schema.json

Cheap validation that turns a silent revert into a hard error:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["image", "service"],
  "properties": {
    "replicaCount": { "type": "integer", "minimum": 0 },
    "image": {
      "type": "object",
      "required": ["repository"],
      "properties": {
        "repository": { "type": "string", "minLength": 1 },
        "tag": { "type": "string" },
        "digest": { "type": "string", "pattern": "^$|^sha256:[a-f0-9]{64}$" },
        "pullPolicy": { "enum": ["Always", "IfNotPresent", "Never"] }
      },
      "additionalProperties": false
    },
    "service": {
      "type": "object",
      "required": ["port"],
      "properties": {
        "type": { "enum": ["ClusterIP", "NodePort", "LoadBalancer"] },
        "port": { "type": "integer", "minimum": 1, "maximum": 65535 },
        "targetPort": { "type": ["string", "integer"] }
      }
    },
    "ingress": {
      "type": "object",
      "properties": {
        "enabled": { "type": "boolean" },
        "className": { "type": "string" }
      }
    }
  }
}
```

- `helm lint` and `helm template` validate values against it and fail with a
  message naming the offending path (`image.digest: Does not match pattern`).
- `"additionalProperties": false` at the top level is what catches a **renamed
  value key** — exactly the silent-revert bug. Use it on every sub-object whose
  shape is stable.
- Use `"anyOf": [{"type": "string"}, {"type": "null"}]` for optional strings; a
  bare `null` in a values file is a common thing to allow.
- Draft 2020-12 is current; older Helm versions validate against draft-07
  semantics. Verify with `helm lint` on your exact Helm version, and check
  `helm show values` output.

## Gotchas

- **`appVersion: 1.8` is a float.** Quote every version in `Chart.yaml` and in
  values.
- **`{{- ... }}` chomps the preceding newline.** Every `toYaml` inside a nested
  block needs `| nindent N` with the right N; `| indent N` leaves the first
  line on the key's line and misaligns the rest. A misaligned `toYaml` produces
  YAML that parses but nests wrongly — always `helm template` and diff it.
- **`tpl` renders templates inside a string value.** `tpl (.Values.config |
  toYaml) .` lets users put `{{ }}` in a value. That is a template-injection
  surface if values come from anywhere untrusted (a PR, a webhook, a
  user-supplied form). Do not `tpl` untrusted values.
- **`lookup` returns nothing under `helm template`.** A template that depends on
  cluster state renders one way in CI and another in production. Avoid `lookup`
  in anything that affects output; if you must, gate it on
  `.Capabilities.HelmVersion` and a `values` toggle.
- **`.Capabilities.APIVersions.Has` needs `--api-versions` in CI.** Without it,
  a version-gated block renders as if the API is unavailable, and you ship a
  chart whose CI rendering differs from its production rendering.
- **`--set` for lists and maps is a quoting trap.** `--set
  ingress.hosts[0].host=api.example.com --set
  ingress.hosts[0].paths[0].pathType=Prefix` in a Makefile or a shell script
  will break on `[` and on commas. Use a values file, or `--set-file` for file
  contents.
- **`helm template` without `-f` renders defaults.** Gate CI on
  `helm template <release> ./chart -f values-prod.yaml`, the same values file
  the deploy uses.
- **`helm install --replace` on an existing release name** deletes the old
  release and creates a new one, losing the revision history you would roll
  back to. It is not a migration path.
- **`helm get values <release> -a`** shows the fully merged values — the only
  reliable way to find out what a release is actually configured with when
  someone else's values file is in play.
- **`helm get manifest`** renders everything including generated Secrets. Never
  paste it into a ticket.
- **Helm does not upgrade CRDs.** `helm show crds ./chart` and apply them
  separately with `kubectl apply`, checking the release notes for a storage
  version migration. A chart that ships a CRD needs that documented in its
  README and in the release process.
- **`helm.sh/hook: test` resources are not run by `helm upgrade`** and do not
  block it; they only run on `helm test`. Do not rely on them as a deploy gate
  in a pipeline that does not run `helm test`.
- **A `lookup`-free chart still behaves differently under `helm install` vs
  `helm upgrade`** for resources created outside the release (a PVC created by
  a hook, a namespace created once). Plan for them explicitly.
- **`--set-string` vs `--set`**: `--set replicas=3` yields an int, but
  `--set image.tag=1.8` yields a float that renders as `1.8` and fails an image
  pull. `--set-string image.tag=1.8` forces a string.
- **`required` fails at render time with the message you provide** — which is
  the best error message a Helm user will ever see. Use it for anything that
  cannot have a sensible default.

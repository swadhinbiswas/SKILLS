---
name: helm-chart-authoring
description: Write and upgrade Helm charts safely - template structure, a values.yaml that will still work in a year, helpers, subcharts, and the upgrade traps that break running clusters: renamed values, immutable fields, and changes that must not be applied in place. Covers helm template and helm diff as CI gates. Use when creating a chart, when a helm upgrade fails or silently reverts a value, or when reviewing a values change before release. Triggers on "Chart.yaml", "values.yaml", "helm upgrade", "helm template", "helm diff", "subchart", "helm hooks", "immutable field", "_helpers.tpl".
compatibility: Helm 3.x. `helm template`, `helm diff` (helm-diff plugin), and `helm lint` are used. `helm upgrade --install`, `--atomic`, and `--wait` are Helm 3 defaults. Chart API v2 (`apiVersion: v2`).
metadata:
  version: "1.0"
---

# Helm Chart Authoring

A chart is a program that renders YAML from values. Two failure modes matter:
it renders the wrong thing (a bug), or it renders something that cannot be
applied to a running cluster (an upgrade trap). The second one is what causes
outages.

Read `references/chart-patterns.md` when designing a new `values.yaml`, adding
a subchart, wiring up `configMapGenerator` or hooks, or writing a
`values.schema.json`.

## The layout

```
mychart/
  Chart.yaml          # name, version, appVersion, dependencies
  values.yaml         # defaults, documented, comments on units
  values.schema.json  # optional but very cheap validation
  templates/
    _helpers.tpl
    deployment.yaml
    service.yaml
    ingress.yaml
    serviceaccount.yaml
    NOTES.txt
    tests/
      test-connection.yaml
  charts/             # vendored subcharts (committed or `helm dep update`)
  .helmignore
```

`Chart.yaml`:

```yaml
apiVersion: v2
name: mychart
description: The thing this chart installs
type: application
version: 0.3.0        # chart version; bump on EVERY change
appVersion: "1.8.2"   # quoted: a bare 1.8 is a float and renders as 1.8
```

`appVersion` **must be quoted**. `appVersion: 1.8` renders as `1.8` and a
Kubernetes field expecting a string rejects it, or silently renders `1.8` where
`1.8.0` was meant. Same for any version in values.

## values.yaml: structure that survives

```yaml
replicaCount: 3
image:
  repository: ghcr.io/org/api
  tag: ""              # empty means .Chart.AppVersion; prefer digest
  digest: ""
  pullPolicy: IfNotPresent
service:
  type: ClusterIP
  port: 80
  targetPort: http
resources:
  requests: { cpu: 500m, memory: 512Mi }
  limits:   { memory: 1Gi }
podSecurityContext: {}   # rendered with `toYaml`; empty means "no opinion"
securityContext: {}
nodeSelector: {}
tolerations: []
affinity: {}
serviceAccount: { create: true, name: "", annotations: {} }
ingress:
  enabled: false
  className: nginx
  hosts: []            # [{ host: api.example.com, paths: [{ path: /, pathType: Prefix }] }]
  tls: []              # [{ secretName: api-tls, hosts: [api.example.com] }]
autoscaling:
  enabled: false
  minReplicas: 3
  maxReplicas: 20
  targetCPUUtilizationPercentage: 70

# Rendered verbatim; the escape hatch for anything not modelled
extraEnv: []
extraVolumes: []
extraVolumeMounts: []
```

A fully annotated values file — every optional component, persistence, PDB,
probes, and the `# --` doc comments Helm turns into a README — is in
`references/chart-patterns.md`.

Rules that prevent upgrade breakage:

- **A value key, once published, is a promise. Never rename or remove it.**
  Add a new key, deprecate the old, and support both for at least one release:
  ```gotemplate
  {{- $legacy := .Values.service.targetPort | default 8080 }}
  {{- $port := .Values.service.portNumber | default $legacy }}
  ```
  A renamed key makes `helm upgrade` **silently fall back to the chart's
  default**, which is the worst possible failure: the deployment succeeds and
  is wrong. Add `values.schema.json` to make a renamed key a hard failure
  instead.
- **Every value is documented in `values.yaml` with a comment saying the
  unit and the default.** The file is the chart's README and its API.
- **Nested, not flat.** `service.port` survives a rename of `service`; a flat
  `servicePort` collides with everything else.
- **`enabled: false` for every optional component** (Ingress, HPA, PDB,
  ServiceAccount, NetworkPolicy), and gate the whole template on it with
  `{{- if .Values.ingress.enabled }}`. A chart that renders a half-configured
  Ingress is worse than one that renders none.
- **Never put a required value in `values.yaml` with a real default.** Leave it
  empty and fail loudly, so a missing value is an error and not a wrong
  deployment:
  ```gotemplate
  {{- required "ingress.hosts[0].host is required when ingress.enabled=true" .Values.ingress.hosts }}
  ```
  `required`, `fail`, and `.Values.x | default` are your validation tools.
- **Quote anything that could be parsed as a number or a bool.** `port: 8080`
  is fine; `appVersion` and any semver are not.

## Templates

Every resource should carry the standard labels so `helm uninstall` and
selectors work:

```gotemplate
{{/* templates/_helpers.tpl */}}
{{- define "mychart.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mychart.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "mychart.labels" -}}
app.kubernetes.io/name: {{ include "mychart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "mychart.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mychart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "mychart.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "mychart.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
```

`selectorLabels` must be **exactly** the subset used in
`spec.selector.matchLabels` and in `Service.spec.selector`, and it must never
include `version` or `chart` — those change on upgrade and a Deployment's
selector is immutable. This one function is the most common source of
"helm upgrade fails with field is immutable".

Rendering a resource:

```gotemplate
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "mychart.fullname" . }}
  labels:
    {{- include "mychart.labels" . | nindent 4 }}
spec:
  {{- if not .Values.autoscaling.enabled }}
  replicas: {{ .Values.replicaCount }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "mychart.selectorLabels" . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "mychart.selectorLabels" . | nindent 8 }}
      annotations:
        checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
    spec:
      serviceAccountName: {{ include "mychart.serviceAccountName" . }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repository }}{{ if .Values.image.digest }}@{{ .Values.image.digest }}{{ else }}:{{ .Values.image.tag | default .Chart.AppVersion }}{{ end }}"
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          {{- with .Values.resources }}
          resources:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.extraEnv }}
          env:
            {{- toYaml . | nindent 12 }}
          {{- end }}
```

- `{{- ... }}` chomps the preceding whitespace/newline; without it you get blank
  lines and, worse, broken indentation inside a `toYaml` block. Every
  `toYaml` needs `| nindent N` (or `| indent N` when the key already has a
  line) with the right N.
- `toYaml` on an empty map renders `{}`, which is valid YAML for an empty map —
  so `podSecurityContext: {}` is fine.
- `quote` anything interpolated into a value that must be a string, especially
  image tags and versions.
- `{{- with }}` skips the block when the value is empty — that is what you
  want for optional blocks, and what you do *not* want for a field that must be
  rendered as `[]` or `{}`.

### ConfigMaps, hooks, and subcharts

Three things that need more space than they deserve here, all in
`references/chart-patterns.md`:

- **Content hashing and `configMapGenerator`.** A mounted ConfigMap does not
  restart the pods that mount it. Either annotate the pod template with
  `checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}`
  or use `configMapGenerator`, whose content-hashed name rolls the Deployment
  on every value change. Never use `secrets.SecretGenerator` for real
  credentials — the rendered Secret lives in the release Secret in the cluster
  and in `helm get manifest` output.
- **Hooks.** `helm.sh/hook: pre-upgrade` for a one-shot migration,
  `post-upgrade` for a smoke check, `test` for something that only runs on
  `helm test`. Hook resources are **not** part of the release: `helm rollback`
  does not undo them, and `helm uninstall` does not delete them. Always set
  `helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded` or a failed
  hook's leftovers make the next run fail on `AlreadyExists`.
- **Subcharts.** Declare them in `Chart.yaml` with an exact version and a
  `condition: <sub>.enabled`; commit `Chart.lock` and use
  `helm dependency build` in CI so the resolved version is the one you tested.
  Subchart values are namespaced (`postgresql.auth.username`), and you cannot
  override a subchart's templates from the parent — fork it instead.

`NOTES.txt` is printed after install and upgrade. Put the "how do I check this
worked" instructions there: the URL, the port-forward command, how to watch the
rollout, where the credentials came from.

## Upgrade safety

The rules, in the order they matter:

1. **Render before you release.** `helm template` is the cheapest possible CI
   gate and it catches template bugs, `values` typos, and YAML errors without a
   cluster:
   ```sh
   helm lint ./mychart
   helm lint ./mychart -f values-prod.yaml
   helm template mychart ./mychart -f values-prod.yaml > /tmp/rendered.yaml
   kubectl apply --dry-run=server -f /tmp/rendered.yaml
   ```
   Gate CI on `helm template` succeeding. It is 30 seconds and it catches the
   entire class of "the chart renders invalid YAML" failures.
2. **Diff before you apply.** `helm-diff` plugin:
   ```sh
   helm diff upgrade myrelease ./mychart -n prod -f values-prod.yaml
   ```
   In CI, render both and diff the YAML in a PR. A diff that shows a removed
   selector, a changed image, or a dropped env var is a stop-the-line finding.
3. **`helm upgrade --install --atomic --wait --timeout 5m`.** `--install`
   makes a first-time install and an upgrade the same command, so a missing
   release is not a confusing error. `--atomic` rolls back on failure (and
   requires `--wait`). `--wait` blocks until the resources are ready, so a
   failed rollout is a non-zero exit rather than a hopeful one.
4. **Bump `version` in Chart.yaml on every change.** A `helm upgrade` to the
   same chart version is a no-op for the chart's own resources in some
   flows, and it makes "which version is deployed" unanswerable.
5. **`helm history` and `helm rollback` are your rollback.** `helm rollback
   <release> <revision>` restores the previous manifest. Record the revision
   number from a successful `helm history` before the risky upgrade. Helm
   rollback restores manifests; it does **not** undo data migrations, CRD
   changes, or anything a hook did.
6. **CRDs are not upgraded by `helm upgrade`.** `helm show crds` and apply them
   with `kubectl apply -f` in a separate, reviewed step, after checking the new
   version's storage migration notes.

### Immutable-field traps

`kubectl apply` on a changed spec can fail with
`field is immutable`. These are the ones that bite Helm users, because a
values change looks harmless:

| Field | Immutable? | Consequence |
|---|---|---|
| `Deployment.spec.selector` | **yes** | a label change in `selectorLabels` breaks every upgrade |
| `StatefulSet.spec.selector`, `.spec.serviceName`, `.volumeClaimTemplates` | yes | cannot change the governing Service or the PVC layout |
| `Job.spec.template` | yes (and `spec.selector`) | an HPA-driven Job or a Job with a changed template needs a new name |
| `Service.spec.clusterIP` | yes | changing `type` from `ClusterIP` to something else, or a `clusterIP` value, fails |
| `Secret.type` (some transitions) | yes | |
| PVC `spec` (size shrink, accessModes, storageClass) | yes | shrinking a `ReadWriteOnce` volume's claim fails; growth is one-way |
| Most fields of a **Service's** `ports[].nodePort` | partially | changing a `nodePort` requires deleting the Service |
| `metadata.name` / `metadata.namespace` | yes | renaming a resource is delete + create |

The chart-level defences: keep `selectorLabels` minimal and stable (no
`version`, no `chart`, no timestamp, nothing derived from a value that
changes); treat a `fullnameOverride` change as a migration, not a values
change, because it renames every resource; and apply the release to a scratch
namespace as part of release testing, because `helm diff` and
`--dry-run=server` do **not** surface immutability — that failure only appears
at apply time.

### The silent-revert trap

The worst Helm failure is one that succeeds. It happens when a value key no
longer exists in the chart: `helm upgrade -f values-prod.yaml` ignores the
unknown key, the chart's `values.yaml` default applies, and the release looks
healthy. Guards: `values.schema.json` with `"additionalProperties": false`,
a human-reviewed `helm diff` on the rendered output, and never renaming a
published key — add and deprecate.

## values.schema.json

One key idea: `"additionalProperties": false` turns a **renamed value key**
from a silent revert into a hard `helm lint` failure. A full annotated
example is in `references/chart-patterns.md`. Add the file for any chart whose
values shape is stable — it is thirty lines and it catches the worst class of
upgrade bug.

## Gotchas

- **`appVersion: 1.8` is a float.** Quote every version in `Chart.yaml` and in
  values. Same for any value that could parse as a number or a bool;
  `--set image.tag=1.8` is the same bug via the command line — use
  `--set-string`.
- **`{{- ... }}` chomps the preceding newline.** Every `toYaml` inside a nested
  block needs `| nindent N` with the right N. A misaligned `toYaml` produces
  YAML that parses but nests wrongly, so always `helm template` and diff it.
- **`nindent` vs `indent`**: `indent` prefixes subsequent lines only, so
  `key: {{ toYaml . | indent 4 }}` leaves the first line on the key's line and
  misaligns the rest.
- **`tpl` renders templates inside a string value.** Without it a user-supplied
  `{{ }}` renders literally; with it, a value from an untrusted source is a
  template-injection surface. Do not `tpl` untrusted values.
- **`lookup` returns nothing under `helm template`.** A template that depends on
  cluster state renders one way in CI and another in production. Avoid `lookup`
  in anything that affects output.
- **`.Capabilities.APIVersions.Has "networking.k8s.io/v1/Ingress"`** gates on
  the cluster version. `helm template --api-versions` supplies it in CI; without
  that flag, version-gated blocks render as if the API is unavailable.
- **`.Files.Get` fails at template time if the file is missing** — a clear hard
  error, but it means the file must be in the chart when it is packaged.
- **`--set` for lists and maps is a quoting trap.** `--set
  ingress.hosts[0].host=…` breaks on `[` and on commas in a Makefile or a
  shell script. Use a values file, or `--set-file` for file contents.
- **`helm template` without `-f` renders the defaults**, which is rarely what CI
  should test. Render with the same values file the deploy uses.
- **`helm get values <release> -a`** shows the fully merged values — the only
  reliable way to find out what a release is actually configured with.
- **`helm uninstall` deletes everything the release owns**, including resources
  a human later edited. It does not delete resources created by hooks unless
  the hook's delete policy says so.
- **A chart that renders a `Secret` with real credentials puts them in the
  release Secret in the cluster and in `helm get manifest` output.** Use an
  external secret operator.
- **Helm does not upgrade CRDs.** `helm show crds ./chart` and apply them
  separately with `kubectl apply`, checking the release notes for a storage
  version migration. A chart that ships a CRD needs that in its release
  process.
- **`helm.sh/hook: test` resources do not block `helm upgrade`.** They only run
  on `helm test`. A pipeline that never runs `helm test` is not gated by them.
- **`helm install --replace` reuses the name and deletes the old release**,
  losing the revision history you would roll back to. It is not a migration
  path.

## Release checklist

- [ ] `helm lint` and `helm template -f values-prod.yaml` pass in CI
- [ ] `helm diff upgrade` reviewed by a human
- [ ] `helm upgrade --install --atomic --wait --timeout 5m`
- [ ] `Chart.yaml` `version` bumped; `appVersion` quoted
- [ ] `selectorLabels` unchanged from the last release
- [ ] No value key renamed; no `fullnameOverride` change
- [ ] PVC size changes are increases only
- [ ] CRDs applied separately, with the storage-version check
- [ ] Rollback revision recorded before the upgrade
- [ ] `values.schema.json` in place if the values shape is not trivial

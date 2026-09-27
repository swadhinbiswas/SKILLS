---
name: gcp-cloud-architecture
description: Build on GCP with the platform's own idioms - project/folder/org hierarchy, keyless service-account auth, Cloud Run vs GKE vs Cloud Functions, Pub/Sub for messaging, BigQuery for analytics, and the VPC-native vs Serverless Connector decision. Use when a user asks how to run a container or function on GCP, when a service-account key file is being created, when "Permission denied" needs decoding, or when choosing between Cloud Run, GKE, and Cloud Functions.
compatibility: Google Cloud, current as of 2026. gcloud/Google-managed defaults change frequently; verify region availability, quota names, and any product in preview/GA with the current docs.
metadata:
  version: "1.0"
---

# GCP Cloud Architecture

GCP's default is good and its gotchas are specific. Three of them cause most
trouble: **resource hierarchy and inherited IAM** (roles granted at the
project level leak across every service and are the reason "Permission denied"
is confusing), **service account keys** (GCP's historic mechanism and the one
you should almost never use), and **the VPC egress decision** (a Cloud Run
service needs a connector, a GKE node needs Cloud NAT, and picking the wrong
one costs money and latency).

## The mental model: hierarchy, projects, and billing

```
Organization
  └── Folder (per cost centre / env)
        └── Project (per workload + env)   <-- IAM, quotas, billing boundary
              └── resources
```

- **The hierarchy is an inheritance chain for IAM and, separately, for
  org policy constraints.** A role granted at the folder or org level applies
  to every project beneath it.
- **A project is the unit of blast radius.** It has its own IAM, its own
  service quotas, its own service accounts, and (usually) its own billing
  export. One project per workload per environment is the default that keeps
  both security and cost attribution workable.
- **Project number is the immutable identity.** Project *ID* can be recycled
  after deletion; the project *number* never changes. Policies, logs, and
  anything that must survive a project rename should key on the number
  (`projects/PROJECT_NUMBER`).
- **Quotas and default limits are per-project-per-service.** A new project
  starts at zero; a "quota exceeded" right after a new project launches is
  almost always a default quota, not a bug in your code.

## Service accounts and keyless auth (the default)

Every workload on GCP should run as a **service account** and authenticate with
the attached identity, not with a key.

| Who runs it | Identity | How it authenticates |
|---|---|---|
| Cloud Run / Cloud Functions | The service's **service account** | Attached; the metadata server issues a short-lived **OAuth2 access token** (1 hour) automatically. No key, no token code |
| GKE (Workload Identity) | A Kubernetes service account bound to a GCP service account via IAM | Short-lived token projected into the pod |
| GCE / GKE node | The VM/node service account, attached at instance creation | Metadata server |
| A CI pipeline | A service account in the CI project's SA pool (Workload Identity Federation) | Short-lived token via OIDC, no stored key |
| BigQuery / Cloud Storage in a job | The job's service account | Attached |
| A human | Their own user account, ideally via group membership | `gcloud auth login` caches a **user** credential locally - fine for a workstation |

**The default: never create a service account key.** Reasons, in order of
severity:

1. A JSON key is a **permanent, non-expiring password** for everything that
   service account can do. It does not appear in audit logs as a workload
   identity, it does not expire, and revoking it means finding every copy.
2. Keys leak. They end up in Docker images, CI variables, `/tmp`, git history,
   and people paste them into tickets. A single key in a public repo is a
   full account compromise for that service account's permissions, and it is
   routinely found by automated scanners.
3. There is **no need**: every Google-managed runtime supports attached
   identities and short-lived tokens. A key is only correct when the
   principal is a machine that cannot reach the token endpoint - and in
   practice that means a legacy external system, not something you design.

If you are handed "just create a key", the answer is to establish which of
these applies instead:

- **CI/CD** -> Workload Identity Federation: a service account in the CI's
  project, a pool + provider config for the CI system, and an attribute
  mapping (`google.subject`) to a service account. The CI system mints a
  short-lived token; no key. (For GitHub Actions, the provider config's
  attribute mapping must set `assertion.sub` from the repo/ref - this is the
  equivalent of the AWS OIDC `sub` boundary; see `aws-iam-and-security` for
  the pattern.)
- **A VM / GKE workload** -> attach the service account to the instance, or
  use Workload Identity for GKE.
- **Cloud Run / Cloud Functions / Cloud Build / Vertex AI** -> attach the
  service account; the runtime does the rest.
- **A local workstation** -> `gcloud auth application-default login` for user
  ADC; `gcloud auth application-default login --impersonate-service-account=...`
  for testing as a service account. No file to leak.
- **An on-prem or third-party system that genuinely cannot do federation** ->
  then, and only then, a key - created for one service account with minimal
  roles, with a rotation alarm, and deleted when the migration lands.

If a key must exist, treat it like a production database credential:
one service account per consumer, minimum roles, a key rotation alarm,
never in an image, never in git, and a deletion date. `gcloud iam service-accounts keys disable` exists
specifically to rotate them.

## IAM: roles, not permissions (mostly) and the inheritance trap

- **Grant predefined roles, not primitive permissions.** A primitive role
  (`roles/editor`, or worse `roles/owner`) grants far more than intended.
  Use the narrowest predefined role that works, or a **custom role** with an
  explicit permission list for anything unusual.
- **Project-level roles apply to every service in the project.** Granting
  `roles/datastore.user` at the project level gives access to *all* Firestore
  and Datastore databases in the project, not one. When a principal should
  touch one resource, grant at the resource level.
- **Inheritance is why permissions are broader than expected.** A role granted
  on the folder reaches every project; a role granted on the project reaches
  every resource. When auditing "who can read this table", walk *up* the
  hierarchy, not just at the resource. The security review that only checks the
  resource's own bindings is incomplete by construction.
- **The basic roles are not for day-to-day use.** `roles/owner`,
  `roles/editor`, `roles/viewer` at any scope are a sign that the right
  narrower role was not identified. `roles/viewer` in particular is a data
  exfiltration grant for every readable resource in scope.
- **The authenticated user can be a principal**: `allUsers` and
  `allAuthenticatedUsers` in an IAM binding make a resource **public**.
  Public buckets and public Cloud Run invocations are the two most common
  accidental exposures in GCP. Make "public" an explicit, reviewed decision.
- **Service account user / act-as is separate from the service account's own
  permissions.** A principal with `iam.serviceAccounts.actAs` on a service
  account can *become* it and gain everything it can do. `roles/iam.serviceAccountUser` is
  powerful and often granted too broadly.
- **Org policy constraints** are the GCP equivalent of AWS SCPs: they can
  *deny* (e.g. disable external IPs, restrict where resources may be created,
  force uniform bucket-level access). They are inherited and are the right tool
  for "no resource in this org may have a public IP".

### "Permission denied" decoding

```
PERMISSION_DENIED: User sa-deployer@my-prod.iam.gserviceaccount.com does not have
iam.googleapis.com/roles/run.admin access to the project. Requested resource:
projects/my-prod, denied because the request did not have the required
permission.
```

- The message names the **permission or role** and the **scope**. Look up the
  permission's role with `gcloud iam roles describe` or the IAM roles page.
- If the principal has the role, check the **hierarchy above** for a deny, and
  check **org policy constraints** - they can deny an action regardless of
  IAM.
- If the principal is a user, check **group membership**: a user's effective
  permissions are the union of their direct grants and every group they are in,
  and group membership is not always obvious from the account.
- **Service account email typos** and using the *default* compute service
  account rather than a dedicated one are frequent causes.
- The **exact string** in `does not have X access` tells you the role the
  error is complaining about; grant the narrowest role containing that
  permission, at the narrowest scope.

## Compute: which one

| Service | Use it for | Do not use it for |
|---|---|---|
| **Cloud Run** | Containers, event-driven, scale to zero, no cluster to own. The default for HTTP services and Pub/Sub/S3/Eventarc consumers | Long-running background compute; anything needing a persistent VM or a specific OS-level capability |
| **Cloud Functions** | Small glue code triggered by GCP events; no container build | Anything substantial - it is Cloud Run under a different name; if you need more control, use Cloud Run |
| **GKE (Autopilot / Standard)** | You need cluster-level control: DaemonSets, custom scheduling, GPUs, node pools, a specific workload runtime | A stateless HTTP service - Cloud Run is cheaper and simpler |
| **GCE** | A specific VM shape, a legacy workload, a bastion | New general-purpose compute - it is a server you now own |
| **Cloud Build** | CI, image builds, deploy pipelines | - |

- **Cloud Run specifics that matter:** concurrency (how many requests per
  instance) is a first-class setting; set it *above* 1 to cut instance count
  and cost, and verify your function is safe for concurrent requests (it must
  be - instances are shared). Minimum instances keep a warm instance so you
  avoid cold starts, at a fixed cost. Cloud Run **scales to zero**, so a
  service with no traffic has no running instance - and the *first* request
  after idle pays the cold start. Set a minimum instance count for anything
  latency-sensitive.
- **Cloud Run and the network:** by default a Cloud Run service has no VPC
  egress to private addresses; see the VPC decision below.
- **Cloud Run jobs** are the batch equivalent (one-shot containers, retries,
  scheduling) and beat a cron-triggered service.
- **GKE Autopilot** removes node management for a per-vCPU pricing model;
  **Standard** is cheaper at scale if you tune it and needs you to own
  upgrades, autoscaling, and node pools. Choose deliberately, not by default.

## Networking in GCP: the decision that bites

Two ways to give a serverless/workload service reachability into a VPC, and
picking wrong is a common and expensive mistake. (Full VPC design is in
`cloud-networking-and-vpc`; this is the GCP-specific decision.)

| | **VPC-native / Direct VPC egress** | **Serverless VPC Access (Serverless VPC Connector)** |
|---|---|---|
| Applies to | Cloud Run (with Direct VPC egress), Cloud Functions gen2 | Cloud Functions gen1, Cloud Run **without** direct egress, Cloud Scheduler, Pub/Sub push, Cloud Tasks |
| How traffic leaves | The instance gets an **internal IP in your subnet** and uses the VPC's routes - Cloud NAT for internet | Traffic is **proxied** through a managed connector; the connector lives in a VPC and does Cloud NAT for internet |
| Egress IP (for allowlists) | The instance's internal IP; for internet, the Cloud NAT address | The connector's Cloud NAT address, or the connector's internal IP for VPC-internal |
| Scaling behaviour | Scales with the instances, using the subnet's IPs | Connector scales independently; can be a bottleneck; limits apply |
| Cost | Cloud NAT (per-hour + per-GB) | Connector (per-hour + per-GB) **and** often Cloud NAT behind it |
| Latency | Lowest - direct | One extra hop (the connector proxies VPC traffic) |
| VPC-native features (Private Service Connect routes, Cloud NAT with static addresses) | Available | Not the same |

- **Choose Direct VPC egress on Cloud Run when you can.** It is fewer moving
  parts, lower latency, and a cleaner IP story. The connector exists for
  services that do not support direct egress.
- **Both need Cloud NAT for internet egress** from a private subnet. Attach a
  Cloud Router to the VPC/subnet for Cloud NAT to work; forgetting the router
  is a common setup error.
- **For reaching Google APIs (Pub/Sub, Storage, BigQuery) from inside a VPC,
  do not use Cloud NAT** - use **Private Service Connect** (Private Google
  Access) so the traffic stays on Google's network and you do not pay NAT for
  Google-internal calls. This is a significant, easy win; see the main skill
  and `cloud-cost-optimization`.
- **Serverless connectors and Cloud Run instances are not in your subnets'
  security group / firewall story the way VMs are** - traffic from a connector
  arrives from the connector's range; scope firewall rules accordingly, and do
  not assume "it is in the VPC" means "it passed through my firewall rules".
- **Private Service Access / Private Service Connect** are different things
  with confusingly similar names: PSA reserves an IP range for Google-managed
  production services (a range you plan for in CIDR), while Private Service
  *Connect* gives a managed service its own private endpoint. Know which one a
  design needs before you reserve a /20 you do not need.

## Data: BigQuery, Pub/Sub, and the rest

- **BigQuery** is the warehouse. Design around **partitioning** (by ingestion
  time or a date column - partitioning is what keeps a query from scanning
  everything) and **clustering** (by a high-cardinality filter column). An
  unpartitioned table scanned by a date-range filter is a cost incident.
  Streaming inserts are per-row priced and, on standard tables, buffered (so
  they are not immediately visible) - use Storage Write API or a batch load
  for volume.
- **BigQuery table and dataset ACLs** and the **BigQuery Data Policy Tags /
  column-level security** are the tools for "this column contains PII" - a
  tag-based policy applies across every table the tag is on, which is far more
  maintainable than per-table grants.
- **Pub/Sub** is the default messaging bus. It is **at-least-once**; your
  subscriber must be idempotent. Enable **exactly-once delivery** where the
  publisher and subscriber are both Google-managed and support it, but do not
  assume it removes the need for idempotency. Ordering is per-message-key and
  only within a region, and enabling ordering has throughput implications.
- **Pub/Sub push to Cloud Run / a webhook**: enable **authentication** (OIDC
  token) on the push endpoint so Pub/Sub is authenticated, otherwise your
  endpoint is an open invoke target. A push subscription also needs to handle
  Pub/Sub's retry behaviour (retries on non-2xx) - your endpoint must be
  idempotent or you will get duplicates.
- **Cloud Storage**: uniform bucket-level access (and public access prevention)
  should be on by default now; per-object ACLs are legacy. Lifecycle rules to
  Nearline/Coldline/Archive are a cost lever, and Archive retrieval is hours,
  not instant. See `cloud-cost-optimization`.
- **Datastream** is the managed change-data-capture path into BigQuery /
  Cloud SQL / AlloyDB if you need replication rather than batch loads.

## Gotchas

- **Keyless does not mean unauthenticated.** Metadata-server credentials on a
  VM with an attached service account can be read by *any* process on that VM,
  including an SSRF from a vulnerable app. "Keyless" removes the file, not
  the impersonation risk - keep the SA's roles minimal and do not run untrusted
  code on a VM holding a powerful SA.
- **A project number, not a project ID, in IAM and resource policies**, when
  the binding must outlive a rename or a project-id change. This is the GCP
  equivalent of using an ARN.
- **`gcloud auth application-default login` writes a credential to your
  machine.** It is a *user* credential, it is not attached to a service
  account, and in a container that copies it in, you have reintroduced the key
  problem with an extra step. Use `--impersonate-service-account` (with a
  short lifetime) instead of a key for local testing.
- **Deleting a service account can be blocked** by the key-count quota and by
  resources still referencing it; disable keys first, then delete. A disabled
  key produces `401 Invalid JWT Signature` / `unauthorized_client` - which
  looks like a clock-skew or IAM problem and is neither.
- **Service account key quotas are per project** (default 10 per service
  account). A script that creates a key per deploy hits `RESOURCE_EXHAUSTED`
  and leaves keys behind.
- **`roles/editor` at the folder level is effectively `owner` for every
  project under it.** Folder-level basic roles are the single most common
  finding in a GCP access review.
- **All `allUsers`/`allAuthenticatedUsers` bindings on a Cloud Run service, a
  bucket, or a Cloud Function make it public.** For Cloud Run, "public" also
  means *unauthenticated invocations are accepted* - set
  `--no-allow-unauthenticated` (and its Terraform equivalent) unless the
  service is genuinely a public web endpoint.
- **Cloud Run concurrency > 1 means concurrent requests into one instance.**
  Any shared mutable state (a module-level variable, a connection) must be
  safe for that, or you have created a race that only appears under load.
- **Cloud NAT is charged per hour and per GB**, and it is easy to leave a NAT
  attached to a subnet nobody uses. See `cloud-cost-optimization` and
  `cloud-networking-and-vpc`.
- **Cloud Functions and Cloud Run deployments are regional**; a global
  load balancer in front of regional backends is a real architecture, not a
  setting. Understand the routing before you assume a single global endpoint.
- **GKE nodes run with a service account that usually has broad scope** (node
  SA). Use Workload Identity so pods get narrow per-namespace identities, and
  do not let the node SA be the pod's identity.
- **Org policy "disable service account key creation"** is the control that
  makes the keyless default enforceable. Turn it on if you have not.
- **`gcloud` project/region/zone defaults are per-configuration**, and a
  `gcloud config set` in one context silently changes the target for the next -
  always pass `--project` explicitly in scripts.

## Safety notes

- Never create or accept a service account key as the default answer; push
  toward Workload Identity Federation, attached identities, or impersonation.
- If a key is unavoidable, it is one service account per consumer with the
  minimum roles, with a rotation alarm and a deletion date - and it never goes
  in an image, an env file committed to git, or a CI log.
- Do not grant `roles/owner`, `roles/editor`, or folder-level basic roles.
  Removing a folder-level `Editor` is a project; adding one is an incident.
- Public resources (`allUsers`, `allAuthenticatedUsers`, `--allow-unauthenticated`)
  must be explicit, reviewed, and the default must be private. Check them in
  the same PR as any IAM change.
- Set budgets and alerts per project so a misconfigured project (a runaway VM,
  a bigquery scan) is caught by billing, not by surprise.

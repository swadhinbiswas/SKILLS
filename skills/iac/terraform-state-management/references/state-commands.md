# State command reference

`terraform state` subcommands, flags, and the cases they exist for. Verified
against Terraform 1.x; run `terraform state -help` and
`terraform state <sub> -help` on your installed version for the authoritative
list, because subcommands have been added over time.

## `terraform state` subcommands

| Command | What it does | Key flags | Use when |
|---|---|---|---|
| `list` | Prints every managed resource address in state | `-state=path`, (no `-target`) | Finding the exact address for a plan line |
| `show [addr]` | Attributes of one resource, or whole state if omitted | `-no-color` | Confirming what is actually recorded before a `mv`/`rm` |
| `pull` | Writes the entire state to stdout | `-state=path` | Backup, or moving state between backends |
| `push` | Replaces the entire state from stdin/file | `-force` (skips lineage/serial check) | Restoring a backup, migrating a backend |
| `mv [src] [dst]` | Moves one instance to another address in the same state | `-dry-run` | Resource adopted from/into a module, list-to-map renumbering |
| `rm [addr...]` | Removes addresses from state; **does not destroy** | `-dry-run` | Object already deleted in the cloud |
| `replace-provider [src] [dst]` | Rewrites the provider address of resources in state | - | Splitting/merging provider instances or aliases |
| `list-resources [module]` | Resources *and* data sources under a module | `-name-only` | Auditing what a module owns |
| `show-provider [addr]` | Which provider backs a resource | - | Debugging alias / credential problems |

### Safe invocations

```sh
# backup, always before anything else
terraform state pull > "state-$(date -u +%Y%m%dT%H%M%SZ).json"

# see the address you are about to touch
terraform state list | grep 'module\.db\.'
terraform state list | grep -c 'module\.new\.aws_subnet\.x'   # must be 0 before a mv

# preview a move or removal
terraform state mv -dry-run 'module.old.aws_x.y' 'module.new.aws_x.y'
terraform state rm -dry-run 'aws_s3_bucket.orphan'

# confirm afterwards
terraform plan
```

`state push` refuses a state whose `serial` is lower than the backend's, or
whose `lineage` differs, unless `-force` is given. Use `-force` only when you
have read the backup's `serial` and `lineage` fields yourself and know why the
check is wrong. It is a blunt instrument; log who used it and why.

## `terraform workspace` subcommands

| Command | Effect | Caution |
|---|---|---|
| `new <name>` | Creates and selects a workspace | Initialises resources from scratch; not a clone of another workspace's resources |
| `select <name>` | Switches namespace inside the one state file | Every subsequent `plan`/`apply` targets the new one. In CI this is the single most dangerous default |
| `list` | Names + current | - |
| `show` | Current workspace | Print this in every CI log line |
| `delete <name>` | Removes the workspace *and its state entries* | Does **not** destroy infrastructure. You get orphans |
| `select -or-create <name>` | 1.x convenience | Fine in a dev loop, avoid in pipelines |

Workspaces share one state file, one lock, and one serial. Two concurrent
`apply` runs in different workspaces serialise on the same lock.

## `terraform import`

### Block form (recommended, 1.5+)

```hcl
import {
  to = aws_s3_bucket.assets
  id = "acme-prod-assets"
}
```

- Runs during `apply`; the plan shows an `import` action for that address.
- Can be `for_each`ed over a list, and can carry `provider` / `lifecycle`
  blocks in 1.7+.
- Import blocks are also how you migrate: keep them in config until the next
  successful apply, then delete them in a follow-up commit.

### CLI form (older, still needed for one-offs)

```sh
terraform import 'module.db.aws_rds_cluster.main' 'prod-cluster'
```

Accepts exactly one address. Writes state immediately, touches no config. After
a CLI import the resource has no import block, so a later colleague will be
confused by a plan that shows a create for an object that exists - add the
import block or a comment.

### Import errors, decoded

| Error | Real cause | Fix |
|---|---|---|
| `Cannot import non-existent remote object` | Wrong account/region for the provider, or wrong ID format | Check `provider` region and the resource's `Import` docs section for the accepted ID form |
| `Import ... not supported` | The resource type has no importer | You may need to write the resource fresh and `terraform state mv` the old address, or manage it out-of-band |
| `Invalid resource address` | Address uses `count`/`for_each` incorrectly, e.g. `aws_x.y[0]` when the config has `for_each` | `terraform state list` to see the real address |
| Import succeeds, plan then shows a huge diff | Live object differs from config | Reconcile: this diff is the real state of the world. Triage it; do not apply it blind |

## The state JSON shape

For `state pull` output, `state push` input, and backup inspection. Do not edit
these fields by hand; understand them.

```json
{
  "version": 4,
  "terraform_version": "1.9.8",
  "serial": 42,
  "lineage": "0f8b1c2e-3a4d-5f6b-7c8d-9e0f1a2b3c4d",
  "outputs": {},
  "resources": [
    {
      "module": "module.db",
      "mode": "managed",
      "type": "aws_rds_cluster",
      "name": "main",
      "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
      "instances": [
        {
          "schema_version": 0,
          "attributes": { "id": "prod-cluster", "engine": "aurora-postgresql" },
          "dependencies": ["aws_vpc.main"],
          "create_before_destroy": true
        }
      ]
    }
  ],
  "check_results": null
}
```

- `version` - state format version, not a Terraform release number.
- `serial` - monotonically increasing; every state write bumps it. A state
  with a lower `serial` than the backend's will be rejected by `state push`.
- `lineage` - UUID identifying this state *history*. A mismatch means you are
  pushing state from a different configuration; that is a hard error, and it is
  the check that stops you overwriting a colleague's work.
- `index` key appears on instances of resources using `for_each` - the map key.
  Its absence with a non-zero instance count means the resource was indexed by
  `count` (or had no meta-argument).
- `deposed` key appears on instances mid-replacement (`create_before_destroy`),
  which is why a state taken mid-apply is confusing to read.
- `check_results` holds `check` block outcomes; they are informational and do
  not block apply.

`terraform show -json tfplan` and the state JSON share a shape; `jq` filters
that work on one usually work on the other, which makes them easy to combine
into CI assertions.

## Backend comparison

| Backend | Locking | Encryption at rest | Notes |
|---|---|---|---|
| `s3` | Lock object in the bucket (`use_lockfile = true`); the old `dynamodb_table` option is deprecated | SSE-KMS on the bucket is the control; the backend's `encrypt` option is belt-and-braces | Configure region/credentials outside HCL. Workspaces are object keys under `key` |
| `gcs` | Native object locking on the bucket (must be enabled on the bucket) | Google-managed by default; CMEK available | Backend supports `prefix`; per-workspace objects are under it |
| `azurerm` | Blob leases; requires the identity to have lease actions on the container | Storage account encryption + optional CMK | `resource_group_name`, `storage_account_name`, `container_name`, `key` |
| `local` | Filesystem lock only | Whatever the disk does | Fine for a laptop; never for a shared root module |
| Terraform Cloud / Enterprise | Server-side, per-run | Vendor-managed, with state versioning and an audit trail | One workspace per (root module, environment); the only backend that makes concurrency safe by default |

### S3 backend, what to set and where

```hcl
terraform {
  backend "s3" {
    bucket = "acme-tfstate-prod"
    key    = "payments/prod/terraform.tfstate"
  }
}
```

```sh
# credentials, region, encryption, and locking come from the environment
export AWS_REGION=eu-west-1
export AWS_PROFILE=prod

# credential-free backend operations (no AWS creds needed at all)
aws s3 cp s3://acme-tfstate-prod/payments/prod/terraform.tfstate recovered.json
```

- The backend does **not** need AWS credentials to be configured for
  `plan`/`apply`; it gets them from the normal provider chain once operations
  touch the cloud. Keeping the bucket in a separate account from the resources
  is a legitimate pattern - the backend identity needs write access to state,
  the provider identity needs the resource permissions.
- A backend identity that can write state but the provider identity cannot is
  a real split: a plan that reads resources fails with
  `No valid credential sources found` while state operations still work. That
  asymmetry is a feature, not a bug.

## Recovering state from bucket versioning (S3)

```sh
# list versions newest first
aws s3api list-object-versions --bucket acme-tfstate-prod \
  --prefix payments/prod/terraform.tfstate --query 'reverse(sort_by(Versions,&LastModified))[:5].[VersionId,LastModified,Size]' --output table

# fetch a specific version
aws s3api get-object --bucket acme-tfstate-prod \
  --key payments/prod/terraform.tfstate \
  --version-id '<VERSION_ID>' recovered.json

# inspect before pushing
jq '{version, terraform_version, serial, lineage, resources: (.resources|length)}' recovered.json
```

Same shape with `gcloud storage versions list` / `gcloud storage cp --version`
for GCS, and `az storage blob list --include m` for Azure. Confirm the
`serial`/`lineage` before `terraform state push recovered.json`.

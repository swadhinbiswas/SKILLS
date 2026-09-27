---
name: terraform-state-management
description: Configure and repair Terraform state - remote backends with locking, encryption at rest, `terraform state` commands, and surgery for moved/renamed resources with `moved` blocks, plus workspace pitfalls and the never-edit-state-by-hand rule. Use when a plan wants to destroy and recreate something that already exists, when a state lock is held or errored, when state has to move between accounts/regions/backends, or when someone says "just edit the state file".
compatibility: Terraform 1.x. Covers the S3 backend `use_lockfile` option that replaced S3 native locking; verify your installed version's backend docs with `terraform version`.
metadata:
  version: "1.0"
---

# Terraform State Management

State is the mapping from config addresses to real infrastructure. Almost every
scary Terraform incident is a state problem wearing a plan problem's clothes.

**The rule that prevents most incidents: never hand-edit a state file.** State
is serialised, versioned, lineage-checked, and (with remote backends)
concurrently written. Editing it in an editor produces a state that either fails
`state version does not match` or, worse, silently orphans a real resource that
nobody can destroy. Use `terraform state` subcommands, `moved` blocks, or
`import`. If you believe you must edit the file, you are missing a supported
operation - find it before you touch bytes.

## Workflow

- [ ] 1. Back up: `terraform state pull > state-$(date -u +%Y%m%dT%H%M%SZ).json`
      (and back up the whole backend bucket/prefix, not just the object)
- [ ] 2. `terraform state list` - find the address you actually care about
- [ ] 3. Decide the operation: move / import / remove / replace
- [ ] 4. Do it with a committed `moved` block where one is possible
- [ ] 5. Re-plan; the destroy/create must be gone
- [ ] 6. Keep the backup until the next successful apply

## Backends: pick one, configure it once

Backend config lives in a `backend "s3" {}` block with **no** values other than
the bucket name and key. Credentials, region, encryption, and locking come from
environment variables, shared config, or `-backend-config` flags - never from
HCL, or you will commit them.

```hcl
terraform {
  backend "s3" {
    bucket = "acme-tfstate-prod"
    key    = "payments/prod/terraform.tfstate"
    region = "eu-west-1"
  }
}
```

```sh
terraform init -backend-config=profile=prod -reconfigure
```

- `bucket` + `key` is the state address. Two root modules sharing a `key` will
  fight over the same state and corrupt it. One key per (root module,
  account/region/environment) tuple.
- `-reconfigure` is required when the backend itself changes. Without it,
  `terraform init` may silently keep the old backend from `.terraform/terraform.tfstate`.
- `dynamodb_table` on the S3 backend is the **old** locking mechanism and is
  deprecated. Current Terraform uses a lock object in the S3 bucket itself,
  enabled with `use_lockfile = true`. Turn the flag on explicitly; do not add
  a DynamoDB table. Verify the flag name against `terraform version` and the
  backend docs for your release - it was introduced in the 1.10 line.
- `terraform { backend ... }` cannot use variables, locals, or functions. If
  you need a computed bucket name, generate the backend block (for example with
  Terragrunt) or accept a CLI flag.

## State security

State is a plaintext database of your whole infrastructure, including
whatever secrets you failed to mark `sensitive`. Treat a state file at the same
tier as production database credentials.

- **Encrypt at rest.** For S3, enable bucket-level SSE-KMS (not just SSE-S3) so
  you control the key and can audit it; the backend's `encrypt` option is
  additional, not a replacement.
- **Version the bucket and enable Object Lock / retention** so a bad
  `apply` or a `rm` is recoverable. State versioning is what makes
  "restore the previous state" a real procedure.
- **Never make the bucket public.** A public state bucket leaks every
  password you `sensitive`-flagged and every one you did not.
- **Restrict write access to CI.** Humans read; only the pipeline role writes.
  Anyone who can write state can make Terraform adopt, destroy, or exfiltrate
  anything in the account.
- **Log reads.** CloudTrail on the bucket, or S3 access logs, tells you who read
  state and when. A read of a state file is a credential access event.
- **Do not put state in a Git repo**, even a private one: it survives branch
  deletes, gets copied into CI caches and forks, and has no retention.
- **Terraform Cloud / Enterprise** gives you per-run state isolation, locking,
  and an audit log for free. It is worth it above ~5 root modules or once
  non-engineers start applying.

## `terraform state` commands you actually use

Full table with flags and examples: `references/state-commands.md`. The four
that matter most:

```sh
terraform state list                      # every managed address
terraform state show 'module.db.aws_rds_cluster.main'   # attributes in state
terraform state pull > backup.json        # full state out
terraform state push backup.json          # full state in (validates serial + lineage)
```

`state pull` / `state push` are the escape hatches: they move an entire state,
which is how you migrate a backend, restore after corruption, or hand state
between teams. `state push` refuses a state whose `serial` or `lineage` is
lower than what the backend holds, which is the point - it stops you clobbering
someone else's concurrent apply.

## The four state operations, and which tool to use

| Situation | Use | Never |
|---|---|---|
| Renamed a resource, moved it into/out of a module, or split one resource into several | **`moved` block** in config, committed | `state mv` as a one-off, because the next person re-does it |
| Infrastructure already exists, Terraform has never seen it | `import` block (1.5+) or `terraform import` | writing the resource into state by hand |
| A resource is gone from the cloud but still in state | `terraform state rm` (after `refresh` proves it is gone) | `rm -rf` of the whole state |
| You want Terraform to *adopt* an existing object and then be able to manage it | `import` then reconcile drift | `--allow-missing` style hacks, or provider flags that suppress the error |

### `moved` blocks are the default

A `moved` block is config, so it lives in git, applies on every plan, and shows
up in code review. `state mv` is a one-shot command that leaves no trace in
config; the next `apply` on a fresh checkout is fine only because the state
already moved, and the next person to recreate the old address has no idea why
the address changed.

```hcl
moved {
  from = aws_instance.web[0]
  to   = aws_instance.web["primary"]
}

moved {
  from = module.vpc.aws_subnet.private[0]
  to   = module.network.aws_subnet.private["eu-west-1a"]
}
```

Cross-module moves work: `from = module.old.aws_x.y`, `to = module.new.aws_x.y`.
You can chain several `moved` blocks for a multi-hop rename, and a `moved`
block that has been applied becomes a no-op (Terraform prints nothing). To move
a whole module, use `from = module.old`, `to = module.new` - Terraform walks the
resource addresses.

`moved` blocks do **not** support renaming with a changed `for_each` *key* when
the module's key set changes shape, and they cannot move a resource into or out
of a different backend or state file.

### When you must use `state mv` / `state rm` anyway

`state mv` is right when the change is not expressible in config: an
experiment's resources leaving a state, a one-off rescue, or a resource
adopted out of band. Always record what you did in the PR/commit message, and
follow the same run with a `plan` to prove nothing is going to be destroyed.

```sh
# 1. prove the destination is free
terraform state list | grep -c 'module.vpc.aws_subnet.private\['   # expect 0

# 2. move
terraform state mv 'module.old.aws_subnet.a' 'module.new.aws_subnet.b'

# 3. immediately verify
terraform plan    # must show no destroy/create for that address
```

`state mv` cannot move a resource to or from a different state file - that is
`moved` across backends done with `state pull`/`state push` on each side, or
Terraform Cloud's state-sharing.

### `import` for adopting existing infrastructure

```hcl
import {
  to = aws_db_instance.main
  id = "prod-db"
}
```

`import` blocks run on the next `apply` (1.5+) and are visible in the plan as an
`import` action. `terraform import ADDRESS ID` is the older CLI form: it writes
state immediately, does nothing to config, and leaves the resource with no
`moved`-block history. Prefer the block.

After import, **reconcile the config with reality before applying.** An imported
resource whose config does not match the live object will produce a diff on the
very next plan, and that diff may be destructive. Run `terraform plan` and
triage the diff, do not just apply it.

The recurring import error:

```
Error: Cannot import non-existent remote object
  with aws_db_instance.main,
The provider returned no result for the given identifier
```

Means one of: the object is in a different account, the region is not the one
the provider is configured for, the ID is the *name* when the importer wants an
ARN (or vice versa), or the object genuinely does not exist. The importer for
that resource accepts specific ID forms - check the resource's
`Import` section in the provider docs.

## Workspaces: the most-misused feature

```sh
terraform workspace new staging
terraform workspace select prod
terraform workspace show
```

- Workspaces are **namespaces inside one state file**, not separate state files.
  A stale workspace entry is a corrupted entry in the same file.
- `terraform workspace select <name>` fails with
  `Workspace "staging" doesn't exist.` after a state `rm`/restore until you
  recreate it. This is harmless; create it and re-apply.
- **A workspace name change renames the state key** in the backend, which looks
  exactly like "all my infrastructure disappeared". Check `terraform workspace
  show` before assuming a data-loss incident.
- Environments are usually better modelled as **separate state files** (separate
  key prefixes or buckets) driven by a per-environment pipeline, or as a
  `for_each` over environments in one root. Workspaces are for *temporary*
  experiments and short-lived branches, not for permanent environment promotion.
- **`terraform workspace select` in CI is a footgun**: two concurrent jobs in
  different workspaces share one state file's lock, so they serialise, and a
  job that assumes its default workspace can act on the wrong one. Pass
  `-workspace`? No such flag - either set the workspace explicitly in every
  command, or use separate backends.
- Never `terraform workspace delete` without understanding which state entries
  it holds. It removes the resources from state; it does not destroy them, so
  you get orphans you can no longer manage.

## Recovering from the common disasters

**State lock held by a dead run.**

```
Error: Error acquiring the state lock
  Error message: Local state file / .../terraform.tfstate is locked by process
  with ID 12345 on this system.
```

`terraform force-unlock <LOCK_ID>` releases it. Get the lock ID from the error
message (or from the `.tflock`/DynamoDB row). Only do this when you are certain
the holder is dead - forcing the unlock while a real apply is running corrupts
state. If the backend is remote, the equivalent is deleting the lock object in
the bucket (with a conditional `If-Match`, not a blind delete) or the DynamoDB
item; prefer `force-unlock`.

**State is newer than your config thinks / serial went backwards.**

```
Error: Failed to write state: state serial number decreased
```

Something wrote an older state. Recover the newest good copy from bucket
versioning: `aws s3 cp s3://bucket/prefix/key?versionId=... recovered.json`,
then `terraform state push recovered.json` after reconciling. Verify the backup's
`serial` and `lineage` fields before pushing.

**Two people applied at once / state looks half-written.** Restore the previous
version from bucket versioning, then `terraform plan` to see what the world
actually is. Never hand-repair the JSON.

**The whole state is gone and there is no backup.** You must re-import every
resource, or write minimal config with the real IDs and `import` each one. This
is why state versioning is non-negotiable, and why a pre-apply backup hook is
cheap insurance.

## Gotchas

- **State contains plaintext for every input value**, including variables you
  never output. A DB root password passed as a `variable` is in state. Use a
  secrets manager lookup (`aws_secretsmanager_secret_version` /
  `data.aws_secretsmanager_secret_version`) so the reference is in state, not
  the value.
- **`terraform state rm` does not destroy anything.** It removes the resource
  from state, leaving real infrastructure you can now only reach by hand. Use it
  only for objects that are genuinely already deleted, or that you are
  deliberately orphaning (after `terraform refresh` confirms gone).
- **Removing a resource from config does not remove it from state** until you
  `plan` and `apply`, and `plan` will show it as a delete. If you genuinely want
  to keep the object but stop managing it, remove it from config and run
  `terraform state rm` for that address.
- **Backend changes need `init -reconfigure`**; without it Terraform keeps the
  previously initialised backend and you will write state to the wrong place.
- **`terraform state list` output is what the plan addresses refer to.** Pipe
  it to `grep` rather than eyeballing a plan.
- **State is not a backup of your data.** It records resource attributes, not
  contents. A database's tables are not in it.
- **Renaming a resource's `name` attribute is not a move** - it is a
  replacement. Only the *address* can be moved.
- **`moved` blocks are applied even for addresses that no longer exist** in the
  target config, which is fine; they are inert. But a `moved` block pointing at
  a typo silently does nothing, so verify the plan.
- **Partial state reads are not a thing**: `state mv` and `state rm` are
  whole-address operations, and `state push` is whole-file. The only
  sub-state operation is `-target` on a plan, which does not help here.
- **`terraform import` requires the provider's importer**, and many resources
  support import by several ID formats. If import fails, the resource's docs
  page lists the accepted formats under "Import".
- **Different `terraform` major/minor versions can write incompatible state
  serial formats.** Pin the CLI version in CI the same way you pin the provider.

## Safety notes

- Never run `terraform state push`, `force-unlock`, `rm`, or `state mv` against
  a production backend without a named human approving the exact command and a
  verified backup within the last hour. Draft the command, show the plan impact,
  and let the human run it.
- `terraform apply -auto-approve` is never acceptable on a state-writing run in
  production. See `terraform-plan-and-apply`.
- Take a backup before every state operation, and keep the last three.
- If a state operation is the only way to fix something, the fix is probably
  wrong. Re-examine whether the real problem is a module boundary, a provider
  bug, or a missing `moved` block.

## Files

- `references/state-commands.md` - `terraform state` / `workspace` / `import`
  command table with flags, plus the state JSON shape and backend
  configuration comparison across S3, GCS, Azure Blob, and Terraform Cloud.

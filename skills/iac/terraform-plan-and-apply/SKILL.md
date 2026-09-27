---
name: terraform-plan-and-apply
description: Run Terraform plans and applies safely - the plan -out / apply saved-planfile workflow, how to read a diff for destroy/create and force-new signals, why -target is a trap, and how to review a plan like a code review. Use when a user pastes a plan, when an apply needs approval, when someone proposes -target or -auto-approve, or when a plan is bigger than expected.
compatibility: Terraform 1.x. `terraform show -json tfplan` and the plan JSON schema are stable enough to script against; check the schema fields you rely on with `terraform show -json | jq 'keys'` on your version.
metadata:
  version: "1.0"
---

# Terraform Plan and Apply

A plan is a *claim about the future*. An apply without a reviewed plan is an
unreviewed, irreversible change to production. The workflow below is the
default for anything that is not a personal sandbox.

## The default workflow

Run these in order. Do not improvise the sequence.

```sh
# 1. init (only when the module/provider config changed)
terraform init

# 2. format + validate, before planning anything
terraform fmt -check -recursive
terraform validate

# 3. plan to a FILE. Never apply an unsaved plan in production.
terraform plan -out=tfplan

# 4. review the human-readable diff
terraform show -no-color tfplan

# 5. optionally dump the machine-readable form for assertions
terraform show -json tfplan > tfplan.json

# 6. apply exactly that file
terraform apply tfplan
```

**Why the planfile matters.** `terraform apply` (no argument) re-plans and
applies a *fresh* plan. Between your review and the apply, the world moved:
someone enabled a feature flag, a data source returned a new value, an instance
family changed. `apply tfplan` applies exactly the diff you read, and it fails
loudly rather than silently doing something else.

- Saved planfiles contain the full state, including secrets in plaintext.
  Treat `tfplan` like a credential. Delete it after the apply, and never
  commit it.
- A planfile is only valid for the state it was made against and the same
  working directory. Regenerating state invalidates it.
- `terraform plan -detailed-exitcode` is for CI: `0` = no changes, `2` = changes
  present, `1` = error. Do not conflate `2` with failure.

**Never** `terraform apply -auto-approve` against a shared or production
state. If an automation genuinely needs unattended apply, put it in a pipeline
where the plan artifact is reviewed by a human gate first, the state backend is
remote, and a pre-apply state backup is taken automatically.

### In CI (the shape to copy)

```sh
set -euo pipefail
terraform init -input=false -no-color
terraform fmt -check -recursive
terraform validate -no-color
terraform plan -input=false -lock-timeout=5m -out=tfplan -no-color | tee plan.txt
terraform show -json tfplan > tfplan.json
# -> publish plan.txt / tfplan.json as build artifacts for a human to review
# -> apply stage, only after approval, only with the same tfplan
terraform apply -input=false -lock-timeout=5m tfplan
```

`-lock-timeout` matters more than it looks: in CI two runs can collide, and
without it the second fails instantly with
`Error acquiring the state lock`. With it, Terraform waits. `5m` is a
sensible ceiling. Also snapshot state before applying
(`terraform state pull > pre-apply.json`) so a bad apply is recoverable.

## Reading the plan: what actually destroys things

Two things in a plan mean "this will be replaced":

1. **The action** shown in the diff header. `+ create`, `- destroy`,
   `~ update in-place`, `-/+ replace`. Terraform prints
   `must be replaced` or `# forces replacement`.
2. **The reason**, printed on the attribute line that triggers it:

```
  ~ resource "aws_instance" "web" {
      ~ ebs_block_device {
          ~ volume_size = 100 -> 200 # forces replacement
        }
    }
```

`# forces replacement` is the single most important string in Terraform output.
`volume_size` on an EBS device forces replacement, so that "resize the disk"
PR will destroy the instance. So will changing most `aws_launch_template`
settings, `aws_db_instance.engine_version` in some cases, and changing an
`aws_instance`'s `subnet_id` or `ami`.

Read the plan in this order:

1. **Bottom summary**: "Plan: 2 to add, 11 to change, 43 to destroy." Compare to
   the last apply's summary. A suddenly large number is a stop signal.
2. **Scan for `-/+ replace` and `# forces replacement`.** Count them. Every one
   is a potential outage or data risk.
3. **Look for resources moving state** - addresses changing, or a resource in
   the destroy list that also appears in the create list. That is a rename or
   a `moved` block, not a real change.
4. **Check anything with a data source feeding it**: a changed data source
   output can cascade into a change you did not write.
5. **Read the actual attribute diffs** for anything with data impact
   (`db_identifier`, `bucket`, `domain_name`, `security_groups`).

Full decision table in `references/reading-a-plan.md` - what each action
notation means, which attributes commonly force replacement, and how to
decompose a huge plan into safe batches.

### The five questions to answer before approving

- [ ] What is the destroy count, and is every destroyed object replaceable
      (stateless) or does it hold data/state?
- [ ] Any `# forces replacement`? Is the blast radius understood (downtime,
      data loss, a new public IP, a new name)?
- [ ] Any resource disappearing entirely that should not? (removed from
      config but not intended to be deleted)
- [ ] Any secret or IP or DNS name changing that other systems depend on?
- [ ] Is this plan from the same commit SHA as the code under review, using the
      same provider version lock file?

## `-target`: know why it is dangerous before reaching for it

`-target` tells Terraform to plan only the selected addresses. Terraform
disables the usual dependency ordering for anything outside the selection, which
produces a class of plan that would never happen in a real apply.

**The main danger: dependencies are ignored.** Targeting a resource whose inputs
come from unselected resources can plan a change with a value that is wrong
today, or a replacement that was supposed to happen together with its
dependency.

**The second danger: it under-reports.** A targeted plan shows fewer changes.
A reviewer reading it thinks the blast radius is small. It is not.

**The third: partial applies leave state inconsistent.** Applying a targeted
subset writes state for those resources while their dependencies keep their
old values; the next full plan can be a large surprise.

Legitimate uses (rare, and all of them are "rescue" operations):

- Recreating a single instance that Terraform's state lost track of, after a
  failed apply or a manual delete.
- Applying a module change incrementally when a huge plan is risky, **but** only
  if the split is along real dependency boundaries (module by module), and the
  remaining work is still applied afterwards, un-targeted.
- Debugging a dependency cycle.

Rules if you use it:

- Never in CI, and never in a reviewed change.
- Print and record the full command in the PR or incident notes.
- Immediately after, run a full `terraform plan` and confirm the remaining
  changes are what you expected. A `-target` apply is not finished until the
  un-targeted plan is empty or the rest is applied.
- Do not chain `-target` flags to approximate "all of module X" - the correct
  form for that is a dependency-aware split via workspace/module boundaries, or
  simply a smaller change.

Related: `-refresh-only` (separate plan mode, see `terraform-drift-detection`),
`-replace=ADDRESS` to force a one-time replacement of exactly one resource
without changing config, and `-refresh=false` to plan without updating state
from the cloud - all of which have their own footguns documented there.

## Reviewing a plan like a code review

The plan artifact is a review artifact. Generate it in the same pipeline run
as the code diff and attach both.

1. **Diff against the last reviewed plan**, not against "nothing". Two PRs that
   each change three resources but together replace a database are a problem
   that only a plan-to-plan comparison shows. `terraform show -json` output is
   diffable; store previous plan JSON and `diff` them.
2. **Check the `resource_changes` programmatically** for the things humans miss:

```sh
# every address that will be replaced or destroyed
jq -r '.resource_changes[]
  | select(.change.actions | index("delete"))
  | .address' tfplan.json

# every "forces replacement" trigger
jq -r '.resource_changes[]
  | select(.change.replace_paths | length > 0)
  | "\(.address): \(.change.replace_paths | flatten | join(","))"' tfplan.json

# a one-line summary suitable for a PR comment
jq -r '[.resource_changes[].change.actions | join(",")]
       | group_by(.) | map({(.[0]): length}) | add' tfplan.json
```

The plan JSON has `resource_changes[].change.actions` (an array:
`["delete","create"]` for a replacement), `replace_paths` (the attributes that
force replacement), `before`/`after`, and `after_unknown` for values not known
until apply. If your Terraform version's schema differs, inspect it with
`terraform show -json tfplan | jq '.resource_changes[0].change | keys'`
rather than guessing field names.

3. **Check for the things a diff cannot show**: does this change anything a
   human will notice (endpoints, DNS, public IPs, bucket names)? Does it
   require a coordinated deploy of application code in the same window? Is
   there a rollback?
4. **Split the change if the blast radius is unclear.** Smaller PRs, smaller
   plans, reviewable diffs. A plan that touches 200 resources because a single
   map variable changed is a design signal, not a review problem.

## When a plan is unexpectedly huge

Diagnose before applying. In order:

1. **A variable changed shape.** A `tags` map, a list length, or a nested
   object that now serialises differently can rewrite every resource that
   includes it. `terraform show -json tfplan.json | jq` the `before`/`after` of
   one resource and diff them.
2. **A provider upgrade.** A new minor can change defaults or force
   replacement. Check `CHANGELOG.md` for the version in `.terraform.lock.hcl`
   between the last apply and now.
3. **A schema change on a provider alias or module version bump** in
   `required_providers` / module `source` ref.
4. **Resources genuinely being replaced** because an attribute forces
   replacement and the value crossed a threshold. The `# forces replacement`
   comment tells you which.
5. **Drift** - things changed outside Terraform and the plan is reconciling
   them. Use `-refresh-only` to see drift alone, separately
   (`terraform-drift-detection`).
6. **State was lost or partially moved**, so Terraform plans to re-create
   things that exist. `terraform state list` versus the resource list in the
   plan will show the gap. This is a state incident, not a plan problem
   (`terraform-state-management`).

Do not apply a plan you do not understand to make the diff go away.

## Gotchas

- **`apply` without a planfile re-plans.** If the plan output is huge, the
  re-plan can differ and the apply can do something you never saw. Use
  `-out` + `apply <file>`.
- **`-out` planfiles contain secrets** (all variable values, all resource
  attributes). They are not shareable outside the trust boundary; delete them
  after use.
- **`terraform destroy` is a plan and an apply of a giant delete.** Prefer
  removing the resource from config and reviewing the resulting destroy plan,
  or use `terraform destroy -target=<addr>` for a single object when a full
  destroy is definitely not what you want.
- **`terraform plan -destroy`** previews destruction without doing it. Use it
  to see what a `destroy` would really do before running one.
- **`-refresh=false` plans against stale state** and will happily propose
  changes that a refresh would cancel. It is for debugging, not for applying.
  (More in `terraform-drift-detection`.)
- **A plan that says "No changes" is not always right**: it reflects the
  refreshed state, but it will not catch drift Terraform does not manage, and
  it will not catch drift inside `ignore_changes` blocks. `ignore_changes` is
  the mechanism by which drift hides - audit for it.
- **Terraform can only guarantee "no changes" atomically if nothing else writes
  state concurrently.** That is what the backend lock and `-lock-timeout` are
  for; in CI always set it.
- **`data` sources are read during plan and can fail a plan** with a transient
  cloud error. That is a plan failure, not a config failure; re-run. If it
  recurs, the dependency is on something unstable - move it out of plan.
- **`prevent_destroy` on a `lifecycle` block** turns a destroy into a hard plan
  error. Use it for stateful resources (databases, buckets with data) as a
  seatbelt; remove it deliberately when you mean to destroy.
- **`create_before_destroy` changes replacement order** and, on some resources,
  means the old and new exist simultaneously - which can conflict on a
  unique name (a bucket name, a hosted zone). Terraform handles it with a
  generated temp name, but budget for the extra resource.
- **`-parallelism`** (default 10) controls concurrent API calls. Lower it if you
  are hitting provider or API rate limits; raise it cautiously for very large
  applies. It does not change the plan.

## Safety notes

- Treat `terraform apply` against a shared/production backend as a production
  change: reviewed plan artifact, human approval, state backup, and a written
  rollback.
- For any resource holding data (databases, volumes, buckets), the default is
  a `moved`/no-op, not a replacement. If the plan says replace, that needs an
  explicit, documented decision and a backup.
- Do not run `terraform destroy` in response to "the plan is too big". Fix the
  plan or split the change.
- Keep a pre-apply `terraform state pull` snapshot for any apply with a
  non-zero destroy count, and know the restore command before you start.

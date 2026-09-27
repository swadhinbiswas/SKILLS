---
name: terraform-drift-detection
description: Detect and resolve Terraform drift - run `terraform plan -refresh-only` on a schedule, read drift in the plan JSON, taint vs untaint after 0.15, and reconcile out-of-band changes instead of fighting them. Use when a plan shows unexpected changes nobody wrote, when a console edit or an external tool changed infrastructure, when someone asks "why is Terraform trying to change things we did not touch", or when setting up drift alerts.
compatibility: Terraform 1.x. `taint` is a standalone command (0.15+); `terraform untaint` is its inverse. `plan -refresh-only` and `-drift-detection` are 1.x features; verify flag availability with `terraform plan -help`.
metadata:
  version: "1.0"
---

# Terraform Drift Detection

Drift is the gap between what Terraform believes and what the provider returns
on refresh. Something changed the real world outside of Terraform - a console
edit, an autoscaler, a failover, a script, a well-meaning engineer in a hurry.

**The default stance: drift is a signal, not noise.** Each drifted resource
tells you either (a) a human changed something Terraform does not know about -
fix the process, or (b) an automated system is intentionally the owner - make
that explicit in config, or (c) something changed outside any system - an
incident. Which one you have determines the fix, so diagnose before you
reconcile.

## Workflow

1. Refresh and see drift only: `terraform plan -refresh-only -no-color`
2. If non-empty, get it in JSON: `terraform show -json <planfile> > drift.json`
3. Classify each drifted address (below).
4. Fix the cause, not the symptom - then reconcile in the direction that makes
   the real world authoritative.
5. Re-run `terraform plan` (normal) and confirm it is empty or is only the
   intended change.

## Detecting drift

```sh
# drift only: compare refreshed real-world state to the existing state
terraform plan -refresh-only -out=drift.tfplan
terraform show -no-color drift.tfplan

# in JSON, drifted addresses live in resource_drift (not resource_changes)
terraform show -json drift.tfplan | jq -r '.resource_drift[] | .address'

# read-only, safe to run from a role with only read + state-read permissions
terraform plan -refresh-only -lock-timeout=5m
```

- `-refresh-only` **never proposes config changes**. It only proposes updating
  state to match the real world. That makes it safe to run on a schedule
  against production: it cannot destroy anything.
- A refresh-only plan still needs the backend lock (it writes state) and
  provider read permissions. Run it from a read-only CI role, not the deploy
  role.
- Terraform 1.x also has a **drift detection** feature invoked with
  `plan -drift-detection` (added after 1.9; check `terraform plan -help` for
  your version). It reports drift as a warning during a *normal* plan rather
  than producing a separate plan. Prefer `-refresh-only` for a scheduled
  detector because it has a stable, documented output shape.
- The refresh itself is where a broken or unreachable API surfaces:
  `Error: Failed to refresh state: ... Unable to connect to the remote API` -
  that is a connectivity/permission problem, not drift. Do not apply
  anything after a failed refresh; the plan is invalid.

### If you cannot run a refresh-only plan

`terraform plan -refresh=false` skips the refresh entirely. **This is the
opposite of what you want for drift detection** - it plans against the state as
it was last written, so it cannot see drift at all, and the resulting plan can
contain changes that a refresh would have cancelled. Use `-refresh=false` only
to isolate "what would config alone change", for example when diagnosing a
dependency cycle or a provider bug. It is a debugging switch, not a
drift-detection switch.

## Taint: marking a resource for replacement

Taint says "pretend the real object is not there, so the next plan proposes
recreating it".

```sh
# 0.15+: taint is its own command; the old plan/apply -taint flag is gone
terraform taint 'module.db.aws_instance.writer[0]'
terraform untaint 'module.db.aws_instance.writer[0]'
```

- The **pre-0.15 form** was `terraform plan -taint=addr` / `terraform apply
  -taint=addr` (and `terraform refresh -taint=`). Scripts written for the old
  CLI fail with
  `Error: The -taint option is ambiguous` / `flag provided but not defined:
  -taint` on modern Terraform. If you see that, use `terraform taint`.
- `terraform taint` marks state; it does not touch the cloud. The next normal
  `plan` shows that address as `-/+ destroy and then create replacement`, and
  the apply actually does it.
- `taint` a **module address** to taint everything inside it
  (`terraform taint module.db`); addresses are listed in the output.
- Taint an **instance** (`aws_instance.writer[0]`), not the resource type.
- Taint is **not a plan option you can set in CI** for a future apply; it is
  state you must have set before planning. It is an operational tool.
- **Every taint is a destroy.** For a stateful resource it means data loss
  unless the provider recreates from a backup or the resource is stateless
  (a node, a Lambda, an ephemeral disk). Confirm the object is disposable
  before tainting, and record why.

### When to taint vs not

| Situation | Do |
|---|---|
| Real object was manually deleted; state still has it | `terraform state rm` (object already gone) - **not** taint. Taint would plan a destroy of something that is not there, which then recreates, which is also fine but noisier |
| Real object is corrupt/partially configured and must be replaced | `terraform taint <instance>` |
| A whole module is suspect after a bad upgrade | `terraform taint module.x`, plan, review the destroy list, apply |
| You want the plan to *propose* a replacement but have not decided | Use `terraform plan -replace=module.db.aws_instance.writer[0]` (plan-time, needs no state change) instead of tainting |

`-replace=ADDRESS` is the gentler tool: it tells the plan to model a
replacement for exactly that address without modifying state, so nothing is
tainted if you change your mind. Prefer it in reviews; use `taint` for
operational recovery.

## Reconciling drift: pick the direction deliberately

Drift reconciliation has exactly two outcomes, and choosing wrong is how you
lose data.

**Direction A - Terraform is authoritative.** Apply a normal plan to overwrite
the manual change. Use when the manual change was an accident, or when it
violates a standard. Check for `ignore_changes` first, because it will suppress
exactly the correction you want.

**Direction B - reality is authoritative.** Update config (or import the new
reality) so it matches, and do not overwrite. Use when a human or an external
system made a deliberate, correct change.

Decide per resource, not per run. A single drifted security group can contain
one accidental change and one deliberate emergency change.

Procedure for Direction A:

```sh
terraform plan -out=reconcile.tfplan
terraform show -no-color reconcile.tfplan   # confirm ONLY the drift is being reverted
terraform apply reconcile.tfplan
```

Procedure for Direction B:

```sh
# 1. read the real values into state first, so the plan is honest
terraform plan -refresh-only -out=adopt.tfplan && terraform apply adopt.tfplan

# 2. now change the config to match reality
#    (edit the .tf files, or use an `import` block if the object is new to Terraform)

# 3. confirm the normal plan is now empty
terraform plan
```

Doing step 1 before editing config is the important habit: it stops you
writing a config value that is a guess when the real value was available.

**Where a change should be automated, it must live in Terraform.** If a
legitimate operator keeps making the same emergency change (opening a security
group, scaling out), that is a missing module with a documented input - build
it, then have them run `terraform apply -target=module.x`. If it happens
repeatedly, they will keep using the console, and you will keep fighting drift.

## ignore_changes: the mechanism that hides drift

```hcl
resource "aws_security_group" "bastion" {
  lifecycle {
    ignore_changes = [ingress]      # Terraform stops reconciling ingress entirely
  }
}
```

- `ignore_changes` means the attribute is tracked in state but **never
  reconciled**. A manual change to an ignored attribute produces no diff, ever -
  drift detection is blind there. A manual change to a *non*-ignored attribute
  produces a diff, and a normal plan will revert it.
- `ignore_changes = all` is almost always a bug. It means Terraform manages
  creation and nothing else.
- `ignore_changes` is a legitimate, narrow tool: attributes a provider
  defaults or mutates on its own and that Terraform cannot pin (some
  auto-scaling `desired_capacity`, some `default_tags` reflections), or
  attributes a scanning tool owns. Document every use with a comment naming the
  owner. Audit for it (`grep -rn ignore_changes`) as part of drift reviews.
- Note: an ignored attribute still appears in the plan output as
  `~ ingress = [...] # ignored`; it is shown, not applied.

## Scheduling drift detection

Run it on a schedule (nightly per root module is the default) and alert on the
address list, not on the plan text.

```sh
#!/usr/bin/env bash
set -euo pipefail
export TF_IN_AUTOMATION=1

terraform plan -refresh-only -lock-timeout=5m -detailed-exitcode -out=drift.tfplan -no-color
code=$?
terraform show -json drift.tfplan > drift.json
addresses=$(jq -r '.resource_drift[]?.address' drift.json)
if [ -n "$addresses" ]; then
  printf 'DRIFT DETECTED:\n%s\n' "$addresses"
  # -> page/Slack/file a ticket. Do NOT auto-apply.
fi
exit 0
```

- `-detailed-exitcode`: `0` no drift, `2` drift present, `1` error. Treat `1`
  as a real alert (the refresh failed, which is its own problem).
- Alert on a **list of addresses**, so a reviewer can triage in seconds. A
  pasted plan dump trains people to ignore the alert.
- Do **not** auto-apply. An automated "Terraform fixes drift" loop will
  cheerfully revert a legitimate emergency change made ten minutes ago.
- Prefer a read-only role for this job. It needs provider read + state
  read/write (refresh writes state). If you cannot grant that, run
  `plan -refresh-only -out` in an ephemeral workspace and discard the state.
- `TF_IN_AUTOMATION=1` makes Terraform non-interactive; without it a plan that
  needs input hangs the job instead of failing.

## Diagnosing the cause of a drift

| Drifted attribute | Usual cause | Fix |
|---|---|---|
| A security group rule | Console edit during an incident | Direction A (revert) + break-glass runbook using `-target` |
| Instance count / `desired_capacity` | Autoscaler or scaling policy acting on a resource Terraform also manages | Decide the owner: let the autoscaler own it and `ignore_changes` it, or remove the autoscaler. Two owners = permanent drift |
| Tags | A compliance/tagging tool, or a bucket-level tag propagation feature | Let the tool own it with `ignore_changes` on the root tags only, or configure the tool to skip Terraform-managed resources |
| A serverless function's code/config | A CI pipeline deploying outside Terraform (e.g. `aws lambda update-function-code`) | The pipeline must call `terraform apply`; otherwise it is a second source of truth |
| A database's minor version | Provider-side automated maintenance (minor version upgrades) | Usually expected; keep the drift alert, learn to recognise it, and if noisy pin the behaviour with an explicit setting |
| Everything in a module, after a failed apply | A partial apply left real and state out of sync | `state pull`, compare, repair with `moved`/`import` (`terraform-state-management`) |
| Drift that appears only sometimes | Multiple Terraform root modules or workspaces managing the same object | Find the second writer. This is the most serious cause; two root modules managing one object will always conflict |

## Gotchas

- **`terraform plan` (normal) silently reverts drift as part of its diff.** If
  you want to *see* drift without proposing to undo it, use `-refresh-only`.
  Confusing the two leads to "why does my plan want to change the thing I
  changed in the console" - which is the correct behaviour, just surprising.
- **A refresh-only plan writes state**, so it takes the lock. A nightly
  detector racing with a deploy produces
  `Error acquiring the state lock` for whichever loses. Set `-lock-timeout`.
- **`terraform state list` shows state, not reality.** After drift, state and
  the cloud disagree and `state list` lies about the current world. Only
  `terraform refresh` (or a plan) updates that view.
- **`terraform refresh` alone is deprecated in spirit** - it is a state-writing
  operation with no plan review. Use `plan -refresh-only` so there is an
  artifact, and then decide whether to keep the refreshed state.
- **Taint survives in state.** A tainted address stays tainted until an apply
  replaces it or you `untaint` it. A tainted address in a plan you did not
  expect is a leftover from a previous incident; check `terraform state list`
  semantics and untaint if the object is fine.
- **Tainting a resource inside a `for_each` module must use the instance
  address** (`module.x.aws_instance.web["prod"][0]`), and if the key set is
  changing the taint will not survive the plan. Prefer `-replace=` for that
  case.
- **Data sources can also "drift"** in the sense that a data source result
  changes; that shows as a `data` resource change, not `resource_drift`. It is
  not drift - it is the plan noticing a changed input.
- **Failures to refresh hide drift.** If the provider cannot read a resource
  (deleted out of band, permissions changed), the plan errors rather than
  showing a delete. A resource deleted behind Terraform's back usually shows up
  as a *create* in a normal plan, not a delete - that asymmetry surprises
  people.
- **`resource_drift` in the plan JSON is only populated for refresh-only
  runs** (and for `-drift-detection` runs). Do not `jq .resource_drift` on a
  normal plan and conclude there is no drift.
- **Drift caused by a provider-side default change** (a new default in a
  provider upgrade) looks identical to human drift. Check the provider CHANGELOG
  before assuming a person edited it.
- **Ignoring drift is not the same as disabling the detector.** `ignore_changes`
  silences the plan for that attribute but the resource is still worth watching
  in the alert, because drift *outside* the ignored attribute is real.
- **Two root modules managing overlapping resources is not fixable with
  `ignore_changes`.** Find the second writer. This includes a module used
  directly by one root and as a submodule of another.

## Safety notes

- Never auto-apply a drift correction. Surface the addresses and let a human
  decide the direction (A or B) - a machine cannot know whether a manual change
  was an accident or a deliberate act.
- Before applying a reconciliation that closes a security group rule, firewall
  rule, or auth permission, confirm you are not locking yourself (or everyone)
  out. Read the rule's direction and source.
- Before tainting anything, confirm the object is disposable or that a backup
  exists and the restore path is known. Taint + apply is a destroy.
- If drift is discovered on stateful resources (databases, buckets), reconcile
  in Direction B (adopt reality) unless you have a verified backup. Overwriting
  a real database's configuration back to a stale value is a data incident.

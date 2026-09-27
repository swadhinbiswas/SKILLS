# Reading a Terraform plan reference

What the plan notation means, what forces replacement, and how to decompose a
large plan. Companion to `terraform-plan-and-apply`.

## Action notation

Terraform prints one symbol per resource, from a two-character legend:

```
  + create
  ~ update in-place
- - destroy
+/- replace (destroy then create)
+~ create then update (new resource whose attributes are also computed)
~+/- replace, then update
```

The two-character form is read as a sequence: `+/-` = destroy then create,
`+~` = create then update. In the JSON plan (`terraform show -json`),
`resource_changes[].change.actions` is an array in the same order
(`["delete","create"]`, `["create"]`, `["update"]`).

Some real headers:

```
  ~ update in-place              # an attribute changed, object edited in place
-/+ destroy and then create replacement   # something forces replacement
+ -/+ create and then replace            # destroy before it was ever created (precondition failure mid-create)
  ~ update in-place              # a meta-argument (count/for_each/provisioner) changed the shape
  < / > read / no-op             # data source refresh
```

`terraform plan` is itself the legend: the first block of any plan output
includes the "Note: You didn't use the -out option" reminder and the
"+ create / ~ update in-place / - destroy" summary.

## `# forces replacement`

When Terraform prints an attribute diff with this comment, that attribute
cannot be changed in place; the object must be destroyed and recreated, and
Terraform is telling you it knows.

```
  ~ resource "aws_instance" "web" {
      ~ root_block_device {
          ~ volume_size = 20 -> 50 # forces replacement
        }
        tags = { "env" = "prod" }
    }
```

If the plan is a full destroy-and-recreate, this comment is why. Common
replacement triggers (provider-specific, verify per resource in the provider
docs' "Updating" section, which lists them explicitly):

| Resource | Attribute that commonly forces replacement |
|---|---|
| `aws_instance` | `ami`, `subnet_id`, `availability_zone`, `instance_type`, most `root_block_device` fields, `user_data_replace_on_change` interactions, `ipv6_address_count` |
| `aws_db_instance` / `aws_rds_cluster` | many, including `engine` and `engine_version` depending on version path, `availability_zone`, `db_name`, `manage_master_user_password` toggles |
| `aws_s3_bucket` | almost nothing now that versioning/separation is per-sub-resource; the `aws_s3_bucket` "bucket" itself forces replacement on rename only |
| `aws_security_group` | `name`, `vpc_id` (tags and rules update in place) |
| `aws_lb` / `aws_lb_target_group` | `name`, `vpc_id`, `load_balancer_type` (attributes like `idle_timeout` update in place) |
| `google_compute_instance` | `zone`, `machine_type`, `boot_disk.initialize_params.image` (some update in place) |
| `azurerm_linux_virtual_machine` | `location`, `vm_size`, `network_interface` shape, `admin_username` |

**Rule of thumb:** an attribute "forces replacement" when the cloud API cannot
modify it on an existing object. Provider docs mark every one of them. When a
plan's destroy count surprises you, look for this comment first; if it is not
present for a destroy, the cause is a `create_before_destroy` dependency
destroy, a resource moving address, or a `count`/`for_each` change.

## Detecting drift, moves, and cascade in a plan

Signals to grep for in `terraform show tfplan`:

- **Rename or move**: the same logical object appears as a destroy of address A
  and a create of address B. In the JSON, a `moved` block shows up as a
  `previous_address` on the resource change. Pure addresses moving with no
  attribute diffs is a `moved` block working, not a change.
- **Cascade from a data source**: many unrelated resources change together and
  a data source is in the plan. Read the data source's planned value change -
  that is the root cause.
- **Cascade from a variable**: a `locals` expression feeding many resources
  changed. Diff the `before`/`after` of one leaf to find the field.
- **`ignore_changes` masking a replacement**: if a resource is in an
  `ignore_changes` list for an attribute that forces replacement, the plan will
  silently not replace it while the cloud drifts. Rare and confusing; audit
  `ignore_changes` when a resource refuses to update.

## Decomposing a large plan

When a plan touches hundreds of resources, find the small set of causes before
splitting anything:

1. Dump JSON and list every address that will be replaced or destroyed:
   `jq -r '.resource_changes[] | select(.change.actions | index("delete")) | .address' tfplan.json`
2. Group by module and by type. One changed variable inside a module used by
   `for_each` over 20 items will show up as 20 modules' worth of changes.
3. Group by `replace_paths` - the attributes responsible. Two or three
   distinct `replace_paths` values across 200 resources means 2-3 real changes.
4. For each cluster, decide: is it a no-op, a safe in-place update, a
   replaceable stateless object, or a stateful object that must be handled
   separately (backed up, migrated, `moved` instead of replaced)?
5. Split the apply along those clusters only if they have no cross-cluster
   dependencies. Verify by applying cluster 1, then a full un-targeted plan, and
   confirming the remaining diff is exactly the other clusters.

## Replacement versus in-place: when to fight for in-place

If a plan replaces a stateful resource for a benign-looking reason, look for
the less destructive way to express the same change:

| Goal | Plan says replace | In-place alternative |
|---|---|---|
| Grow an EBS volume | `volume_size` on `root_block_device` | Separate `aws_ebs_volume` + `volume_attachment`; grow, then `aws_volume_modification` online |
| Change tags | Nothing (tags always update in place) | - |
| Change instance type | `# forces replacement` | Accept it, or use a launch template + ASG so scaling handles it |
| Rename a resource | Replacement | Add a `moved` block, or keep the name and change only a tag |
| Change security group rules | In place | - |

The general fix for "it wants to replace something big": the change is being
expressed against the wrong resource boundary. Splitting a root volume into a
managed `aws_ebs_volume`, or moving instances into an ASG, is the pattern that
makes these operations in-place.

## JSON plan fields worth scripting

`terraform show -json tfplan`:

- `format_version` - plan JSON schema version; pin your jq against this if you
  automate.
- `terraform_version` - the CLI that made the plan.
- `planned_values` - the post-apply world (what exists after, including
  not-yet-known values marked as such).
- `resource_changes[].address`, `.module_address`, `.mode`
  (`"managed"` or `"data"`), `.type`, `.name`
- `resource_changes[].change.actions`, `.before`, `.after`, `.after_unknown`,
  `.replace_paths`, `.previous_address`
- `output_changes` - the root outputs and whether they will change (useful for
  surfacing "the API endpoint changes" in review).
- `resource_drift` - **only present in a refresh-only or drift-detecting run**;
  the resources that changed outside Terraform. This is your drift signal in
  JSON form.
- `prior_state` - the state as it was before the plan.

Useful jq one-liners:

```sh
# action histogram
jq -r '[.resource_changes[].change.actions | join("+")]
       | group_by(.) | map({action: .[0], n: length}) | .[]' tfplan.json

# everything that forces replacement, with the reason attribute
jq -r '.resource_changes[]
  | select(.change.replace_paths | length > 0)
  | "\(.address)  <- \(.change.replace_paths | flatten | join(", "))"' tfplan.json

# only root-module (non-module) destroys, the highest-risk set
jq -r '.resource_changes[]
  | select(.module_address == null)
  | select(.change.actions | index("delete"))
  | .address' tfplan.json

# outputs that will change (often the "user-visible" summary of a plan)
jq -r '.output_changes | to_entries[] | select(.value.change) | .key' tfplan.json
```

## `prevent_destroy` and `create_before_destroy`

```hcl
resource "aws_db_instance" "main" {
  lifecycle {
    prevent_destroy = true
  }
}
```

- `prevent_destroy` makes any plan that would destroy this address **fail**
  (`Plan ... is protected by lifecycle.prevent_destroy`). Use it on databases,
  stateful queues, and object stores holding data. It also blocks `terraform
  destroy` of the whole root, so document how to remove it deliberately.
- `create_before_destroy = true` inverts replacement order to avoid a gap in
  the name/identifier. It requires the resource to support it (Terraform errors
  with
  `Root resource was present, but now absent` / provider-declared
  `create_before_destroy` conflicts if the provider does not allow it), and it
  means the old and new exist at once, which costs money and can conflict on
  unique names.
- Both are `lifecycle` settings: they apply to a resource or module and are
  part of the config, reviewed like anything else.

---
name: terraform-module-design
description: Design reusable Terraform modules - typed and validated variables, the count vs for_each index-keying gotcha, module composition and `for_each` at the call site, required_version and provider constraints, locals vs outputs, and the anti-patterns (no outputs, god-module, providers threaded through five layers). Use when writing or refactoring a shared module, when someone asks to "make this reusable" or "DRY up our Terraform", or when a plan is churning because a resource moved index.
compatibility: Terraform 1.x. Examples use the 1.6-1.9 CLI (check_for_object_consistency, moved blocks, optional object attributes). Verify newer syntax with terraform validate.
metadata:
  version: "1.0"
---

# Terraform Module Design

A module's interface is its `variables` and `outputs`. Everything else is
implementation. Design the interface first, write the body second, and let
`terraform test` prove it (see `infrastructure-testing` for the test layers).

## Workflow

- [ ] 1. Name the *capability*, not the resources ("`s3_bucket_with_lifecycle`"
      is a resource list, "`object_store_bucket`" is a capability)
- [ ] 2. Pick the boundary: what the caller must know, and what they must not
- [ ] 3. Write `variables.tf` - type, `description`, `validation`, sensible
      `default`
- [ ] 4. Write the body in `main.tf`; put lookups in `data.tf`, pure
      computations in `locals.tf`, names/tags in `versions.tf`-adjacent
      conventions
- [ ] 5. Write `outputs.tf` - every attribute a caller will need, and nothing
      that leaks provider internals
- [ ] 6. Prove it: `terraform init -backend=false && terraform validate`, then
      `terraform test`
- [ ] 7. Write the README with a copy-paste `main.tf` example for each variant

## Variables: type, validate, default

Type everything. `variable "x" {}` with no `type` is untyped and fails at apply
time with a confusing message.

```hcl
variable "environment" {
  type        = string
  description = "Deployment environment. Used in resource names and tags."

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "replicas" {
  type        = number
  description = "Worker count per availability zone."
  default     = 2

  validation {
    condition     = var.replicas >= 1 && var.replicas <= 20
    error_message = "replicas must be between 1 and 20."
  }
}
```

Rules that survive review:

- **Validations may only reference the variable they are attached to** (plus
  `var` itself). If a validation needs another variable, move the check into a
  `precondition` on a `lifecycle` block or a `check` block.
- **Defaults change behaviour for every caller that omits the variable.** Once
  the module is published, a new default is a breaking change for silent
  callers. Add the variable without a default and mark it `nullable` if you need
  "explicitly no value".
- **Use `optional()` in object-typed variables for 1.3+**, with
  `default = {}` semantics you can compute against:
  ```hcl
  variable "tags" {
    type = map(string)
    default = {}
  }
  ```
  Prefer this over a nullable variable plus a `coalesce`.
- **Secrets are inputs but must not be outputs.** Take `db_password` as
  `variable "db_password" { type = string, sensitive = true }`, use it in
  `random_password` or a secrets manager, and never echo it. If the caller needs
  to know the value came from a secret store, output the *ARN*, not the value.

Cross-variable checks go in `check` blocks (1.5+) or preconditions:

```hcl
check "either_tls_or_private" {
  assert {
    condition     = var.certificate_arn != null || var.private == true
    error_message = "A public listener needs certificate_arn; otherwise set private = true."
  }
}
```

## count vs for_each: the index-keying gotcha

This is the single most common cause of "terraform wants to destroy and
recreate everything" in modules. **Both attach a meta-argument to the *address*
of a resource, and the address is a string.**

| Meta-argument | Key type | Index | Safe to add/remove items? |
|---|---|---|---|
| none | - | `aws_instance.web[0]`, `aws_instance.web["a"]` | Yes |
| `count` | number | `aws_instance.web[0]`, `aws_instance.web[2]` | **No** - removing item 0 renumbers 1 -> 0, so state mismatches |
| `for_each` | string or set of strings | `aws_instance.web["a"]` | Yes - removal leaves the others untouched |
| `count` + `for_each` | both | `aws_instance.web["a"][0]` | Only meaningful inside a module over a set |

`for_each` over a set of strings gives a per-element index, which is almost
always what "one resource per item" means:

```hcl
variable "subnets" {
  type        = set(string)
  description = "Subnet IDs. One service task per subnet."
}

resource "aws_ecs_task_definition" "svc" {
  count = length(var.subnets)      # 0, 1, 2 ...
}

resource "aws_lb_target_group" "svc" {
  count   = length(var.subnets)
  name    = "${var.name}-${count.index}"
  vpc_id  = var.vpc_id
  port    = var.port
}
```

**The zero-vs-one object gotcha.** `for_each` over a *map/object* keys by map
key, so a single-element map yields `aws_x.foo["only"]`; `count = 1` yields
`aws_x.foo[0]`. Code written for one breaks under the other, and the error
points at an attribute lookup, not at the meta-argument:

```
│ Error: Invalid index
│   on main.tf line 12, in resource "aws_lb_target_group" "svc":
│    12:     vpc_id = each.value.vpc_id
│ The "given" key is not a valid key: "only" is not a valid ... 
```

More often you hit it via a computed list:

```hcl
# module receives a list of objects
variable "nodes" { type = list(object({ id = string, cidr = string })) }

resource "aws_subnet" "n" {
  for_each = { for n in var.nodes : n.id => n }   # key = stable id, not index
  vpc_id            = var.vpc_id
  cidr_block        = each.value.cidr
}
```

Build the map with a **stable key** (`n.id`, the subnet name, the AZ) and the
zero-vs-one problem disappears, because the address no longer depends on
position. **Default to keyed `for_each` maps. Reach for `count` only when the
count is a single number and you are deliberately fine with renumbering**
(typically a list of identical, self-contained things with no cross-references
between instances).

Use `-target`-free, `moved` blocks for renumbering (see
`terraform-state-management`).

## Composition

Compose modules rather than nesting resources deeper. Two directions:

**Hierarchical (module calls module).** Keep it shallow - the god-module is
usually three levels deep because someone kept inlining. Rules:

- A child module must declare the provider resources it uses with an
  `alias`; it must not call `provider` blocks itself.
- Pass only what the child needs. Passing a whole `var.app` object into every
  module and unpacking it inside is coupling, not reuse.
- A module with more than ~15 resources, or more than ~2 nested `module` calls,
  should be split.

**Flat (root wires the graph).** For tight coupling where resources share
lifecycles, keep them in the root and use `locals` to stitch values together.
Two root modules calling a shared "vpc" module is fine and often better than
one module that owns the vpc *and* everything in it.

**`for_each` over modules at the call site** is the pattern people forget:

```hcl
module "queue" {
  source   = "./modules/queue"
  for_each = var.environments          # map(env_name => settings)
  name     = each.key
  settings = each.value
}
```

Keys are the environment names, so adding `prod` is a non-destructive diff.
The module's own resources then live at `module.queue["prod"].aws_sqs_queue.this`.

**`depends_on` is for edges HCL cannot express**, not for ordering. Adding it
to a whole module to fix a race serialises everything it contains and hides the
real dependency. Name the real edge; if you cannot name it, the modules are too
coupled.

## Versions and providers

```hcl
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"          # patch/minor only, within major 5
    }
  }
}
```

- `~> 5.60` allows `>= 5.60, < 6.0`. `~> 5.60.0` additionally pins the minor.
  Pick the loosest constraint that still protects you from a breaking major.
- Modules should declare `required_version` and `required_providers` but
  **should not** declare a `backend`. Backends are the root module's business.
- Do not put `provider "aws" { ... }` in a shared module. Provider config
  belongs at the root; pass an `alias` when a module needs a second one.
- Pin the provider in the **root** module's lock file (`.terraform.lock.hcl`).
  Commit it. A module cannot lock anything.

## Locals and outputs

`locals` are for naming and derivation, not for control flow. A local that
recomputes the same expression in three places is a smell; a local named
`name_prefix` used a hundred times is exactly right.

```hcl
locals {
  name_prefix = "${var.org}-${var.app}-${var.environment}"
  common_tags = merge(var.tags, {
    ManagedBy = "terraform"
    Module    = "api"
  })
  is_prod     = var.environment == "prod"
}
```

**Outputs are the contract.** Rules:

- Every output that a caller could plausibly need, with a `description`.
- No output that only exists to get a value back into the same root module -
  calculate it in the root.
- Do not output whole resource objects just to expose one field; that couples
  the caller to your provider schema and breaks on upgrade.
- `sensitive = true` on outputs whose value derives from a secret. Terraform
  propagates the flag, but a sensitive output is redacted in plan output *and*
  in `terraform output` - which is how it leaks, via a human copying it.
- `nullable = true` (1.1+) on an output that may be `null`, so callers can use
  `try()` and get `null` rather than an error.
- `ephemeral = true` (1.10+) for values that must not be persisted in state at
  all - session tokens, credentials, plaintext secrets. Verify your Terraform
  version supports it (`terraform version`) before relying on it.

## Anti-patterns

| Anti-pattern | What goes wrong | Fix |
|---|---|---|
| **Module with no outputs** | Callers reach into `module.x.aws_thing.y` directly, or use data sources to re-read what the module already knows | Every module outputs its primary identifiers (id, arn, endpoint) |
| **God-module** (`infra/`) | One module owns VPC, subnets, buckets, databases, IAM. Nobody can change a subnet without owning the org | Split by capability: `network/vpc`, `data/aurora_cluster`, `edge/alb` |
| **Provider threaded everywhere** | `providers = { aws = aws.usw2 }` on every module call; renaming a provider is a 200-file diff | One default provider, plus named aliases only where a region/account genuinely differs |
| **`var.app` mega-object** | One typed object input with 30 fields; the module silently ignores most, and the type is unmaintainable | Discrete variables, or a typed object only where the fields are genuinely cohesive |
| **No `description` on variables** | `terraform-docs` output is unreadable, reviewers guess, the registry page is useless | Every variable and output gets one sentence |
| **Hard-coded names** | Two instances in one account collide | Derive all names from a `name`/`name_prefix` variable |
| **`map(any)` / `list(any)`** | Type errors surface at apply, and `for_each` over `any` may not be a valid key set | Concrete object types |
| **Secrets in variables** | Plaintext lands in state, and state is often less protected than the DB | `sensitive` + a secrets manager reference (SSM/Secrets Manager) resolved at apply |

## Gotchas

- **A `default` on a variable in a published module is a silent behaviour
  change for every caller who omits it.** Bump the module major version or
  rename the variable.
- **`for_each` over a `set(string)` is fine; `for_each` over a `list` is an
  error** - it is ambiguous, and Terraform says so
  (`Invalid for_each argument ... set of string required`). A `set` of objects
  is also rejected: build a map first.
- **`for_each` keys must be known at plan time.** A key derived from a resource
  attribute that is only known after apply produces
  `Invalid for_each argument ... depends on resource attributes that cannot be
  determined until apply`. Use `count` there, or restructure.
- **`variable "tags"` defaulting to `{}` and then `merge(var.tags, {...})` is
  fine; `merge()` with a `null` argument errors.** Guard with `coalesce`.
- **`nullable = true` + `default = null` is different from omitting the
  variable**: `null` means "explicitly no value" and lets the module's own
  `coalesce`/default logic take over. Understand which one you need.
- **`required_providers` in a module with a bare `aws` alias-only resource
  causes an implicit empty default provider block**, which is fine, but adding
  `provider "aws" { region = ... }` inside a shared module breaks every caller
  that needs a second region.
- **Renaming a module or moving a resource between modules is a destroy/create
  unless you add a `moved` block.** `moved` blocks work across module boundaries
  and are inert once applied. See `terraform-state-management`.
- **`check` blocks warn; they do not fail a plan.** Put hard failures in
  variable `validation` or a `precondition`.
- **`terraform plan` on a fresh checkout fails with `Module not installed`**;
  `terraform init -backend=false` validates without touching a backend.
- **A module that reads the current workspace** (`terraform.workspace`) is not
  portable and breaks the `terraform test` harness. Return a value from a data
  source or take it as an input.
- **`depends_on` on a module applies to every resource in it**, so it is
  almost never the right granularity. Name the actual producer-consumer edge.
- **`count = 0` is a valid state**: Terraform records the resource as having
  zero instances. Destroying and recreating across that boundary is fine, but
  writing `aws_x.y[0]` without guarding fails hard when count is 0.
- **Module registries require a Git tag for each version** (`v1.2.3`), and
  `source = "git::https://...?ref=v1.2.3"` is the only way to pin one.

## Checklist before publishing a module

- [ ] `terraform init -backend=false && terraform validate` passes
- [ ] `terraform fmt -check -recursive` clean
- [ ] Every variable: type, `description`, `default` or explicit `required`
- [ ] Every input validated for the mistakes users actually make
- [ ] Outputs: id/arn/endpoint, described, no secret values
- [ ] Addressed resources use `for_each` with stable keys, not `count`
- [ ] `required_version` and `required_providers` pinned
- [ ] No `provider` block, no `backend` block, no hard-coded account ids
- [ ] README has a complete `main.tf` example per supported variant
- [ ] `terraform test` has at least one `assert` per output

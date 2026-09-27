---
name: infrastructure-testing
description: Test infrastructure-as-code with Terraform's native test framework, Terratest assertions against real cloud APIs, plan-based checks, Checkov/tfsec/OPA policy-as-code, and sandbox drift tests - so a module is verified to do what its README claims before it reaches production. Use when writing or reviewing a module, when a plan is too large to review by eye, when setting up pre-commit or CI policy gates, or when someone asks how to prove IaC changes are safe.
compatibility: Terraform 1.6+ for `terraform test` (mock providers 1.7+). Terratest v0.4x (Go). Checkov (Python) and tfsec (Go/standalone). Confirm exact flags with `--help` for your installed versions.
metadata:
  version: "1.0"
---

# Infrastructure Testing

Two things break IaC: a plan nobody read, and a module that does not do what
its variables suggest. Testing addresses both, at different layers.

Layer them, cheapest first:

| Layer | Catches | Cost | When to run |
|---|---|---|---|
| `fmt` / `validate` | Syntax, typos in types, missing attributes | seconds | Every save |
| Static policy (Checkov/tfsec/OPA) | Security misconfigurations, policy violations | seconds | Every commit |
| `terraform test` (unit, mocked) | Does the module's *code* produce the right plan? | ~1 min, no cloud | Every PR |
| Plan assertions on a real sandbox | Does the config *apply*? | minutes, real money | Every PR (nightly at minimum) |
| Terratest | Does the *behaviour* match the claim? (API calls, ingress works) | minutes, real money | Every PR for high-traffic modules |
| Post-apply drift check | Does state match reality, and does the plan stay empty? | minutes | Nightly, per environment |

A module without at least the first three is unproven, and a module without the
last is unverified in production.

## Workflow

- [ ] 1. `terraform fmt -check -recursive` and `terraform validate`
- [ ] 2. Static policy scan (`checkov -d .` / `tfsec .`) - fix or annotate
- [ ] 3. `terraform test` - unit-level plan assertions with mocked providers
- [ ] 4. Apply into a sandbox account, assert real behaviour (Terratest or a
      script)
- [ ] 5. Confirm the post-apply plan is empty and a second apply is a no-op
- [ ] 6. Gate CI on 1-4; run 5 nightly

## `terraform test`: unit tests for infrastructure

Terraform 1.6+ ships a native test framework. Tests are `.tftest.hcl` files
next to the module; `.tfmock.hcl` files provide mocked provider responses
(1.7+) so a test runs with no cloud account and no credentials.

```hcl
# modules/bucket/tests/default.tftest.hcl
variables {
  bucket_name = "acme-test-bucket"
  versioning  = true
}

run "plan_is_valid" {
  command = plan

  assert {
    condition     = aws_s3_bucket.this.bucket == "acme-test-bucket"
    error_message = "bucket name was not honoured"
  }

  assert {
    condition     = aws_s3_bucket_versioning.this.versioning_configuration[0].status == "Enabled"
    error_message = "versioning must be enabled when the input is true"
  }
}

run "versioning_can_be_disabled" {
  command = plan
  variables {
    versioning = false
  }
  assert {
    condition     = aws_s3_bucket_versioning.this.versioning_configuration[0].status == "Suspended"
    error_message = "disabling versioning should suspend, not remove, it"
  }
}

run "rejects_empty_bucket_name" {
  command = plan
  variables {
    bucket_name = ""
  }
  expect_failures = [var.bucket_name]   # the validation must fire
}
```

```sh
terraform init
terraform test                     # all tests in the module
terraform test -filter=tests/bucket.tftest.hcl
terraform test -verbose
```

- **`command = plan`** (default) asserts on the *plan*, not the apply - fast,
  free, and enough for most structural claims. Use `command = apply` only when
  the assertion needs a real value or a data source that only resolves against
  live infrastructure.
- **`expect_failures = [var.x]`** asserts a variable `validation` or a
  `precondition` rejected the input. This is how you test your own guardrails;
  without it, validations are untested code.
- **`assert { condition = ... }`** is evaluated against the module's
  **resources and data sources in that run block's plan**, so assert on
  `aws_x.this.attr`, not on `output.name` unless the output exists.
- **Mocks** (1.7+): a `.tfmock.hcl` next to the test overrides provider behaviour
  (computed attribute values, data source results). Useful for asserting on a
  value that is only known at apply (ARNs, generated ids) without applying
  anything.
- `terraform test` still needs `terraform init` in the **module** directory and
  **will not** work for a root module with a real backend configured - point it
  at a module or use `-backend=false` init in a temp working dir.
- Tests are per-directory; the conventional layout is
  `modules/<name>/tests/*.tftest.hcl`.

## Terratest: assert the behaviour, not the shape

Terratest is a Go library that runs `terraform apply` and then makes **real API
calls** to verify the result. It is the right tool for "does the load balancer
actually serve traffic", "is the security group actually not open to the
world", "can a user log in with the generated password".

```go
package test

import (
	"testing"

	"github.com/gruntwork-io/terratest/modules/random"
	"github.com/gruntwork-io/terratest/modules/terraform"
	"github.com/gruntwork-io/terratest/modules/aws"
	"github.com/stretchr/testify/require"
)

func TestBucketIsPrivateAndVersioned(t *testing.T) {
	t.Parallel()

	opts := terraform.WithDefaultRetryableErrors(t, &terraform.Options{
		TerraformDir: "../bucket",
		Vars:         map[string]interface{}{ "bucket_name": random.UniqueId() },
		EnvVars:      map[string]string{"AWS_REGION": "eu-west-1"},
	})

	terraform.InitAndApply(t, opts)
	terraform.AssertResourceExists(t, "aws_s3_bucket.this", opts)

	bucket := aws.GetS3Bucket(t, "acme-test-bucket")
	require.Equal(t, true, bucket.Versioning != nil &&
		*bucket.Versioning.Status == aws.BucketVersioningStatusEnabled,
		"versioning must be enabled")
	require.Nil(t, bucket.PublicAccessBlockConfiguration,
		"bucket must have a public access block")
}

func TestDestroyCleansUp(t *testing.T) {
	t.Parallel()
	opts := &terraform.Options{TerraformDir: "../bucket", Vars: map[string]interface{}{}}
	defer terraform.Destroy(t, opts)
	terraform.InitAndApply(t, opts)
	// destroy is deferred; a leak shows up as a non-empty account
}
```

Practical rules:

- **Every test must have a `defer terraform.Destroy` (or `DestroyE`)**. A test
  that leaks a NAT gateway or an RDS instance costs real money and will be
  noticed by the bill, not by the test run.
- **Generate unique names per test run** (`random.UniqueId()`) so parallel
  tests (`t.Parallel()`) do not collide on globally-unique names (buckets,
  hosted zones).
- **Run against a dedicated sandbox account** with a hard budget alarm. Never
  against a production account.
- **Assert on the API, not on Terraform's output.** Reading a value back
  through the provider's own API is the point; asserting that an output equals
  what you passed in proves nothing.
- Test the **negative** too: a security group is *not* open to `0.0.0.0/0`; a
  function is *not* publicly invokable. Those are the claims that break silently.
- `terraform.InitAndApplyE` returns an error instead of failing the test
  immediately - use it when you need to assert on a failure case (e.g. that
  apply fails when a required variable is missing).

## Policy-as-code: static gates

`fmt` and `validate` cannot tell you the security group is open to the world.
Scanners can, in seconds, on every commit.

```sh
# Checkov (Python; also scans Terraform plan JSON, CloudFormation, K8s, etc.)
pip install checkov
checkov -d modules/bucket --quiet --compact
checkov -d . --framework terraform_plan -f plan.json   # scan a real plan, not just HCL

# tfsec (Go binary; checkov's rules overlap heavily, pick one)
tfsec modules/bucket --no-colour

# OPA / Conftest for policy that is yours, not a vendor's list
conftest test modules/bucket -p policy/
```

Put the gate in pre-commit **and** CI. Pre-commit only covers people who cloned
the repo and installed the hook; CI covers everyone.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/bridgecrewio/checkov
    rev: <pin a version>
    hooks:
      - id: checkov
        args: ["-d", ".", "--quiet", "--compact"]
  - repo: local
    hooks:
      - id: terraform-validate
        name: terraform validate
        entry: sh -c 'terraform init -backend=false -input=false && terraform validate'
        language: system
        pass_filenames: false
```

**Handle findings explicitly, do not blanket-suppress.** A scanner will always
have findings that are wrong for your context. For each, either fix it or add a
narrow, commented skip that names the exception:

```hcl
resource "aws_security_group" "db" {
  # checkov:CKV_AWS_24 - RDS SG must not allow ingress from 0.0.0.0/0.
  # Exception: the DB is inside a private subnet with no route to the internet;
  # verified by tests/infrastructure/assert_no_public_db.sh.
  ingress { from_port = 5432 ... }
}
```

Never `# noqa`/`skip` the whole file, and never turn a rule off globally to make
CI green. A suppressed finding with a reason and an owner is a decision; a
blanket suppression is a hole. Review the skip list periodically - it grows.

Scanners read **HCL**, not intent, so they have systematic blind spots: they
cannot see a value that comes from a module variable, a `for_each` over a map
from a remote data source, or a resource built in a loop in a template. That
is why scanning the **plan JSON** (`checkov -f plan.json`) is strictly better -
it is post-expansion, post-variable-resolution, and catches exactly those
cases. If your CI already produces a plan artifact, scan that.

## Plan-based assertions (the cheapest useful test)

You do not need Terratest to catch the class of bug that hurts most: a plan
that destroys or replaces something it should not. Assert on the plan JSON.

```sh
terraform plan -out=tfplan -lock-timeout=5m
terraform show -json tfplan > tfplan.json

# no unexpected destroys
unexpected=$(jq -r '[.resource_changes[]
  | select(.change.actions | index("delete"))
  | select(.module_address == null)     # root-level only
  | .address] | join(" ")' tfplan.json)
[ -z "$unexpected" ] || { echo "unexpected destroy: $unexpected"; exit 1; }

# nothing forced to replace
jq -e '[.resource_changes[] | select(.change.replace_paths | length > 0)] | length == 0' \
   tfplan.json >/dev/null || { echo "forced replacement detected"; exit 1; }
```

For a PR gate, the strongest cheap version is **plan-to-plan**: store the plan
JSON from the default branch and assert that this PR's plan is a superset of
the change the author intended. Concretely: fail if the destroy set is larger
than the "before" plan's, and print the address-level diff of actions so a
reviewer sees only what changed. Store `tfplan.json` as a CI artifact either
way - it is the review artifact.

## Sandbox drift: prove the post-apply world is stable

The cheapest high-value integration test: apply in a sandbox, then check that a
second run is a no-op and that the real world matches state.

```sh
terraform apply -auto-approve tfplan   # sandbox account only
# 1. second apply must be a no-op
terraform plan -detailed-exitcode       # expect 0
# 2. state must match reality: refresh-only must be empty
terraform plan -refresh-only -detailed-exitcode   # expect 0
```

Exit code `2` on either check means the configuration is not stable - a
provider default is not being pinned, or something is mutating the resources
between runs. Both are real findings and both show up in production as
perpetual drift. This test catches classes that no unit test can: an
auto-scaling group's `desired_capacity` drifting, a default tag being
recomputed, a setting the provider does not actually pin.

Run the whole thing on a schedule per environment, not just in CI: a nightly
"apply then plan" on prod state catches drift nobody caused deliberately.

## Testing Ansible roles

IaC testing means Ansible too. The equivalents:

- `ansible-lint` + `yamllint` for style and the most common correctness traps.
- `ansible-playbook --syntax-check` then `--check --diff` - catches the
  playbook's own typos without a target.
- `molecule test` - converge a role into a throwaway instance, then a second
  converge must be a no-op (idempotence), then `verify.yml` asserts the real
  state through fact gathering and a direct check. Details in
  `ansible-automation`.
- Idempotence is the test that matters most for Ansible, and it is the one most
  roles fail. A role that has never been converged twice is unverified.

## Choosing what to test, per module

| Module type | Minimum bar |
|---|---|
| Leaf resource wrapper | `terraform test` plan assertions for each variable branch; one static policy scan |
| Composite (IAM, networking) | Above + a Terratest that asserts the *security* property (not open to the world, route works) |
| Stateful (DB, bucket) | Above + destroy-leak test + post-apply drift test |
| Anything with a data source | A test where the data source is mocked (1.7+) so the test is deterministic; a Terratest that exercises it against real infrastructure |
| A root module / environment | The sandbox apply, the second-apply no-op, and a nightly refresh-only drift check |

## Gotchas

- **`terraform test` needs no backend but does need `init`** in the module
  dir; running it from a root module with a configured backend fails. Use a
  module directory or `init -backend=false`.
- **Mocked providers (1.7+) can mask real provider behaviour** - a mock returns
  what you told it. Mocks are for asserting on values Terraform could not know
  without an apply; they are not proof the apply works. Pair them with a
  Terratest or a sandbox run.
- **Terratest leaks are the norm, not the exception.** A panic mid-test skips
  the deferred `Destroy` sometimes. Run tests against a sandbox with a budget
  alarm and a nightly sweep for leaked resources (`aws resourcegroupstaggingapi
  get-resources` / a tag-based cleanup Lambda) so failures are not permanent
  cost.
- **Unique-name generators collide across parallel packages** if they are not
  globally unique (e.g. an RDS identifier's length limit). Keep generated names
  short and salted with the test name.
- **Checkov/tfsec rules change between versions**; pin the version, or a
  provider upgrade will add a rule, fail CI on an unrelated change, and get
  "fixed" with a blanket skip. Pin and review new rules deliberately.
- **Scanning plan JSON is stricter than scanning HCL** and will report things
  the HCL scan cannot see. If you switch, expect a burst of new findings; that
  burst is the point.
- **An `expect_failures` test that stops failing because the validation was
  removed is a silent loss** - keep the negative tests; they are the only
  coverage your guardrails have.
- **A check that asserts on Terraform *outputs* is usually a tautology.** Assert
  against a provider API, a fact, or a file's content on the target.
- **A nightly drift test on prod that auto-reconciles is a bad idea** (see
  `terraform-drift-detection`); it should alert, not fix.
- **Sandbox accounts need their own budget alarm and a tag-based sweeper**,
  because the tests will eventually fail halfway and leave things running.

## Safety notes

- Never point Terratest, Molecule, or any test harness at a production account.
  Require an explicit account-id assertion at the top of the test run and fail
  if it matches production.
- Every test that creates anything destroys it, with a
  `defer`/`terraform.Destroy` and a belt-and-braces sweeper. Cost and orphans
  are the failure mode of a broken test run, not of a broken application.
- Do not let a test write to a shared state backend. Give the harness a
  per-run state key and destroy the whole run's state.
- Treat policy suppressions as code: reviewed in the same PR as the change,
  each with a reason and an owner.

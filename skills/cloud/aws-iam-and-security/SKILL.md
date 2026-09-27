---
name: aws-iam-and-security
description: Design AWS IAM that is least-privilege and reviewable - roles over users, explicit deny and policy evaluation order, permission boundaries vs SCPs vs identity policies, OIDC role assumption for CI, and a safe way to derive a minimal policy instead of guessing `Action: "*"`. Use when a task is "the pipeline can't access X", when an AccessDenied needs decoding, when reviewing an IAM policy or trust policy, or when someone proposes wildcard permissions.
compatibility: AWS IAM, current as of 2026. Service-prefixed actions and role/session features evolve; verify the exact action strings against the service's IAM service authorization reference before writing a policy.
metadata:
  version: "1.0"
---

# AWS IAM and Security

IAM is a policy engine, not a permissions list. Almost every confusing IAM
problem is a wrong mental model of how it evaluates. Get the model right and
most policies are easy to reason about; get it wrong and you will add
`*` until something works, which is the exact failure mode this skill exists to
prevent.

Full evaluation procedure and the decode table: `references/iam-evaluation.md`.
Read it when you are debugging an `AccessDenied`.

## The model, in five lines

1. An identity (user, role, or root) plus a resource plus a context (which
   service, which condition keys, source IP, MFA, session tags).
2. **Identity-based policies** (attached to the identity) grant permissions.
3. **Resource-based policies** (attached to the resource) also grant, *and*
   can deny. This is why an S3 bucket policy or a KMS key policy can grant
   someone access you never attached an IAM policy for.
4. **Permission boundaries** cap what an identity can ever do (the intersection
   of "allowed by boundary" and "allowed by identity policy").
5. **SCP**s (and AWS Organizations policies) cap what an account or OU can ever
   do, applied to the identity's *request context*, and an explicit deny in an
   SCP wins over everything. **An SCP that does not `Allow` the action denies
   it** - the default is deny, not allow.

The non-obvious consequences, which are most of the practical knowledge:

- The union of identity and resource policies is the grant; a deny in
  **either** wins. There is no "most permissive wins" across policy types.
- **The principal does not have a policy; the role's session does.** `Role`
  (resource policy) and the identity policy are both required for
  `AssumeRole` to succeed. A role with a permissive trust policy and no
  identity policy still cannot be assumed usefully.
- `NotAction` and `NotResource` mean "everything *except*", which is an
  **allow** of a very large set. In a policy that also has an `Allow`, a
  `NotAction` allow is nearly always a bug.
- Permissions boundaries do **not** grant anything. They only remove. An
  identity with a boundary and no identity policy can do nothing.
- Resource policies are evaluated for the *resource*, so a cross-account
  access question is a question about both the role's identity policy **and**
  the resource's policy (and, for KMS, the key policy, which for a
  customer-managed key is the dominant one).

## Roles, not users

**Default: no IAM users for anything that runs on a machine or in a pipeline.**
An IAM user with an access key is a static password that leaks through
`.env` files, CI logs, and laptop backups, and it does not expire.

| Identity | Use for |
|---|---|
| Role (assumed via OIDC, instance profile, or STS) | Everything automated: CI/CD, EC2/ECS/EKS/Lambda, cross-account |
| IAM user | A human doing one-off CLI work, only, with MFA and no keys if the org supports it |
| Root | Never. Never use the root access key in automation; the account is a single point of catastrophic failure |

### OIDC role assumption: the default for CI

Give the CI provider a role it can assume via OIDC, scoped by
`sub` (which repo/branch/pipeline) and `aud`. No static secret exists.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": { "token.actions.githubusercontent.com:aud": "sts.amazonaws.com" },
      "StringLike":  { "token.actions.githubusercontent.com:sub": "repo:acme/payments:ref:refs/heads/main" }
    }
  }]
}
```

- The **`aud`** must be the STS audience the provider requests (`sts.amazonaws.com`
  for GitHub Actions, `sts.amazonaws.com` for GitLab by default, the provider's
  own value for others). Wrong `aud` gives a confusing
  `Could not retrieve verification key` / `InvalidIdentityToken`.
- The **`sub`** is the security boundary, and its format differs per provider:
  GitHub is `repo:owner/repo:ref:refs/heads/main` for a branch,
  `repo:owner/repo:pull_request` for a PR (so PRs from forks get
  `ref:refs/pull/N/merge` - decide deliberately whether forks can deploy);
  GitLab is `project_path:group/project:ref_type:branch:ref:main`. Match on the
  most specific form you need; do not default to `"*"` because it is the only
  form everyone copies.
- `token.actions.githubusercontent.com:sub` is case-insensitive in GitHub, so
  `StringLike` with a `repo:...` prefix is fine, but do not rely on case for
  the branch name beyond the prefix.
- Keep **two roles**: a deploy role for the default branch and a read-only plan
  role for pull requests. The plan role cannot write, so a compromised PR cannot
  change infrastructure.
- Enable **GitHub's "Require OIDC" / branch protection** and turn off
  long-lived secrets, or the OIDC path is decorative.

### Cross-account, without keys

Use a role in the target account whose trust policy names the *source* role's
ARN, and grant the source role `sts:AssumeRole` on it. Chain: instance profile
role -> role with `sts:AssumeRole` on the other account's role -> target
permissions. Assume the chain explicitly, and prefer the session name to carry
context for CloudTrail:

```sh
aws sts assume-role --role-arn arn:aws:iam::222222222222:role/deploy \
  --role-session-name "ci-${GITHUB_RUN_ID}" --duration-seconds 3600
```

- **Do not use the `Principal: { "AWS": "arn:...:root" }` shorthand** for a
  cross-account trust. `"root"` means "any principal in that account that has
  an explicit allow" - which is *not* "nobody" (a common and costly
  misreading), and it is easy to widen accidentally. Name the specific role.
- Do not trust an account principal plus a `aws:PrincipalArn` condition you
  forgot to write. Name the role directly; conditions are defence in depth.
- `chained role assumption` (role -> role) is a normal, supported pattern.
  Sessions are time-limited, so a leaked assumption expires; leaked keys do not.

## Writing a policy that survives review

**Never** ship `Action: "*"`, `Resource: "*"`, or `NotAction` as a starting
point. They are acceptable in a *deny* statement (deny-all as a guardrail) and
nowhere else in a first draft.

Start from the smallest thing that could work and prove it is too small:

1. **Name the action and the resource.** Use the service's IAM Service
   Authorization Reference table (it lists every action, its resource types,
   and its condition keys). The resource column is the answer to "which ARN?".
2. **Scope the resource.** `"Resource": "arn:aws:s3:::acme-reports/*"` not
   `"*"`. For many APIs you need two statements (one `List*` on the bucket,
   one `Get*` on `bucket/*`) - that is normal, not a sign you need a wildcard.
3. **Scope the action.** If you need read, list the read actions
   explicitly: `s3:GetObject`, `s3:ListBucket`, not `s3:*`.
4. **Add conditions** for the things that are context, not permission:
   `aws:SourceVpce`, `aws:SourceArn`, `aws:PrincipalOrgID`, `aws:RequestedRegion`,
   `s3:prefix`, `aws:TagKeys`, `aws:MultiFactorAuthPresent`. Conditions are how
   you express "only from our VPC endpoint" or "only for this tag" without
   widening the resource.
5. **Deny the things you never want**, explicitly, at the account level, in
   the boundary or an SCP - not scattered through identity policies.

A policy that is *readable* beats one that is *short*. Six explicit statements
with a comment each is better than one `s3:*` on `*`.

### Deriving a minimal policy safely (never guess)

There is a supported way to find the minimal set: **CloudTrail + IAM Access
Analyzer**.

- **CloudTrail data events** for the specific S3 bucket prefix / DynamoDB table /
  SQS queue / Lambda function, for a representative week, aggregated with Athena,
  give you the set of `(principal, action, resource)` actually used. Start from
  that and add the `List*`/describe actions the console and Terraform need.
- **IAM Access Analyzer** (`generate-policy` in the CloudTrail-backed analyzer
  policy generator) proposes a policy from actual CloudTrail data. It is a
  *starting point to review*, not something to paste: it omits actions that were
  not exercised, includes some you do not want, and will not include the
  `List*`/tag actions needed for the plan to succeed.
- **The iterative loop, done safely**: apply a *narrow candidate* policy, run
  the real workload, and read `AccessDenied`. The error names the action and
  often the resource:
  ```
  User: arn:aws:iam::123456789012:user/deployer is not authorized to perform:
  s3:GetObject on resource: arn:aws:s3:::acme-reports/2026/q1/report.csv
  because no identity-based policy allows the s3:GetObject action
  ```
  Add exactly that action on exactly that resource pattern. Never respond to an
  `AccessDenied` by adding `"Action": "*"`.
- **Never** "just test with AdminAccess" on a shared or production account. If
  you must reproduce a failure with admin rights, do it in a throwaway account
  and say so in the change.

### `AccessDenied` in one line

The message tells you the action, the resource, and - crucially - the
*identity* that was evaluated. Common shapes:

- `is not authorized to perform: X on resource: R because no identity-based
  policy allows the X action` - the identity policy is missing `X`. If the
  resource is in another account, the *other* account's resource policy also
  has to allow it.
- `... because no resource-based policy allows the X action` - a resource policy
  exists on the target and it does not allow you. This is the one people
  misread as "my IAM policy is wrong".
- `... because a deny in a resource policy` / `... because an explicit deny in
  an identity policy` - someone (or an SCP/boundary) is denying. Removing the
  allow will not help; find the deny (`iam:SimulatePrincipalPolicy`, and read
  the boundaries and SCPs).
- `is not authorized to perform: sts:AssumeRole` - a **trust policy** problem,
  not a permissions problem. The role's trust policy does not include you, or a
  condition failed.
- `is not authorized to perform: kms:Decrypt because no identity-based policy
  allows the kms:Decrypt action` on an S3 object you *can* `GetObject` - the
  S3 bucket is fine; the **KMS key policy** is the gate for a
  customer-managed key. Grant on the key, in the key policy, to the role.

## Boundaries, SCPs, identity policies: which is which

| Mechanism | Attaches to | Grants? | Caps? | Who manages |
|---|---|---|---|---|
| Identity policy | user/role | Yes | No | You, per principal |
| Resource policy | resource | Yes | Yes (can deny) | Resource owner; for KMS, the key policy is the real authority |
| Permission boundary | user/role | No | Yes (intersection) | Usually a security team, to cap blast radius |
| SCP / Organizations policy | account, OU, or organization | No | Yes (explicit deny wins) | Security team; applies to *every* principal in the account including root |
| Session policy | assumed role session | Yes | No | Passed at `AssumeRole`; intersects with the role's identity policy |
| Resource control policy (RCP) | future (preview in some regions) | No | Yes | Data-plane guardrails, e.g. S3 encryption |

- **Boundaries are for blast radius, not for grant.** Use a boundary on
  high-privilege roles (break-glass, deploy roles, CI) so that even a bug in
  the identity policy cannot exceed a documented ceiling. The boundary must
  allow every action the role legitimately needs or it is a silent breakage
  (`AccessDenied` on something that "worked yesterday").
- **SCPs are the account's floor.** A default SCP full-allow plus your own
  deny rules is the normal shape. When something "works for the admin and not
  for the role", suspect an SCP at the OU. SCPs do not apply to management or
  service-linked roles, and (important) they do not apply to **IAM roles in
  other accounts** unless that account also has the SCP - an SCP in account A
  does not constrain what A can do *to* B.
- **Layering order to remember**: SCP (and RCP) > boundary > session policy /
  identity policy, with resource-policy denies also winning. A grant must
  survive *all* the caps above it.
- A boundary or SCP that omits an action is a **deny**, so the "minimum
  permissions" for a boundary is the *union* of what every legitimate principal
  under it needs - usually computed by starting from CloudTrail usage
  (Access Analyzer) and adding the `List*`/describe actions Terraform needs.

## Other AWS security defaults worth stating

- **Enable MFA delete** on the root user, delete root access keys, and use an
  organisation with SCPs from day one; SCPs cannot be applied retroactively
  with any grace period.
- **S3**: block public access at the account level (`BlockPublicAcls`,
  `IgnorePublicAcls`, `BlockPublicPolicy`, `RestrictPublicBuckets`); require
  SSE-KMS and TLS in a deny SCP; deny `s3:*` without encryption headers if you
  need it.
- **CloudTrail**: management events are on by default everywhere; **data
  events are not** - S3 object-level, Lambda invoke, DynamoDB, and SQS message
  logging are opt-in per resource and cost money. Enable them for anything you
  would need to investigate, and know that the default is "off".
- **GuardDuty** and **Security Hub** are separate and both must be enabled;
  Security Hub aggregates findings but does not detect anything itself.
- **Secrets Manager** for secrets, not environment variables in a task
  definition, and not SSM `SecureString` parameters where a rotation path
  exists. Terraform `sensitive = true` redacts *display*; the value is in state
  in plaintext.
- **IMDSv2** for EC2 instance metadata (`HttpTokens = required`); IMDSv1 is a
  classic SSRF-to-credentials path.
- Encryption: S3 SSE-KMS (control the key), RDS/EBS/DynamoDB at rest, TLS in
  transit enforced in policies (`aws:SecureTransport: false` deny).

## Gotchas

- **An SCP `Allow` is not additive across accounts.** An SCP in account A
  constrains principals *in* A, including A's access to B. It does not constrain
  B.
- **Identity policy + resource policy = union for the grant, but a deny in
  either wins.** A resource policy can grant an access your identity policy does
  not cover - which is why an S3 bucket policy suddenly "works" after you add
  nothing to IAM.
- **A KMS key policy is the primary gate for a customer-managed key**, and the
  key policy cannot be solved by the caller's identity policy alone; the key
  must name the principal. This is the most common `AccessDenied` in
  serverless setups.
- **`Condition` with a `StringLike` and a missing key does not fail closed
  automatically** - a request that does not send the key at all can be treated
  differently from one that sends the wrong value. Use `...IfExists` only when
  you mean "if present"; otherwise use `StringEquals`.
- **Session tags and `aws:PrincipalTag` / `aws:RequestTag` / `aws:TagKeys`**
  are a real least-privilege mechanism: tag the assumed session at
  `AssumeRole` and gate a sensitive action on the tag. It is underused.
- **`sts:TagSession` must be allowed on the role** for session tagging to take
  effect, and the trust must pass it through - a common
  "my condition never matches" cause.
- **A trust policy with `Principal: {"Service": "lambda.amazonaws.com"}` is
  not enough**; the role also needs an identity policy, and for VPC-attached
  Lambdas the `aws:SourceVpc`/`aws:SourceVpce` conditions on the trust policy
  are what prevent confused-deputy access.
- **Passing a role to a service does not pass the role to a resource the service
  then calls.** The service's own role/permissions are what matter (a Lambda
  reading from a stream, an ECS task reading from a queue). Grant on the
  execution role, not the caller.
- **A user with two active access keys is a real pattern and a real risk** -
  AWS caps at two, and `aws iam create-access-key` on a user used by a script
  means a secret is being minted without anyone noticing. Alert on it.
- **IAM policy JSON is case-sensitive and service-prefix-sensitive**:
  `s3:GetObject` not `S3:getobject`, `iam:PassRole` not
  `iam:PassRole` on the wrong casing, and condition keys are lowercase-prefixed
  (`aws:SourceArn`, `s3:prefix`, `secretsmanager:secretId`).
- **An `Allow` with `Resource: "*"` and a `Condition` on `aws:RequestTag` is
  not a least-privilege policy**, it is a tag-scoped one - which is fine and
  sometimes exactly what you want, but be clear about which you have.
- **Revoking a session** is `aws:iam` no -
  `sts:RevokeSession` on the session's access key, or a deny added to the
  principal; an assumed role session cannot be "logged out" and simply times out
  at its maximum duration.
- **Access Analyzer** findings are *potential* external access; a finding on a
  key policy is usually a real one. Set up the analyzer once and treat new
  findings as incidents.

## Safety notes

- Never grant `*` on `*` to "fix" an `AccessDenied` in a change that touches
  production. Narrow the failure, find the exact action and resource, and add
  that.
- Permission changes to a role's own trust or to an SCP are the highest-risk
  edits: a wrong SCP can lock everyone out of the account, and a wrong trust
  policy can expose a role. Apply those with a break-glass path that does not
  depend on the role being changed.
- Do not attach a permission boundary to a role that holds the only path to fix
  things without also having a documented break-glass (a separate, boundary-free
  role, whose use is audited).
- When reducing permissions, do it in a mode that shows the impact first:
  generate CloudTrail-based candidate policies and compare against the current
  one in a simulator (`aws iam simulate-principal-policy` /
  `simulate-custom-policy`) before attaching.

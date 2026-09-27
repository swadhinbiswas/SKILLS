# IAM policy evaluation reference

The exact procedure AWS uses, the messages it produces, and how to walk the
decision tree without guessing. Companion to `aws-iam-and-security`.

## The evaluation model

An evaluated request is a tuple: **(identity, action, resource, context)**.

- **Identity** - the principal: IAM user, assumed role session, or root.
- **Context** - the conditions AWS attaches to this specific request: the
  service principal that made the call, source IP, MFA presence, secure
  transport, source VPC/VPC endpoint, requested region, resource tags, and
  principal/session/request tags.

Evaluation is not one pass. It is a fixed set of rules, in effect:

1. Collect identity-based policies (attached to the user/role) - each may
   Allow, Deny, or neither.
2. Collect resource-based policies (on the target resource) - these may Allow
   **or Deny**.
3. **Any explicit Deny anywhere (identity policy or resource policy) wins.**
4. Determine whether there is an Allow. In the general case, permission is
   granted if **either** an identity policy allows **or** a resource policy
   allows (this is the "union" - it applies within a single account; across
   accounts, the resource policy's `Principal` still has to name you, and the
   union of your identity policy and that resource policy applies).
5. Apply the account-level caps: **SCP / Organizations policy** and (where
   applicable) **resource control policies**. An explicit Deny in an SCP wins
   over everything. An SCP that has no matching Allow for the action denies it.
6. Apply the **permission boundary** (on the user/role): the request is allowed
   only if **both** the boundary allows **and** the identity policy allows.
   A boundary never grants.
7. Apply the **session policy** (passed at `AssumeRole` / federation): also an
   intersection with the role's identity policy. A session policy can narrow;
   it cannot widen the role.
8. **Resource-based policy denies for cross-account access to some services
   (S3, KMS, and others) can be evaluated even when an SCP would allow.** For
   S3 and KMS specifically, the resource policy is authoritative - a permissive
   SCP does not help if the key policy or bucket policy denies you.

## Decision tree

```
Request denied?
├─ Explicit Deny in an SCP / RCP / organization policy?
│    → find it. Adding an Allow anywhere will not help.
│      List candidates: aws organizations list-policies, then the OU the
│      principal is in, then the account's attached policies.
├─ Explicit Deny in an identity policy (including a boundary or session
│  policy Deny)?
│    → grep the principal's policies for "Deny". Note: a boundary Deny and a
│      session policy Deny are also explicit denies.
├─ Explicit Deny in a resource policy (bucket policy, key policy, etc.)?
│    → read the resource's policy. Especially the KMS key policy.
├─ No Allow found?
│    → the missing grant is on the identity side (add an identity policy
│      statement), or, cross-account, the resource policy does not name you
│      (fix the resource policy), or a boundary/SCP does not Allow it
│      (fix the cap).
└─ Allowed, but the service returns an error anyway?
     → a service-specific condition: resource policy condition failed, a
       permission boundary/session policy nuance, an SCP "not attached" at
       the right OU, an Access Analyzer-style public-block, or a KMS grant
       that is missing.
```

## `AccessDenied` messages, decoded

### Identity policy missing the action

```
User: arn:aws:iam::123456789012:role/deploy is not authorized to perform:
s3:GetObject on resource: arn:aws:s3:::acme-reports/2026/report.csv
because no identity-based policy allows the s3:GetObject action
```

Add `s3:GetObject` to an identity policy on the role, on
`arn:aws:s3:::acme-reports/2026/*`. Note the message says *the* action and
*the* resource - the exact one to add. If this is cross-account, the bucket
policy in the owning account must also allow your role.

### The resource policy is the gate

```
User: arn:aws:iam::123456789012:role/deploy is not authorized to perform:
s3:GetObject on resource: arn:aws:s3:::acme-reports/2026/report.csv
because no resource-based policy allows the s3:GetObject action
```

The bucket (or bucket policy) does not grant you. The role's own policy being
correct is irrelevant until the resource policy allows you (cross-account) or
does not deny you (same account, when a resource policy exists it participates
in the union/deny logic).

### An explicit deny

```
... because an explicit deny in an identity policy blocks the s3:GetObject action
... because no identity-based policy allows ... and there is a deny
... Access Denied because the deny policy is attached
```

Something denies. Adding an Allow does nothing. Find the Deny: identity
policies, boundary, session policy, resource policy, or SCP.

### Trust policy failure

```
User: arn:aws:iam::123456789012:role/deploy is not authorized to perform:
sts:AssumeRole on resource: arn:aws:iam::222222222222:role/target
```

This is the **trust policy** on the target role, not the caller's
permissions. Check: is the caller's principal in `Principal`? Is the
`Action` `sts:AssumeRole`? Did a condition fail (source account, source
ARN, external ID, source IP, MFA, session tags)? A `Condition` failure often
produces this same generic message - enable CloudTrail, or test the trust with
`sts:AssumeRole` and read the response's `ErrorMessage`.

An **external ID** mismatch produces:

```
The request failed because the external id ... is invalid
```

The external ID is a `sts:ExternalId` value the target trust policy requires;
it is a confused-deputy defence and is a string the caller must pass exactly.

### KMS

```
User: arn:aws:iam::123456789012:role/lambda is not authorized to perform:
kms:Decrypt on resource: arn:aws:kms:eu-west-1:123456789012:key/abcd-...
because no identity-based policy allows the kms:Decrypt action
```

For a **customer-managed key**, the key policy is the primary gate. Adding
`kms:Decrypt` to the lambda role is necessary but may not be sufficient - the
key policy must also name the role. For an **AWS-managed key** (`aws/*`), only
the identity policy is needed, but the *caller* of the encrypting service also
needs `kms:GenerateDataKey`/`kms:Decrypt` on the key.

### S3, listing versus getting

S3 has two distinct actions and two distinct resource shapes, and mixing them
up is the most common S3 policy bug:

| Operation | Action | Resource |
|---|---|---|
| List objects in a prefix | `s3:ListBucket` (+ `s3:GetBucketLocation` sometimes) | the **bucket** ARN (`arn:aws:s3:::bucket`) |
| Get/Put/Delete an object | `s3:GetObject` / `s3:PutObject` / `s3:DeleteObject` | the **object** ARN (`arn:aws:s3:::bucket/*`) |
| Restrict List to a prefix | condition | `s3:prefix` on `s3:ListBucket` |

A policy with `s3:ListBucket` on `arn:aws:s3:::bucket/*` is wrong (wrong
resource shape) and gets `AccessDenied` on the listing, not on the get.

## Trust policy patterns

### Cross-account, specific role (the default)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "AWS": "arn:aws:iam::111111111111:role/deploy" },
    "Action": "sts:AssumeRole",
    "Condition": { "Bool": { "aws:MultiFactorAuthPresent": "true" } }
  }]
}
```

### Service principal (EC2, Lambda, ECS tasks, a service assuming a role)

```json
{
  "Effect": "Allow",
  "Principal": { "Service": "lambda.amazonaws.com" },
  "Action": "sts:AssumeRole"
}
```

Add conditions that tie the assumption to a specific, non-repudiable property
of the workload - for a VPC-attached Lambda, the ENI's VPC and VPC endpoint
IDs; for a cross-service call, `aws:SourceArn` of the calling resource. These
are what stop a confused deputy.

### OIDC federation (CI)

See the main skill for the GitHub Actions example. The two keys that decide
everything are the `aud` (must match the audience the provider requests -
`sts.amazonaws.com` for GitHub Actions) and the `sub` (the actual security
boundary - match on repo + ref, not `*`).

### Federated user / SAML / web identity

`Action: sts:AssumeRoleWithSAML` or `sts:AssumeRoleWithWebIdentity`, with the
provider ARN as `Principal.Federated`. Web identity needs an OIDC provider ARN
(`arn:aws:iam::ACCT:oidc-provider/host`) in `Principal.Federated`, not a
service principal.

## Conditions worth knowing

| Condition key | Use it for |
|---|---|
| `aws:PrincipalArn`, `aws:PrincipalAccount`, `aws:PrincipalOrgID` | Pinning who/what, so a role name alone is not the security boundary |
| `aws:SourceArn` | "only when called by *this* resource" (an ALB, a specific Lambda, a specific queue) - the confused-deputy defence |
| `aws:SourceVpce`, `aws:SourceVpc` | "only from inside the VPC" / "only through this endpoint" - the most useful condition in private networking |
| `aws:SecureTransport` | Deny anything not over TLS |
| `aws:MultiFactorAuthPresent` | Require MFA for human/break-glass actions |
| `aws:RequestedRegion` | Region scoping (not a security boundary, but a real reduction) |
| `aws:TagKeys`, `aws:RequestTag`, `aws:ResourceTag/tag-key` | Grant on a tag the resource must carry, or on a request/session tag |
| `s3:prefix` | Restrict `s3:ListBucket` to a prefix |
| `kms:EncryptionContext` | Key access only for a specific encryption context - a strong, little-used pattern |
| `lambda:Principal` | Restrict invoke to one function's execution role |

Conditions are *conjunctive* across keys within a block and across blocks
(unless `ForAllValues`/`ForAnyValue` is used). A key that is **absent** from
the request is not a match for a `StringEquals`; that is why
`Null: {"aws:MultiFactorAuthPresent": "false"}` is the correct way to *deny*
when MFA is missing, rather than `Bool: {"aws:MultiFactorAuthPresent": "false"}`
(which reads as "MFA is not present OR unknown").

## Simulator

Use the simulator before attaching a narrowed policy - it shows the effect
without breaking anything:

```sh
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::123456789012:role/deploy \
  --action-names s3:GetObject s3:PutObject \
  --resource-arns arn:aws:s3:::acme-reports/2026/report.csv
```

The output is per-action: `allowed` / `implicitDeny` / `explicitDeny`, with
`evaluationReason` and `matchedStatements` telling you which policy decided.
- `implicitDeny` = nothing allowed it.
- `explicitDeny` = something denied it; the `matchedStatements` will point at
  the denying statement (identity policy, boundary, or - if the resource is in
  the same account and you pass its ARN - a resource policy).

The simulator does **not** evaluate every condition key or every service's
resource-policy nuance, and it does not know about SCPs. Treat a simulator
"allowed" as necessary, not sufficient, and validate in a real call.

## Reviewing a policy: questions to ask

- Is there a statement with `Action: "*"` or `Resource: "*"` that is an
  **Allow**? (Fine in a Deny; never in a first-draft Allow.)
- Is there a `NotAction`/`NotResource` in an Allow? It is an allow of
  everything *else* - almost always a bug.
- Does every action have a matching resource shape (bucket vs `bucket/*` for
  S3, table vs `table/index/*` for DynamoDB, `queue` vs `queue/*`... no, SQS is
  just the queue ARN)?
- Are `List*`/describe/`GetBucketLocation`/`tag:GetResources` actions present
  for the tools that use this policy (Terraform, the console, the SDK)? A
  policy that works for the SDK can still fail the plan.
- Is the trust policy naming a specific principal, with conditions that
  actually bind (not a `Principal: "root"` shorthand with no condition)?
- Is there a KMS key policy in the picture, and does it name this principal?
- Are the conditions fail-closed (a `StringEquals`, not a `...IfExists`, unless
  "absent is fine" is the intent)?
- Would removing this policy break a break-glass path, or a session that is
  currently held open?

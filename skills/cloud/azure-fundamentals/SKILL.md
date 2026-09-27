---
name: azure-fundamentals
description: Azure fundamentals done properly - tenant/subscription/resource-group scoping, Entra ID and managed identity instead of connection strings, RBAC vs access policies vs network ACLs, naming and region conventions, and policy-as-code with Azure Policy. Use when creating Azure infrastructure, when someone is about to use a storage account key or a service principal secret, when "AuthorizationFailed" or "403" needs decoding, or when a user asks about resource groups, subscriptions, or managed identities.
compatibility: Microsoft Azure, current as of 2026. Entra ID was formerly Azure AD; the Microsoft Graph / az CLI / ARM naming has stabilised but verify service-specific roles and limits against current docs.
metadata:
  version: "1.0"
---

# Azure Fundamentals

Azure's model is four nested scopes, three different authorization systems, and
one pervasive habit (using access keys) that is almost always wrong. Get these
straight and Azure is unremarkable; get them wrong and every troubleshooting
session ends in a shared access signature somebody pasted into a config file.

## The scope chain

```
Tenant (Microsoft Entra ID / Azure AD)  - the directory; users, groups, apps
  └── Management group                    - policy and governance across many subscriptions
        └── Subscription                   - the billing + management boundary (one per env, usually)
              └── Resource group           - the deployment/lifecycle boundary (one per workload, per region)
                    └── resources
```

- **Subscription = billing and quota boundary.** One per environment is the
  default that makes cost attribution and blast radius both sane. Quotas and
  some rate limits are per-subscription-per-region, which is why a
  subscription per region is sometimes forced on you.
- **Resource group = lifecycle and RBAC scope.** Everything in a resource
  group shares a lifecycle and can be granted access together, which is
  convenient and also means the scope is coarse: a role on the resource group
  reaches every resource in it. One resource group per workload, per region,
  per environment.
- **Management group** is where Azure Policy, role definitions, and
  configurations propagate from. It is the control-plane for governance; see
  "policy as code" below.
- **Resources are mostly regional, but some are global**: a storage account,
  a front door, a Microsoft Entra tenant-scoped resource. Storage account
  names are **globally unique** across all of Azure and cannot be changed
  after creation; network interface names are unique within a resource group
  and region. These naming constraints drive a lot of naming convention pain.

## Authorization: three separate systems, often conflated

| System | Where it applies | Granularity | Notes |
|---|---|---|---|
| **Azure RBAC** (role-based access control) | Management plane - ARM resources (create, delete, and some read) | Role assignment at any scope | Assign a role to a principal; the roles are built-in or custom |
| **Data plane RBAC** (Storage Blob/Data Lake, Service Bus, Key Vault, etc.) | Data operations (read/write a blob, send a message) | Role assignment scoped to the resource or a container/share | **Separate** from management-plane RBAC. A Storage Blob Data Reader is not a member of "Storage Blob Data Reader" management role |
| **Access policies / keys / SAS / firewall** | Storage accounts, Service Bus, Event Hubs - the legacy auth model | Key or SAS token | Full or scoped access to the whole resource, positional, and it **bypasses RBAC** |

- **The single most common Azure mistake is confusing these.** "I gave the
  managed identity the Reader role but it cannot read the blob" is not a bug:
  the management-plane Reader role does not grant data-plane blob reads. You
  need a **Storage Blob Data Reader/Contributor** role at the storage account,
  container, or storage-account scope.
- **Access policies are the thing to retire.** A storage account access key is
  a full-access, non-expiring, shared, listable secret; access policies attach
  those keys to users. Prefer RBAC + Entra ID authorisation. If access policies
  must exist (legacy), keep them minimal and monitor them.
- **Prefer Entra ID authorisation on storage and disable shared key access**
  where possible (`allowSharedKeyAccess: false` on the storage account). That
  one setting eliminates a whole class of leaked-key incidents and forces
  managed identities everywhere.
- **Prefer Entra ID authorisation on Service Bus / Event Hubs**
  (`minimumTlsVersion`, disable local auth) for the same reason.

## Managed identity: the default, not the option

- **Every supported Azure compute has an identity**: App Service, Functions,
  Container Apps, AKS (via workload identity), VMs (system-assigned or
  user-assigned), Service Fabric. Enable it; do not create an app
  registration secret.
- **System-assigned** identity is tied to one resource's lifecycle: it is
  deleted with the resource, and every resource gets a distinct identity
  (more Entra objects, harder to audit). **User-assigned** identity is a
  standalone resource you create once and attach to several resources - the
  right default when more than one resource needs the same permissions, or when
  the identity must survive a resource replacement.
- **No secret to leak.** A managed identity acquires a token from the Entra
  token endpoint on the instance metadata service; the app code just calls the
  SDK and the token is cached and refreshed for it. A managed identity is
  credentials *without* a secret - but only for code running on the resource.
- **Where the token comes from and the classic error:**

```
ManagedIdentityCredential authentication failed, 
AADSTS500011: The resource principal could not be found... 
```
  or, for a user-assigned identity, the very common
  `The requested identity has not been assigned to this resource` - meaning
  the identity is valid but **not attached to this resource**; assign it to the
  resource (a system-assigned one cannot be selected where a user-assigned is
  required). And the usual role problem:
  `AuthorizationFailed ... does not have authorization to perform action
  'Microsoft.Storage/storageAccounts/blobServices/containers/read' over scope
  '/subscriptions/.../blobServices/default/containers/x'`.

- **RBAC role assignments are eventually consistent.** Immediately after
  assigning a role, the token can still be issued *without* the new role, and
  the call fails for a minute or two. This is not a bug to debug; it is the
  reason a just-created App Service + just-created role assignment
  intermittently 403s. Retry.
- **For local development**, use `DefaultAzureCredential` (which tries the
  developer's Entra token first, then a managed identity, then a workload
  identity, then a developer CLI login) - it works on a laptop and in Azure
  with the same code. Do not branch your auth code by environment; use
  `DefaultAzureCredential` and let the chain do its job.
- **App registrations / service principals with client secrets** are the
  legacy equivalent of an AWS access key: a non-expiring secret that lives in
  a config. Use a **federated identity credential** (a service principal with a
  federated credential, so a GitHub Actions workflow can assume it with no
  secret) or a managed identity. If a secret must exist, prefer a
  certificate credential over a client secret and rotate it.

## Naming, regions, and the gotchas that follow

- **Region is mostly a deployment decision, but two things are not
  reversible-or-cheap:** a **region's availability** for a given SKU (a
  resource in a region with no capacity fails to deploy - "the requested
  resource SKU is unavailable in this region") and **paired-region failover**
  (a region paired with a second for DR; you cannot pair arbitrary regions).
- **Do not put everything in one region unless you mean to.** Multi-region
  means: which region is primary, what replicates where (storage geo-redundant,
  SQL geo-replication, availability zones within a region), and what the
  failover story is. Availability **zones** are within a region; **paired
  regions** are across. They are different tools.
- **Global resources (Storage, Front Door, Entra) have no region but are still
  billed and still single-failure-domain** in ways teams forget.
- **Resource names are globally unique for some services** (storage accounts) and
  **region/resource-group unique for others** (NICs, VNets, subnets, VMs within
  a VNet must have unique names). Encode both into the convention rather than
  discovering it in CI.
- **Availability Set vs Zone** are different: an Availability Set is a fault/
  update domain within one datacenter; a Zone spreads across physically
  separate datacenters. Using an Availability Set when you meant a Zone gives
  a weaker guarantee than you think you have.

## Policy as code: Azure Policy and deployment guardrails

- **Azure Policy** is Azure's declarative guardrail layer. A policy definition
  (JSON, with an `if`/`then` and aliases) is assigned at a management group,
  subscription, or resource group, and either **denies** non-compliant
  deployments (`Deny` effect) or **audits** them (`Audit` effect, or
  `AuditIfNotExists`). It is the Azure analogue of AWS SCPs and GCP org
  policies, and it is the correct answer to "how do we stop anyone creating a
  public IP / an unencrypted disk in this subscription".
- **Default policies exist** (some Azure-subscription-scope policies are
  assigned by default - the "Deny" policies for things like deploying certain
  regions or resources outside allowed locations). Their presence and effect are
  worth knowing because a policy can deny a perfectly valid deployment with a
  message that names the policy assignment, not your code.
- **The `Deny` effect fails a deployment with an error naming the policy** -
  that is the useful part: it is a pre-deployment gate, not a post-hoc audit.
  `Audit` flags it in the activity log / compliance dashboard without blocking.
- **Manage the policy JSON in git** and deploy it (Bicep/ARM or the
  `Microsoft.Authorization/policyAssignments` ARM resource) rather than clicking
  it into the portal; the same "if it is not in git it is not enforced" rule
  applies.
- **Terraform azurerm** and **Bicep** both let you express guardrails in code;
  for policy definitions specifically, the assignment is itself an ARM
  resource, so it can be IaC-managed. Do not hand-maintain a large policy
  assignment set in the portal.
- **Conventions and naming**: an Azure Policy can also *modify* resources on
  write (e.g. inject a diagnostic setting, add a tag) - the
  `modify`/`append` effects, and `DeployIfNotExists` for rolling out a
  configuration (an agent, a setting) to existing resources. Those are how
  you get org-wide compliance without touching every resource.

## Shared responsibility, stated concretely for Azure

This is where "we use Azure so the provider handles security" goes wrong.

**Azure secures the infrastructure; you secure what you put in it.**

| Layer | Azure's job | Your job |
|---|---|---|
| Physical, hypervisor, datacentres, network backbone, host OS | Fully Azure's | None |
| Hosted OS patching, the managed runtime (PaaS internals) | Azure patches the base OS / runtime for managed services (SQL, App Service, Functions) | You patch anything you manage yourself: a custom VM image, a container's base |
| Your data, identities, access control, secrets, network config, code | Little to none | **Everything**: RBAC assignments, public access settings, encryption configuration, diagnostic logging, app code, dependency patching |
| Physical security of your data centre hardware, access to the region | Azure's, up to the point of your subscription | - |

The failure mode: Azure's "secure by default" applies to the *platform*. A
storage account that is **public**, a function app with **unauthenticated
invocations**, a SQL database reachable from the internet with a **weak admin
password**, or a **diagnostic setting that is off** are all entirely yours. The
Azure portal's own "secure score" is a useful, imperfect checklist - it
measures posture, not correctness.

## Gotchas

- **Storage account key listed in the portal works for everything** and is
  the fastest path to a "why can't my app read the blob" - the answer is often
  that a key is being used instead of a managed identity. Turn off shared key
  access and the class of bug disappears.
- **A key or a connection string in an App Service application setting is
  visible to anyone with `listSecrets` permission** on the app - and it is
  encrypted at rest but decryptable by the platform. A managed identity has no
  such value. (`ConfigurationKey` / "list secrets" RBAC action is a real
  escalation path; treat it as sensitive.)
- **RBAC and access policies can coexist** on storage. With shared key access
  disabled and `Entra ID` authorisation on, a key-based client fails with
  `AuthenticationFailed` / `403 This request is not authorized to perform this
  operation using this permission` - which is the intended outcome, not a
  regression.
- **"Reader" on a resource group gives no data access** to storage/SQL/Service
  Bus data. Name the data-plane role (`Storage Blob Data Reader`,
  `Key Vault Secrets User`, `Key Vault Crypto User` - note Key Vault has
  separate roles for secrets vs keys vs certificates, and a secret role does
  not decrypt).
- **`Reader` at subscription scope does not include listing keys or secrets**;
  the "list keys" actions are separate and often the reason a deployment
  "cannot find the key" it just created - it needs a data-plane role or the
  `listKeys` action.
- **An Entra ID group-based RBAC assignment is a better audit trail than
  per-user assignments**, and it makes offboarding correct. Prefer assigning to
  a group, not a person.
- **Entra ID Conditional Access / MFA and PIM** are where you control
  privileged *human* access; a subscription-level RBAC role can be very
  powerful and is often assigned to a standing user. Privileged Identity
  Management (just-in-time, time-bound, approved elevation) is the Azure
  answer to standing admin.
- **Management group policy can deny a resource deployment with an error
  message that mentions the policy, not the resource type you were expecting**;
  read the whole error before debugging your template.
- **Bicep/ARM deployments are declarative and idempotent by default**; running
  one twice is a no-op. Terraform is the same. Clicking "Deploy" in the
  portal repeatedly creates *duplicate* resources - avoid portal deploys for
  anything you manage as code.
- **A resource group's name is not a namespace that must match the
  environment**; it is just a folder. Put `rg-<app>-<env>-<region>` on
  everything and let the convention carry the meaning, but do not rely on
  parsing it in automation.

## Safety notes

- Never accept a storage account key, a service principal client secret, or a
  SAS in an app config as the solution to an auth problem. Push to a managed
  identity and fix the RBAC role instead.
- Do not grant `Owner` or `Contributor` at subscription or resource-group scope
  as a shortcut. Most tasks need a narrower built-in role; when unsure, start
  from a role's documented permission list and add.
- Prefer role assignments to **groups** over individuals, and use PIM /
  just-in-time for privileged roles.
- When you disable shared key access or SAS on a storage account (a good move),
  you may break an existing consumer. Find the consumer first - a sudden
  `AuthenticationFailed` in an unrelated app is a worse outcome than leaving the
  key enabled for one sprint. Roll it out with the audit finding in hand.
- Azure Policy `Deny` assignments change what everyone can deploy; apply them
  at a management group with a pilot scope first and a documented exception
  process, or you will block your own team.

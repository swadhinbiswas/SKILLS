# ADR 0007: Use Postgres row-level security for tenant isolation

Status: Accepted
Date: 2026-03-04
Deciders: platform team; Dana Okafor asked to be on the hook
Consulted: security (Sam Ruiz)

## Context

<The problem and the constraints. What is true today, with numbers: 340
customers share one Postgres 16 primary. Per-tenant databases means 340
backends to patch, back up, and migrate; per-tenant schemas means 340 schemas
per DDL change. Isolation must be enforced by Postgres, not by the application,
because INC-1877 (2025-11-03) served customer 214's orders to customer 209 when
one service path omitted the `tenant_id` filter and the only safeguard was
"every developer remembers the filter". The team is six engineers and there is
no connection proxy that can rewrite sessions.>

## Decision

<What is decided, in the active voice. One or two sentences.>

We use Postgres row-level security. Every tenant-scoped table has `ENABLE ROW
LEVEL SECURITY` and a policy keyed on `current_setting('app.tenant_id')`, which
the connection pool sets per checkout.

## Alternatives considered

### Per-tenant database
- Rejected: 340 connections to patch and upgrade; ~6 engineer-weeks per schema
  change, recurring.

### Per-tenant schema
- Rejected: every DDL change becomes 340 statements in one transaction;
  `pg_dump` unreadable; cross-tenant reporting needs explicit federation.

### Enforce in the query builder only (ORM-scoped sessions)
- Rejected: this is what failed in INC-1877. The guarantee has to live below
  the application layer.

## Consequences

### Positive
- A missing application filter is no longer a data leak: the policy returns
  zero rows.
- Adding a tenant is an INSERT, not a provisioning job.

### Negative
- Every connection must set `app.tenant_id`; forgetting it yields zero rows,
  which reads as "no data" rather than "misconfigured" — a subtle failure.
- RLS injects a predicate into every plan on a tenant-scoped table, adding
  per-query planning overhead that grows with the number of such tables.

### Follow-up
- Add tenant RLS policy coverage to CI. (Done, INC-1903.)

## Revisit when

- Either table's p99 write latency exceeds 200 ms at 3k writes/s; or
- A compliance requirement appears that RLS cannot express (e.g. per-role
  audit logging).

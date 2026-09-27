# A worked ADR: 0012, replacing row-level security

This is a complete, realistic ADR. Note the structure: the Context carries the
*facts that changed* (the incident number, the measurement, the new compliance
requirement), the Decision is two sentences, the Consequences have real costs,
and `Revisit when` has a numeric trigger. Read it alongside ADR 0007 below to
see a supersede chain.

---

# ADR 0007: Use Postgres row-level security for tenant isolation

Status: Superseded by [ADR 0012](0012-replace-rls-with-per-tenant-roles.md)
Date: 2026-03-04
Deciders: platform team; Dana Okafor asked to be on the hook
Consulted: security (Sam Ruiz)

## Context

We are multi-tenant: 340 customers share one database. The alternative shapes
were per-tenant databases (340 backends to migrate, back up, and patch) and
per-tenant schemas (340 schemas to migrate on every DDL change).

Isolation must hold at the database layer, not only in the query builder, so
that a bug in application code cannot leak one customer's rows to another. The
incident history is the reason this ADR exists: INC-1877 (2025-11-03) served
customer 214's order list to customer 209 because one service path forgot the
`tenant_id` filter, and the fix relied on every developer remembering the
filter forever.

Operational constraint: one Postgres 16 primary, no connection proxy that can
rewrite sessions, and a team of six. We need something enforced by Postgres
itself.

## Decision

We use Postgres row-level security. Every tenant-scoped table has `ENABLE ROW
LEVEL SECURITY` and a policy keyed on `current_setting('app.tenant_id')`, which
the connection pool sets per checkout.

## Alternatives considered

### Per-tenant database
- Rejected: 340 connections to patch and upgrade. The migration tooling cost
  was estimated at 6 engineer-weeks per schema change, recurring.

### Per-tenant schema
- Rejected: every DDL change becomes 340 statements inside one transaction;
  `pg_dump` output becomes unreadable; cross-tenant reporting needs explicit
  federation.

### Enforce in the query builder only (ORM-scoped sessions)
- Rejected: this is what failed in INC-1877. The guarantee has to be below the
  application.

## Consequences

### Positive
- A missing application filter is no longer a data leak: the policy returns zero
  rows.
- Adding a tenant is an INSERT, not a provisioning job.

### Negative
- Every connection must set `app.tenant_id`; forgetting it yields zero rows,
  which reads as "no data" rather than "misconfigured".
- RLS adds per-query planning overhead (predicates are injected into every plan
  on a tenant-scoped table).

### Follow-up
- Add the tenant RLS policy coverage test to CI. (Done, INC-1903.)

## Revisit when

- Either table's p99 write latency exceeds 200 ms at 3k writes/s; or
- A compliance requirement appears that RLS cannot express (e.g. per-role
  audit logging).

---

# ADR 0012: Replace row-level security with per-tenant database roles

Status: Accepted
Date: 2026-09-18
Deciders: platform team; Sam Ruiz asked to be on the hook
Supersedes: [ADR 0007](0007-postgres-row-level-security.md)
Informs: [ADR 0003](0003-single-postgres-instance.md)
Where this lives: `db/migrations/0044_tenant_roles.sql`,
`src/db/pool.py` (`acquire_tenant`), `tests/test_tenant_isolation.py`

## Context

ADR 0007 chose RLS. Two things have changed since March, and both are
specific, measured facts rather than preferences.

1. **Plan-cache pressure.** RLS injects a tenant predicate into every plan on
   the `orders` and `order_items` tables. At 3k writes/s the instance shows
   `pg_stat_statements` top entries at 40–90 ms of planning overhead per
   statement, and the p99 write latency on `orders` crept from 12 ms (February)
   to 58 ms (August). This was traced in INC-2291 (2026-08-14). It is not
   catastrophic yet, but it is the largest single contributor to write latency
   and it scales with the number of tenant-scoped tables.

2. **Audit requirement.** Compliance (SOC 2, control CC6.1, effective
   2026-09-01) now requires that access be attributable to an authenticated
   *role*, not just to a session variable. An `app.tenant_id` setting is
   trivially spoofable by any code that can run SQL; a per-tenant Postgres role
   is not, and it makes `pg_stat_activity` and the audit log name the tenant
   directly.

The data model is unchanged: still one database, still one schema, still 340
tenants. The isolation guarantee of ADR 0007 must be preserved — the
integration test that proves it is a hard gate, not a nice-to-have.

## Decision

We replace RLS with a per-tenant Postgres role: one `tenant_<id>` role per
tenant, granted `SELECT, INSERT, UPDATE, DELETE` on the tenant-scoped tables
only, and the application pool sets `SET LOCAL ROLE tenant_<id>` inside each
transaction after the caller authenticates.

## Alternatives considered

### Keep RLS and tune it (the status quo)
- Rejected on both grounds above. Tuning (e.g. `plan_cache_mode`) reduces the
  planning cost without addressing the audit requirement, so the decision
  would have to be revisited for CC6.1 anyway. Doing it now avoids paying for
  the RLS cost and the replacement cost.

### Per-tenant schema
- Rejected for the same reasons as ADR 0007 (340 schemas per DDL change,
  unreadable dumps).

### Trusted pooled `app.tenant_id` with periodic auditing
- Rejected: fails CC6.1, because the session variable alone is not a
  cryptographic or permission boundary.

## Consequences

### Positive
- The write-latency contributor from INC-2291 is removed; p99 write on `orders`
  is expected to return to ~12 ms (verify in the week-2 dashboard).
- Audit logs name the tenant role directly; CC6.1 evidence is a Postgres log
  line, not an application claim.
- Post-incident forensics ("which queries ran as tenant 214 at 03:12") becomes
  a `pg_stat_statements` filter rather than a log search.

### Negative
- **Every connection path must set the role.** A missed `SET LOCAL ROLE` now
  yields a permission error (loud) rather than zero rows (quiet). That is the
  intended direction, but it means connection-pool bugs now surface as 500s;
  `tests/test_tenant_isolation.py` and a staging smoke test are gates.
- Role management becomes a real concern: creating, rotating, and dropping 340
  roles needs a migration path and a reconcile job (follow-up below). A leaked
  role with stale grants is a small, long-lived security liability.
- The daily `pg_dump` includes role definitions; backup/restore procedures must
  carry roles, which the current runbook does not yet do.

### Follow-up
- [ ] `db/reconcile_roles.py` — idempotent reconciliation of tenant roles from
  the tenants table; run nightly. (Ticket PLAT-812.)
- [ ] Update the backup/restore runbook to include roles. (Ticket PLAT-815.)
- [ ] Delete the RLS policies in migration 0045, after the role path is live
      and the isolation test passes against it. (Ticket PLAT-813.)

## Revisit when

- Tenant count exceeds ~2,000 (role-management overhead becomes a real cost);
  or
- The audit requirement is relaxed such that a session variable suffices; or
- Write latency on any tenant-scoped table regresses above 20 ms p99, which
  would mean the role switch itself (a `SET ROLE` per transaction) is now the
  bottleneck and needs connection-per-tenant instead.

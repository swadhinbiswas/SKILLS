# Spec review, common defects, and worked examples

Read this when reviewing someone else's spec, or when your own spec keeps
producing the wrong build.

## The defects a reviewer should look for, in order

1. **The problem is a solution.** "We need Redis to cache sessions" is not a
   problem. Ask: what is slow/wrong for the user, and would a different
   approach also fix it? If the constraint is not actually Redis, the spec
   has pre-committed.
2. **No non-goals.** Guaranteed scope creep later. Every spec that ships late
   had a non-goals section it ignored.
3. **Acceptance criteria that cannot fail.** "The page loads quickly" is not
   a criterion. A criterion must be decidable by a test or a demo, with a
   named observable.
4. **Error and empty states are unspecified.** Happy path only. The builder
   will invent behaviour, inconsistently, and it will be wrong.
5. **No rollback, or an irreversible migration with no stated end state.**
6. **Success metrics with no baseline or guardrail.** Unmeasurable or
   un-falsifiable.
7. **Requirements that name technologies instead of behaviour.** Fine as a
   decision, wrong as a requirement — the requirement is "filters reports by
   date", the decision is "use the existing query builder".
8. **Conflated requests.** Two features, one spec, both half-built. Split.
9. **Open questions marked non-blocking that are actually blocking** (an
   unanswered auth model, an undecided data owner). These surface as a
   3am page.
10. **No owner for the acceptance test.** If nobody owns verifying the
    criteria, they will not be verified.

## A minimal worked example (abridged)

```markdown
# CSV report export — Technical Spec
Status: In review
Owner: R. Okonkwo
Reviewers: data-eng, security

## Problem
Support handles ~40 tickets/month where customers cannot get their usage data
out of the product and resort to screenshots of the dashboard. Two enterprise
renewals in Q3 cited reporting as a blocker. No data is lost; the product
works, it just cannot answer "give me last quarter's numbers" without help.

## Goal
An admin can download their org's usage for a chosen date range as CSV,
without contacting support.

## Non-goals
- Scheduled/recurring exports (separate spec).
- PDF or Excel formats — CSV only.
- Per-user (non-admin) export.
- Exporting raw event logs; only the aggregated usage table.
- Anything for accounts > 10M events in range (see Success metrics).

## User stories
### As an admin, I can export my org's usage as CSV for a date range.
Acceptance criteria:
- [ ] Given an admin, when they open Settings → Export and pick
      2026-01-01..2026-03-31, then a CSV downloads within 60s containing one
      row per (day, metric) for the org.
- [ ] Given a range > 365 days, when they request it, then they see an error
      "range too large (max 365 days)" and no export starts.
- [ ] Given a range with no usage, then they get a valid CSV with headers
      and zero data rows (not an error, not an empty file).
- [ ] Given a non-admin, the Export nav item is not visible AND
      GET /v1/exports returns 403.
- [ ] The export reflects data as of the moment of the request, and the
      download link expires in 15 minutes.

## Requirements (technical)
- Data model: none new; read the existing `usage_daily` table.
- API: POST /v1/exports {from,to} -> 202 {export_id};
      GET /v1/exports/{id} -> {status, url|error};
      GET /v1/exports/{id}/download -> text/csv.
- Config: `max_export_range_days` (default 365), `export_ttl_minutes` (15).
- Feature flag: `csv_export` (default off, admin-only rollout).

## Edge cases
| Situation | Behaviour |
|---|---|
| Range > max | 400, "range too large" |
| No rows in range | 200, header-only CSV |
| Non-admin | 403 (not hidden-only) |
| Export job fails | status=failed + reason in the export record; UI shows retry |
| Two exports same range | allowed; independent ids (idempotent by id) |

## Success metrics
- Support tickets tagged "reporting/export" per month: ~40 → <10 (8 weeks).
- Guardrail: p95 of GET /v1/usage unaffected (<200ms), export job CPU < 5%
  of a core at current volume.

## Rollout & rollback
Flag `csv_export` off by default; enable for 5 internal orgs, then 10%, then
all. Rollback: turn flag off; in-flight exports finish or are cancelled by
the 15-min TTL. No data migration, nothing to reverse.

## Open questions
| Question | Owner | Needed by | Blocking |
|---|---|---|---|
| Do we need a per-row org filter in the export at scale? | data-eng | before beta | No |
```

## Spec vs design doc vs ADR — when each

- **Spec**: what we are building and how we will know it is right
  (problem, scope, criteria, metrics). Lives with the feature.
- **Design doc / RFC**: how we will build it, in technical depth (schemas,
  algorithms, diagrams, trade-offs). Link it from the spec.
- **ADR**: a decision with lasting consequences (choosing Postgres over
  Dynamo, an irreversible migration). One decision per ADR, immutable once
  accepted, superseded rather than edited. See
  `architecture-decision-records` in the `docs` domain.

A spec that contains a full design has usually grown past its job; split it
and keep the spec decision-focused.

## Making acceptance criteria testable

A quick rewrite pass:

| Vague | Testable |
|---|---|
| "Loads fast" | "p95 TTFB < 300ms for a 30-day export over 1M rows" |
| "Handles errors" | "If the job fails, export.status = 'failed' and GET returns 200 with `{status, reason}`; the UI shows a Retry button" |
| "Works for all users" | "Admins can export; non-admins get 403 from the API and the nav item is hidden" |
| "Scales" | "Under 10 orgs exporting a 365-day range concurrently, export worker CPU stays < 30% of a core and no export exceeds the 60s SLO" |

If you cannot finish the right-hand column, that is the spec telling you the
requirement is not yet decided. Go decide it.

## Length discipline

If the spec exceeds ~3 pages, the fix is usually one of: move schema/algorithm
detail to a design doc; split two features; collapse a "Requirements" list
that restates the user stories. Keep the spec to what a builder and a checker
need, and link the rest.

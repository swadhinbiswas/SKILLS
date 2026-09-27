---
name: task-breakdown-and-estimation
description: Break a feature or project into small shippable pieces and estimate them honestly - vertical slices over horizontal layers, sizing without fake precision, time-boxed spikes for unknowns, dependency ordering, definition of done, and safe parallel work. Use when planning a project, when a task is "too big", when estimating a quote or sprint, or when work keeps overflowing. Triggers on "estimate", "break this down", "how long", "sprint planning", "task too big", "work breakdown", "scope", "parallelize", "story points".
compatibility: Sizing units (points, days, t-shirt sizes) are team convention; the method is unit-agnostic. No tool required.
metadata:
  version: "1.0"
---

# Task Breakdown and Estimation

Two jobs: cut work into pieces that can each be **finished and verified**, and
size them without lying. A breakdown is good when each piece ends in something
demoable and nothing is blocked waiting on an unwritten lower layer.

## Vertical slices, not horizontal layers

The most common mistake: break by *component* ("build the database", "build
the API", "build the UI"). That produces three long parallel efforts, none of
which a user can see, and the integration risk lands all at once at the end.

Break by **user-visible capability**, smallest-first, each one working
end-to-end:

```
Horizontal (wrong)        Vertical (right)
- DB schema                - As an admin, list users (schema + API + UI, thin)
- API endpoints            - As an admin, invite a user
- Frontend                 - As a user, change my own settings
- (nothing works for 8 wks) (something demoable every few days)
```

Rules for slicing:

- **Slice by workflow, not by layer.** "Create → read → update → delete" are
  four slices; "database + API + UI" are not.
- **Start with the cruddiest path that produces value** (often a read-only
  view), so real feedback arrives early.
- **Each slice must be independently shippable** (or explicitly flagged as
  hidden behind a flag) and independently revertable.
- **A slice that only makes sense with the next one is too big.** If you can't
  demo it, split it again.

## Sizing without fake precision

- **Never give a single point estimate with a decimal.** "3.5 days" is false
  precision on something that is a distribution, not a number. Use a range or
  a bucket.
- **Compare to a known reference.** "About as big as the settings page" beats
  an absolute number, because your team's history is your only real data.
- **Estimate in the unit your team actually uses** — points, ideal days, or
  t-shirt sizes (S/M/L/XL). Points are deliberately unitless so they are
  never compared to calendar days.
- **Size by complexity, not by duration.** Duration depends on who's free;
  complexity is a property of the work. Converting points to days requires
  knowing the team's throughput, which you only learn by measuring.

```
Horizontal (false precision)     Honest
"4 days"                        "M (≈ 2-3 days for someone who knows the area,
                                  2-3 weeks for someone who doesn't)"
```

**T-shirt sizes:** XS = < half a day, S = ~1 day, M = 2-3 days, L = ~1 week,
XL = > 1 week (split it). Anything an L or bigger is not a task; it is a
project with hidden tasks.

**The best estimate is decomposed.** Estimate the pieces (UI, API, data
model, tests, rollout), sum the *pessimistic* ends of each range, and that
sum is the realistic figure. Most bad estimates come from estimating the
happy path of a whole feature as one blob.

## Spikes for unknowns

A **spike** is a time-boxed investigation whose output is a *decision or a
number*, not code. Use one when you don't know if something is feasible or
how expensive it is, and you cannot estimate past that uncertainty.

- **Time-box it** (a day, half a day — write the day down). A spike that
  isn't time-boxed becomes a feature with no acceptance criteria.
- **Deliverable is written**: "Postgres JSONB with a GIN index meets the 50ms
  p95 at 10M docs" or "it does not; DynamoDB + DAX is the only option". Not
  "I looked into it".
- **Spike code is thrown away** unless it is deliberately promoted to
  production and re-reviewed as such. Prototype code that quietly becomes the
  real thing is how unmaintainable systems are born.
- **Spike the riskiest unknown first**, in the dependency order, because it
  can invalidate the estimates and the plan for everything after it.

## Dependencies and sequencing

Before scheduling, draw the dependency edges explicitly. A task is ready only
when its inputs exist.

- **Hard dependencies** (a migration before the code that reads it) are
  ordering constraints — sequence them.
- **Soft dependencies / can run in parallel** — group independent tasks so
  they can be picked up by different people simultaneously.
- **Look for the long chain.** The critical path (the longest chain of
  dependencies) sets the minimum duration, no matter how many people work on
  it. Adding people to a task that is *not* on the critical path does nothing.
- **Sequence for feedback and risk, not for comfort.** Put the thing most
  likely to change the plan earliest.

```
                [schema]──┐
                [API]────┼──[UI]──[e2e test]──[rollout]
   [feature flag]────────┘
Critical path: schema -> API -> UI -> e2e -> rollout.  The flag is parallel.
```

## Definition of Done (per task)

A task is done when all of these are true — agree this per task, not
per-project, so "done" is never negotiable at the end:

- [ ] Code written and reviewed.
- [ ] Tests written and passing (including the edge cases from the spec).
- [ ] It works end-to-end in a real (non-mock-only) environment.
- [ ] Error and empty states handled.
- [ ] If it changes an interface or schema, that is documented and any
      migration has a rollback.
- [ ] Any flag/wiring needed for it to actually run in the environment.
- [ ] No new alerts, TODOs, or commented-out code left behind.

## Parallelising work safely

Parallel work multiplies *coordination* cost and integration risk, so only
parallelise where the seams are clean:

- **Safe to parallelise:** independent modules/files with a stable interface
  agreed up front; separate slices with no shared table; work that will be
  merged in one PR vs many.
- **Expensive to parallelise:** many branches touching the same files
  (constant merge conflicts); work depending on an interface that is not yet
  fixed; anything with shared mutable state or a shared migration.
- **Interface first.** Parallelism needs a contract (an API schema, a type, a
  table DDL) agreed *before* the parallel work starts, so the pieces meet
  cleanly.
- **Feature flags** let parallel teams ship independently behind a flag and
  integrate later — but flags are debt; give each a removal date.
- **One owner per interface.** If two people will edit the same schema or
  public API, the integration cost exceeds the parallelism benefit.

## Estimating traps

- **The estimate includes the work nobody mentions**: tests, code review
  churn, rollout, docs, monitoring. Budget 20-30% over the sum of the happy
  path pieces.
- **Unknown codebases and unknown third-party APIs are 2-3x, not 1.1x.**
  Reading unfamiliar code *is* the task the first time.
- **Dependencies on other people are not "in progress = 90% done."** Waiting
  on a team is a scheduling risk, not progress.
- **Adding people to a late project makes it later** (Brooks's law): the work
  is not yet understood well enough to parallelise, and the integration cost
  exceeds the gain.
- **Optimism is systematic.** Every estimate has an unknown tail. Plan the
  schedule from the pessimistic end, and keep a buffer.

## The breakdown output

When you produce a breakdown, give this shape:

```markdown
## Goal (one line)
## Slices (ordered, each independently shippable)
| # | Slice | Size | Depends on | Done when | Owner |
|---|-------|------|------------|-----------|-------|
| 1 | Admin sees user list (read-only) | S | schema | list renders + e2e test | ... |
| 2 | Admin invites a user | M | 1 | invite works + email sent | ... |
| 3 | User edits own settings | S | schema | settings save + persist | ... |
## Spikes (time-boxed)
- [1 day] Does the org's data volume support per-row export in <60s? Output: yes/no + number.
## Critical path
1 -> 2 -> 4 (rollout). Slice 3 is parallel.
## Open questions
- [blocking] who owns the email sending?
```

Slices ordered smallest-first, dependencies explicit, done-when testable, the
critical path named, unknowns time-boxed. That is the whole deliverable.

## Gotchas

- **A task whose "done when" contains no noun a user would recognise is not a
  slice.** "Design the schema", "Build the API", "Add the endpoint" all reach
  Done without anything outside the team being able to see it. Rewrite the first
  slice as the cruddiest real read — "admin sees a list of users" — and let the
  table end up with exactly the columns that list needs, none speculative.
- **A slice that owns a migration is not independent of the slices after it.**
  Two slices that each add a `NOT NULL` column with a volatile default rewrite
  the table twice and both queue for the same migration window, and the first
  slice becomes unmergeable once the second one's migration has landed. Put
  migrations in their own slice, first, with a written rollback; flag removal
  is its own later slice, not a checkbox on the original.
- **The critical path is wrong until review and deploy are nodes in the
  graph.** The chain that actually gates the release is usually `migration ->
  code -> review -> staging deploy -> prod flag flip`, not `schema -> API -> UI`.
  A one-hour migration that can only run in a Tuesday 02:00 window is the real
  critical path, and it is invisible in a graph that stops at "rollout".
- **A spike that keeps its code becomes production code without review.** The
  spike's branch already runs and is one commit from being merged as "the first
  cut of the feature". Put spike work on a branch named `spike/`, delete it, keep
  only the number or the decision. Promoting it is a separate decision that goes
  through normal review.
- **A commitment made on an open question is a bet, not an estimate.** When the
  open item is "does the payment API support partial refunds", a 3-point size is
  a coin flip with a number attached, and the number looks like knowledge. Commit
  the time-boxed spike instead — its done-when is a written decision, and the
  story gets sized next iteration against an answer.
- **Fake precision survives review when it carries a decimal.** "3.5 days" is
  read as a commitment to the half-day. Round to a bucket, give the range, and
  name what makes the range wide. The subtler form: any single number with no
  reference class is a guess in a suit — the only real data is the team's own
  shipped history, so anchor to it ("about the invoice PDF") and adjust.
- **"Just add a column" is a five-item task.** Migration, backfill of existing
  rows, every read path that must handle the old rows, the export and any report
  that now includes it, and the validation. The hidden work is always in what
  *reads* the thing, never in what writes it, which is why one-line schema
  changes are the most reliable source of 5x underestimates.
- **Sharing a file serialises the work at the merge, not at the review.** Two
  branches that both touch `models/user.py`, or both add a migration to the same
  directory, integrate in the last two days of the iteration, when the other
  context has been closed for a week. Judge parallelism by file overlap, not by
  how unrelated the tasks sound, and merge the agreed contract (OpenAPI schema,
  protobuf file, the DDL) before anyone branches.
- **Sum the pessimistic ends of the parts, then carry the unknown as its own
  row.** A blanket "+20%" hides which part could be 3x. Keep "CSV generation,
  unknown until spike" as a visible line in the breakdown; a total with no row
  for the risky part is understating, not being careful.

## Checklist

- [ ] Work is sliced vertically (each piece demoable end-to-end), not by
      layer.
- [ ] Every piece is independently shippable and revertable.
- [ ] Sizes are buckets or ranges vs a known reference, never false-precision
      point estimates with decimals.
- [ ] Anything XL is split further.
- [ ] Unknowns are time-boxed spikes with a written, decision-shaped
      deliverable.
- [ ] Dependencies are explicit; the critical path is identified.
- [ ] Definition of Done is per task and includes tests, error states, and
      rollout wiring.
- [ ] Parallel work has agreed interfaces and non-overlapping files; shared
      schema/API has a single owner.
- [ ] Estimates include tests, review, and rollout, not just the happy path.
- [ ] Read `references/estimation-depth.md` when you need the finer
      decomposition method, the reference-class trick, or how to give a
      range that survives contact with the team.

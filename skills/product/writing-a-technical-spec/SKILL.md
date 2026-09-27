---
name: writing-a-technical-spec
description: Turn a feature idea into a written spec an engineer can build without asking you questions - problem statement, scope, non-goals, requirements, user stories with testable acceptance criteria, edge cases, failure modes, and success metrics. Use when someone asks to "write a spec", "design doc", "RFC", or "PRD" for a feature, before starting implementation, or when a team keeps building the wrong thing. Triggers on "write a spec", "design doc", "RFC", "PRD", "acceptance criteria", "scope creep", "non-goals", "what are we building".
compatibility: Template is markdown; render to your team's system (Linear, Notion, GitHub issue, ADR) after editing. Review before starting implementation.
metadata:
  version: "1.0"
---

# Writing a Technical Spec

A spec exists to make the **build** and the **check** possible without the
author in the room. If a reader would have to ask you a question to start, the
spec is not done. A good spec is short enough to read and specific enough to
test against.

Write the spec **before** the code. A spec written afterwards describes what
you built, not what you should have built.

## The two sections that do the most work

Most bad specs are missing these, and their absence is why the work goes
wrong:

- **Non-goals** — what this deliberately does *not* do. Non-goals are how you
  stop scope creep, and they give reviewers something to point at when
  someone asks for "just one more thing". Every "can we also…" gets answered
  with "that's a non-goal for this spec" or a new spec.
- **Acceptance criteria** — the testable conditions that define "done". If a
  criterion can't be checked by running or observing something, it's not an
  acceptance criterion; it's a wish.

## Template

Copy this skeleton and fill it in. Delete sections that genuinely do not
apply, but keep Problem, Scope/Non-goals, Acceptance criteria, and Success
metrics.

```markdown
# <Feature> — Technical Spec
Status: Draft | In review | Approved | Implemented
Owner: <name>
Last updated: YYYY-MM-DD
Reviewers: <names>

## 1. Problem
<2-4 sentences. The current situation, who it hurts, and evidence it is real
(usage data, support tickets, incident count, time wasted). Not the solution.>

**Why now:** <what made this urgent/valuable now>

## 2. Goal
<One sentence: the outcome we want, measurable if possible.>

## 3. Non-goals
- <Thing a reader would reasonably assume is included, but is not.>
- <Adjacent problem this does NOT solve.>
- <Scale/performance/UX we are explicitly not optimising for yet.>

## 4. Scope
**In scope:**
- <capability>

**Out of scope:** (see Non-goals; keep this consistent with §3)

## 5. User stories
### Story 1: <title>
As a <role>, I want <capability>, so that <benefit>.

**Acceptance criteria** (Given/When/Then or a checklist — all must be true):
- [ ] Given <state>, when <action>, then <observable outcome>.
- [ ] <Edge case: empty / error / permission denied → specific behaviour.>
- [ ] <Non-functional: latency, limit, permission.>

## 6. Requirements (technical)
- **Data model:** <entities, fields, types, nullability, where stored.>
- **API/interface:** <endpoints, signatures, request/response shape, errors.>
  Or "no public interface; internal function X".>
- **Behaviour/rules:** <business rules, state transitions, defaults.>
- **Dependencies:** <services, libs, teams whose work we need.>
- **Configuration/feature flag:** <name, default, who can toggle.>

## 7. Design / approach
<The chosen approach in 1-3 paragraphs + a diagram if it helps. Why this
approach over the alternatives. Link the full design doc / ADR if separate.>

## 8. Edge cases and failure modes
| Situation | Behaviour |
|---|---|
| <input missing / too large / malformed> | <what happens — reject, ignore, default, error code> |
| <downstream unavailable> | <retry, degrade, fail, queue> |
| <concurrent access / duplicate submit> | <idempotency, locking> |
| <permission denied> | <403 vs hide vs error> |

## 9. Success metrics
- <metric>: <current> → <target>, measured <how, where>.
- <guardrail metric that must not regress, e.g. p95 latency, error rate.>

## 10. Rollout & rollback
- <flag? which users first? migration needed?>
- **Rollback:** <exact steps to undo, and what happens to data written meanwhile.>

## 11. Open questions
| Question | Owner | Needed by | Blocking? |
|---|---|---|---|
| <...> | <...> | <date> | Yes/No |

## 12. Alternatives considered
<What else was considered and why it was rejected. Keeps future readers from
re-litigating.>
```

## Writing each section well

**Problem** — describe the pain with evidence, not adjectives. "Support
handles ~40 tickets/month about duplicate charges" beats "duplicate
charges are a significant problem". A spec whose problem statement is really
a solution in disguise ("we need a Redis queue") has skipped the step where
you'd ask whether Redis is the constraint.

**Goal** — one sentence, and it should be a *user-visible* outcome, not an
implementation ("users can filter reports by date" not "add a `date_from`
column").

**Non-goals** — write the ones that will actually come up. The realistic
candidates: mobile support, bulk/batch operations, permissions granularity,
internationalisation, historical data migration, performance beyond N, and
the "while we're here" refactors. Non-goals are not "we won't do it ever";
phrase them as "not in this spec".

**User stories & acceptance criteria** — write criteria as observable
behaviour. A good acceptance criterion names inputs and a specific outcome
you could test or demonstrate:

- Good: "Given a user with the `admin` role, when they open the billing page,
  then the 'Export' button is visible and clicking it downloads a CSV."
- Bad: "The billing page should be user-friendly." (untestable)

Cover the unhappy path in the criteria, not just the happy path: what happens
with no data, too much data, a bad request, a revoked permission, an
offline/failed dependency. Empty and error states are where acceptance
criteria earn their keep.

**Edge cases / failure modes** — the table forces you to decide behaviour
instead of discovering it in production. Pick a stance for each: reject with
an error, accept with a default, ignore, or defer. "We'll figure it out" is
a decision that will be made by a 3am page.

**Success metrics** — one or two, with a baseline and a target, plus a
**guardrail** (something that must not get worse: p95 latency, error rate,
support volume). A spec with no metric cannot be evaluated after shipping.

**Rollout & rollback** — additive/reversible by default. If a data
migration is involved, state what happens to rows written between deploy and
migrate. If there is no rollback, say that plainly and say why.

**Open questions** — list with owners and dates. Anything marked "Blocking:
Yes" gates the build start. Open questions are not a failure of the spec; an
*unrecorded* one is.

## Anti-goals and acceptance criteria are the load-bearing parts

If a reader pushes back on scope, point at §3 Non-goals. If a reviewer says
"is it done?", point at the acceptance criteria checklist and the edge-case
table. These two sections are what make the spec operational rather than
descriptive.

## Length and audience

- One to three pages. If it is longer, move the detailed design to a linked
  ADR/design doc and keep the spec to problem/scope/criteria/metrics.
- Audience is the **builder** (engineer who implements it) and the
  **checker** (QA, reviewer, or an automated test). Write for someone who
  knows the codebase but not the context. Name concrete artefacts
  (`POST /v1/reports`, the `report_date` column, the `new_reporting` flag) —
  see `api-design` in this repo for interface contracts and
  `architecture-decision-records` in the `docs` domain for the full
  design-vs-spec split.
- Front-load the decision: **Problem, Goal, Non-goals** at the top. Many
  readers will stop after page one; make page one the part that matters.
- State the status honestly (Draft until reviewed). A spec presented as
  "approved" that nobody agreed to is worse than no spec.

## Checklist

- [ ] Problem has evidence, not adjectives, and does not presuppose a
      solution.
- [ ] Goal is one user-visible sentence.
- [ ] Non-goals list the things a reader would otherwise assume are included.
- [ ] Every user story has testable acceptance criteria, including the error
      and empty cases.
- [ ] Edge-case/failure table gives a concrete behaviour per row.
- [ ] Success metric(s) have a baseline, a target, and a guardrail.
- [ ] Rollout and rollback are described, including what happens to data
      written during rollout.
- [ ] Open questions have owners and dates; blockers are marked.
- [ ] Status is honest; the doc is short enough that people actually read it.

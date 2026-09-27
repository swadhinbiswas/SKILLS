# Estimation depth, reference classes, and ranges

Read this when a single number will not do — when you need to defend a range,
decompose a large estimate, or convert between units without lying.

## Decompose, then sum the pessimistic ends

The single most accurate estimating technique: break the work into its real
parts, estimate each, and sum the **high end** of each range (not the
average). The average of averages is the average of an optimistic model.

Work on "As an admin, export usage as CSV", XL as a whole:

| Part | Estimate (range) | Note |
|---|---|---|
| Data model + migration | S (0.5-1 d) | reuses `usage_daily` |
| API: POST /exports (async) | S (0.5-1 d) | |
| API: GET /exports/{id} status | XS (0.25 d) | |
| API: CSV generation + download | M (2-3 d) | unknown until spike |
| Frontend: export form + polling | S (1 d) | |
| Frontend: status/download UI | S (0.5-1 d) | |
| Tests: e2e for 5 acceptance criteria | M (2-3 d) | |
| Feature flag + rollout + metrics | S (0.5 d) | |
| Review churn + fixes | 20% | |
| **Sum of high ends** | **~10-11 days** | not "1 week" |

That 10-11 days is a different conversation from "1 week". Sum the high ends,
then tell stakeholders the range *and* the assumptions behind it.

## Reference classes beat absolute numbers

Your team's history is the only estimator that knows your codebase. Anchor each
estimate to a comparable past piece of work and adjust for the differences:

```
"Our invoice PDF took 4 days and touched rendering, a new table, and an
 e2e test. This is similar but the export is a simpler read-only query.
 Estimate: M, 3 days, high confidence."
```

Adjust for: familiarity with the code, whether an external dependency is
involved, whether a migration is needed, and how much of it is new vs
patterned. A new team's first task in an area is 2-3x the team's fifth,
regardless of raw size.

## Ranges that survive contact

- Always give a **range**, and say what drives its width: unknowns, external
  dependencies, migration risk, review cycles.
- A wider range is more honest, not less competent. "3-5 days, wide because
  the query volume is unknown" beats "4 days" (which will be read as 4).
- Re-estimate when a **new unknown appears** (a schema change, a third-party
  API behaving differently). Re-estimating is not failure; not re-estimating
  is.
- State **confidence per slice**, not one confidence for the project. A
  read-only slice can be near-certain; the export generation is the risky one.

## Points vs time: why you cannot convert directly

Points are unitless by design. The conversion is empirical:

```
velocity (points/sprint)  ~= measured over the last 3-5 sprints
days ~ points / (velocity / sprint_length_days)   # very rough
```

- Use points to compare *complexity* between items.
- Use days only for scheduling, and only for the team doing the work.
- Never convert a stakeholder-facing "days" from points by a fixed rate and
  treat it as a commitment — it is an output, not an input.
- "X points" is a unitless claim that *I understand this work's shape*.
  "X days" is a *schedule prediction* that needs a team and a calendar. They
  are different objects; do not let one masquerade as the other.

## Wide-bang uncertainty: three-point (PERT) when you must have a number

For a rough probabilistic feel, or when a method like PERT is required:

```
E   = (O + 4M + P) / 6        # expected duration
sd  = (P - O) / 6
```

`O` optimistic, `M` most likely, `P` pessimistic. PERT is a rough heuristic,
not physics — use it to expose that *the range is wide*, then go find the
unknowns with a spike. A "3-point" estimate that hides a 10x spread is worse
than a bucket.

## Sizing tables your team can reuse

| Size | Rough shape | Signal it is mis-sized |
|---|---|---|
| XS | < half a day, one file, obvious | — |
| S | ~1 day, familiar pattern, small test surface | needs a migration → bump |
| M | 2-3 days, new surface but well-understood, several tests | touches 3+ systems → re-slice |
| L | ~1 week, real unknowns, multiple surfaces | usually two M's — split it |
| XL | > 1 week | not a task; it is a project needing breakdown |

## Anti-patterns, named

- **Anchor to a deadline instead of the work.** "It has to be done by Friday"
  is a constraint, not an estimate; estimate the work, then talk about
  tradeoffs.
- **Percentage-of-work units** ("it's 80% done") with no remaining-items list.
  80% of a large thing is still a large thing.
- **The optimistic-path estimate** that omits tests, review, and rollout.
- **The lumped estimate** for a feature with hidden unknowns, where a spike
  first would shrink the range.
- **Estimating someone else's patience** as a deliverable. Slippage is a
  communication problem; say early, not in a status update at the end.

## Re-estimating triggers

Re-estimate (and say so) when any of these land:

- A spike returns a number that changes the approach.
- A dependency (another team, a vendor, a migration window) slips.
- The work turns out to touch a system nobody on the team knows.
- Review or QA surfaces a class of problem not in the original criteria
  (usually an acceptance criterion that was not testable — feed that back
  into the spec).

A re-estimate with the reason and the new range is trust-building. A silent
slip is the thing teams lose faith in.

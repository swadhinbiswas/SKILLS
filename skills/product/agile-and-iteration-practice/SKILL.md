---
name: agile-and-iteration-practice
description: Run iterations that actually deliver - slicing sprint scope, definition of ready and done, realistic estimates, retros that produce a change, and holding scope when pressure arrives. Opinionated and practical, not corporate-agile-preachy. Use when planning or running a sprint, when sprints keep overflowing, when retros produce no change, or when stakeholders add work mid-iteration. Triggers on "sprint", "iteration", "retro", "standup", "definition of done", "velocity", "scrum", "carryover", "scope creep in a sprint".
compatibility: Applies to any team using time-boxed iterations (Scrum, Kanban with a cadence, or a two-week cycle). Method, not tooling.
metadata:
  version: "1.0"
---

# Agile and Iteration Practice

An iteration is a **bounded promise**: a fixed set of work, a fixed timebox,
and a clear statement of what will be true at the end. The discipline is in
protecting the promise, not in the ceremonies.

Opinionated defaults below. Adapt the labels to your team's actual process;
keep the substance.

## The commitment, and the only two ways to keep it

An iteration commits to a **set of work that will be finished and usable**.
There are exactly two levers when reality diverges:

1. **Cut scope** (fewer, smaller things done properly), or
2. **Extend time** (rare, and it costs trust and rhythm).

Adding work mid-iteration, or shipping it half-done, is not on the list.
Pick one of the two levers and make it explicit. A team that does neither is
how "sprint commitments" become fiction.

## Slicing sprint scope

Take the feature; cut it into **independently shippable slices** (see
`task-breakdown-and-estimation` in this repo). An iteration is a handful of
slices, each of which a user could actually use.

- **Smallest-first, then largest.** Early small slices de-risk and build
  momentum; the last slice is the smallest risk multiplier.
- **Aim the slice set at the commit, minus a little.** Plan to ~80-85% of
  the realistic capacity. The remainder absorbs the interruption every
  iteration has.
- **One or two "stretch" items** at the end, clearly marked. They are the
  first thing to drop when things slip, and the moment of joy when they
  land. Never build the whole plan on them.
- **Keep a slice small enough to finish in a day or two.** A slice that
  spans the whole iteration is not sliceable in practice — split it or spike
  it.

## Definition of Ready (before it enters the sprint)

An item is Ready when someone *other than the author* could pick it up and
finish it. Per item, before it is committed:

- [ ] A user story or a clear, testable outcome.
- [ ] Acceptance criteria written and testable (not "works well").
- [ ] Design/UX decided if the item needs it; otherwise a spike.
- [ ] No blocking unknown. Unknowns became spikes or separate items.
- [ ] Dependencies identified, and the *other* team's commitment obtained if
      it depends on them.
- [ ] Sized and fits in the iteration.
- [ ] The person who will verify the acceptance criteria is known.

If most items are not Ready, the fix is not "be more disciplined in
planning" — it is that specs or breakdowns are thin. See
`writing-a-technical-spec` in this repo for acceptance criteria and edge
cases.

## Definition of Done (the same bar for every item)

An item is Done when:

- [ ] Acceptance criteria are met and demonstrable.
- [ ] Code reviewed and merged.
- [ ] Tests written and passing (automated where it will be kept).
- [ ] Deployed/usable in the environment it ships to (merged is not done).
- [ ] Error and empty states handled.
- [ ] Monitoring/alerting exists if it can fail in production.
- [ ] Documentation updated if behaviour changed for users or operators.

"Done" is **not** negotiable per item. If a team routinely ships items with
"done = the code is written", the iteration's promise is hollow. Write the
DoD once, put it where everyone sees it, and apply it to every item
including the easy ones and the urgent ones.

## Estimates and velocity

- **Estimate in complexity units (points or sizes), not days.** Days are a
  schedule output that depends on who is free; points are a claim about the
  work's shape. See `task-breakdown-and-estimation`.
- **Velocity is for planning, never for individual performance.** It measures
  the team's historical throughput to forecast the next iteration. Never two
  engineers' velocity compared against each other; it measures the item mix,
  not the person.
- **Plan from measured velocity**, over the last 3-5 iterations, with a range.
  A brand-new team has no velocity — start with a deliberately small
  commitment and let it grow.
- **Don't chase a velocity number.** Optimising for a point total is
  Goodhart's law: the moment velocity is a target, the estimates inflate and
  the work splits into less-real slices. Velocity is an input to planning, not
  a performance metric.
- **Recount a changed item.** If an item's scope or acceptance criteria
  change materially mid-iteration, re-estimate it. Carrying a stale number is
  how a sprint silently overcommits.

## Holding scope when pressure arrives

This is the whole job, and it is social, not technical. When someone asks to
add mid-iteration:

1. **Acknowledge the value** — it usually has a real reason behind it.
2. **Name the trade explicitly** — "I can add this if we drop X from this
   sprint or push to next sprint." Never a bare "no."
3. **Take the smallest honest version** — a spike, a flag-gated slice, or a
   "next sprint, first item" promise beats a rushed half-done version.
4. **Let the requester choose the trade.** The person asking for scope is
   usually the person who can prioritise it; make the cost of adding visible
   and let them decide.

The failure mode is not saying no — it is saying yes to everything and
carrying a "maybe" list into the next sprint, which is how teams end up
permanently behind and call it normal. If a "maybe" list is long, the scope
process is broken, not the team's effort.

## The daily loop (keep it light)

- **Daily sync (15 min):** each person: what will I do, what's blocking me,
  what did I finish. Its purpose is **surfacing blockers**, not status for
  a manager. If it runs 45 minutes, it is a status meeting wearing a
  standup costume.
- **Blockers are the agenda.** A blocker raised in the daily is either solved
  today or explicitly parked with an owner. Unowned blockers are the single
  biggest source of slipped iterations.
- **Board hygiene:** the board reflects reality within a day. Cards in
  "In Progress" that nobody touched in three days are either blocked (say so)
  or miscategorised (fix it).

## The iteration review (demo)

Show **working software against the acceptance criteria**, not a status
report. 30-45 minutes, product present, run it on real (or realistic) data.

- Demonstrate the slices that are Done; be explicit about what is not.
- Collect feedback *now* while the work is fresh and the iteration just
  closed — that feedback shapes the next backlog, which is where it has
  traction.
- Do not demo work that will be thrown away. If you are demoing a spike,
  frame it as the decision it produced, not a half-feature.

## The retrospective that produces a change

A retro that ends in "we should communicate more" changed nothing. A retro
exists to produce **one or two concrete changes with an owner**, carried into
the next iteration.

- **Format:** simplest that works. "What went well / what didn't / what we'll
  try." A 60-minute session on a 2-week cadence is enough. The format matters
  far less than whether anything changes.
- **One or two actions, maximum.** Three improvement actions is zero actions.
- **Every action has an owner and a date**, and is visible in the next
  iteration's board. Unowned actions die.
- **Review the previous retro's actions first**, every time. This is what
  stops the retro from being theatre — if last time's action did not happen,
  either do it or drop it, and say why.
- **Retro the process, not the people.** "Deploys were flaky and we fixed
  them ad hoc" is workable. Blame kills the signal.

## Metrics worth looking at (and one that isn't)

- **Cycle time** (item enters "in progress" → Done) and **throughput** (items
  Done per iteration). These tell you where work is stuck.
- **Carryover rate** across iterations. Consistently > 20-30% means the
  commitments are too large or the DoD is not being met — fix the cause, not
  the symptom.
- **Escaped defects** and **change-failure rate** (how often a release causes
  an incident). Quality is part of "done", and these are the numbers that
  catch a team shipping faster than it can sustain.
- **Not a metric: individual velocity/utilisation.** Utilisation at 100% has
  no slack for the interrupts that every real iteration has.

## When a team is permanently behind

It is almost never "not working hard enough". In order of likelihood:

1. **Commitments are too big** (unrealistic sprint planning) → shrink the
   commitment; measure real throughput first.
2. **Too much WIP** (many parallel half-done items) → finish-done items
   before starting new ones; limit WIP explicitly.
3. **DoD is not being met** (items marked done without tests/deploy) → the
   cycle looks fast because the real work moved to the next iteration. Fix
   the DoD.
4. **Too many dependencies on other teams** → these are not your velocity;
   negotiate commitments or decouple.
5. **Interrupt-driven environment** → batch it; shield focus time. If it
   cannot be batched, the iteration format is the wrong tool — use a
   flow/Kanban approach with cycle-time goals instead.

The fix is a change to the *system* (commitment size, WIP, DoD, batching),
not an exhortation to "go faster". Changing the system is exactly what a
retro is for — so the retro is the tool, and it must produce the change.

## Gotchas

- **An iteration commits to a decision, not to a number guessed at.** The
  common failure is committing an item whose open question has no answer yet:
  "3 points" for a story blocked on "does the vendor support partial refunds" is
  a coin flip wearing a number. Commit the spike instead — it finishes inside the
  iteration, its done-when is a written decision, and the real story gets sized
  next iteration against an answer.
- **Mid-iteration scope does not arrive as extra work; it arrives as the DoD
  checkboxes on the item that was already half-finished.** "Sure, add the
  filter" gets implemented by dropping the edge cases, the error state, and the
  test on the item in progress. The requester sees +1 item; the definition of
  done quietly loses three checkboxes. If you accept the addition, name what you
  are trading, and if the answer is the DoD, say that.
- **A mid-iteration request with no acceptance criteria yet is unbounded, not
  small.** "Add a filter to the list page" is two hours until someone decides
  what filtering means. Force the shape at the moment you accept it: write the
  acceptance criteria, then size it, then offer the trade. Most "tiny"
  additions are a day, and they land on a week that was already full.
- **Points are a per-team unit, and comparing them across teams compares slice
  size, not speed.** The same story sized 3 by team A and 8 by team B is a
  statement that B cut it differently — the numbers were never on one scale.
  Never roll points up into an org-level velocity, never use them in a cross-team
  comparison, and never apply a fixed points-to-days rate. Even inside one team
  the mapping drifts as their reading of "a 3" changes.
- **Velocity as a target moves the number without moving the work.** Once a
  point total appears in a dashboard, the same stories get sized smaller and cut
  thinner, and each slice passes the DoD while the assembled system is unusable.
  The tell is velocity rising while cycle time and carryover also rise. Keep the
  point total out of anything a manager reads; cycle time and escaped defects are
  the honest numbers.
- **A velocity jump usually means the DoD moved, not that the team got
  faster.** When items are marked Done at "code written", tests, deploys, and
  error states reappear as the next iteration's work, and they land as someone
  else's cycle time. Check whether the DoD changed before reading anything into
  the trend, and record the change so the series means one thing throughout.
- **Re-adding carryover silently changes what the commitment number means.** If
  40% of last iteration came back, "34 points committed" is 20 points of new
  work plus 14 points of old work, and the new work is what nobody planned for.
  Commit to new items and keep carried items as a separate visible list with
  their original sizes.
- **A retro action that is not a card on the board is a sentence in a file
  nobody opens.** It gets discussed for an hour, written in the retro notes, and
  never checked; the next retro generates three new ones instead. Put the action
  on the board as a real item with an owner and a date, and spend the first five
  minutes of every retro on the previous actions' status. A 60-minute retro for
  twelve people is twelve person-hours to produce one change.
- **A retro held by a chronically-behind team produces process fixes for a
  capacity problem.** The complaint is always throughput, and the default retro
  output is "communicate more" or "plan better". If carryover is over 20-30% every
  iteration, the only honest output is "the commitment size is wrong" — stated
  as a decision to shrink the number, with a date on the new number.
- **Changing the iteration length changes the units of velocity.** Moving from
  2-week to 1-week sprints roughly halves the point total per sprint with no
  change in throughput, and the drop reads as a performance collapse. Record the
  cadence change, re-baseline, and wait one full iteration of new data before
  trending anything.
- **Stretch items get started first, and then they are not stretch.** If the
  team opens a stretch item before the committed work is Done, the plan now
  contains two commitments and the stretch item is the one that gets cut. Agree
  in the plan which committed item is the designated drop, and treat starting
  anything outside the commit as requiring that drop to be named out loud.

## Checklist

- [ ] Every iteration commits to a set of **finished, usable** slices; scope
      is cut or time extended, never silently both.
- [ ] Scope planned to ~80-85% of capacity plus one or two marked stretch
      items.
- [ ] Definition of Ready met per item; unknowns are spikes.
- [ ] Definition of Done is the same bar for every item, including "deployed
      and usable", and is not negotiable under pressure.
- [ ] Estimates are complexity units vs a reference; velocity plans the
      iteration and is never a personal target.
- [ ] Mid-iteration additions come with an explicit trade the requester
      chooses.
- [ ] The review demos working software against acceptance criteria.
- [ ] The retro produces one or two owned, dated changes, and reviews last
      time's changes first.
- [ ] Carryover and cycle time are watched; a chronically high carryover is
      treated as a process problem, not an effort problem.

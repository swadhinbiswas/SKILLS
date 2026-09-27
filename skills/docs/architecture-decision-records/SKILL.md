---
name: architecture-decision-records
description: Write architecture decision records that stay useful - when to write one, the context/decision/consequences format, numbering and immutability discipline, superseding, and linking ADRs to code. Use when documenting a significant technical choice, when a reviewer asks "why did we do it this way", when superseding an old decision, or when setting up a docs/adr directory.
compatibility: Template follows the MADR/Nygard style; works with any numbering scheme.
metadata:
  version: "1.0"
---

# Architecture Decision Records

An ADR captures **a decision and its reasoning at the time it was made**, so
that in two years a reader can tell the difference between "this is stupid" and
"this was a deliberate trade-off for a reason that no longer holds". It is a
permanent record — not a document you revise.

The format is fixed and small. The discipline is the hard part.

## When to write one

Write an ADR when a decision is **expensive to reverse** and **not obvious from
the code**. Both conditions, not either.

Write one for:
- A technology or major dependency choice (Postgres vs DynamoDB; Kafka vs
  SQS; a framework).
- A structural change: a new service, a new module boundary, a new data
  ownership model, a change in the dependency direction.
- A contract with other teams or external consumers: an API style, an event
  schema, an auth model, a compatibility promise.
- Choosing to *keep* something when a rewrite was proposed ("we will stay on X
  for these reasons") — arguably the most valuable ADR, because it stops the
  question being re-litigated every year.
- Deliberately accepting a known risk with a stated review date.

Do **not** write one for:
- Library choices that are trivially swappable (`requests` vs `httpx`).
- Naming, formatting, and lint config.
- Anything the code and its tests already say unambiguously.
- "We tried approach A and it was slow" — that belongs in a comment or the PR.
- Every PR. An ADR per PR turns the directory into noise; if you cannot tell
  an important decision from a routine one at review time, the problem is your
  review, not the format.

Rule of thumb: if a competent engineer joining the team would ask "why is it
like this?" and the answer is not one line of code or a comment, write it.

## The format

Five parts. Keep them short.

```
# ADR 0007: Use Postgres row-level security for tenant isolation

Status: Accepted          (Proposed | Accepted | Deprecated | Superseded by ADR 0012)
Date: 2026-03-04
Deciders: platform team, @dana (asked to be on the hook)
Consulted: security (@sam)
## Context
The forces at play. Constraints, deadlines, what we know, what we don't.
## Decision
The choice, in the active voice, one or two sentences, no hedging.
## Alternatives considered
What else was on the table, and why it lost. This is the part everyone skips
and the part that is most valuable later.
## Consequences
What becomes easier, what becomes harder, what we now have to live with.
Follow-up work, with a link to a ticket.
```

Write `Status`, `Date`, `Deciders` in the header; the rest as headings. The
header is what tooling and reviewers scan.

`Consequences` must include the bad ones. An ADR whose Consequences section is
pure upside is either dishonest or incomplete — every decision costs something,
and naming the cost is how the next person knows what to watch.

## The template

Copy this into `docs/adr/NNNN-kebab-title.md`:

```markdown
# ADR NNNN: <decision, as a statement, active voice>

Status: Proposed
Date: YYYY-MM-DD
Deciders: <team or person, "asked to be on the wrong" for a new hire>
Consulted: <who must sign off / who was asked>
Informs: <links to other ADRs this builds on, or is constrained by>

## Context

<2–4 paragraphs. What problem, what constraints, what deadlines, what data.
Include the numbers that drove the decision: QPS, team size, data volume,
incident count. The state of the world, not the conclusion.>

## Decision

<1–3 sentences, active voice, present tense: "We use X." "We will Y."
State what is decided, not what might be decided. If it is a proposal, set
Status: Proposed and say what the approval criteria are.>

## Alternatives considered

### <Alternative A>
<Why it lost. Be specific: what would have had to be true for it to win.>

### <Alternative B>
<Same.>

## Consequences

### Positive
- <what gets easier>

### Negative
- <what gets harder, and who pays>

### Follow-up
- <ticket or PR, with a link. Empty is a smell: a decision with no follow-up
  and no review date will not be revisited.>

## Revisit when

<The concrete trigger that should make someone re-open this: a volume
threshold, a date, a dependency's end of life. "When it gets slow" is not a
trigger; "when p99 write latency exceeds 200ms at 5k writes/s" is.>
```

## Numbering and the file

- **Zero-padded, monotonically increasing, never reused**: `0001`,
  `0002`, … The number is an ID, not a priority. Take the next free number
  from the directory listing, even in a PR that touches one ADR.
- **Filename**: `docs/adr/0007-postgres-row-level-security.md` — number,
  kebab-case title. Sortable, greppable.
- **One decision per file.** "Storage decisions" with three sections means
  three ADRs; the middle one gets superseded and the other two are ambiguous.
- **Kebab-case titles that state the decision**, not the topic:
  `0007-use-postgres-rls-for-tenant-isolation`, not `0007-security.md`.

## Immutability: append-only, with one exception

**Never edit an accepted ADR's Context, Decision, or Alternatives.** They are a
record of what was known and believed at a date. If you disagree with a past
decision, you write a new ADR; you do not rewrite history. Editing an accepted
ADR destroys the only thing it was for.

The one edit you may make to an accepted ADR is the **status and its
supersession link**:

```markdown
Status: Superseded by [ADR 0012](references/worked-example.md)
```

And it is better practice to make that edit *in the new ADR* (which states what
it supersedes) and let the old file keep its original status line, with the
index showing the current mapping. Pick one convention for your repo and apply
it everywhere; mixing them is worse than either.

What you may always do:
- Fix a typo or a broken link.
- Add a link from an old ADR to a later one that refines it ("see also ADR
  0012"), as a clearly marked addendum at the end, dated.
- Change `Proposed` → `Accepted` or `Rejected` (the decision's lifecycle, not
  its content).

What you may never do:
- Change an `Accepted` ADR to say something different.
- Delete an ADR. Supersede it. Deleted decisions get re-litigated, because the
  reasoning went with them.
- Renumber.

The lifecycle:

```
Proposed ──(discussion)──> Accepted ──(new ADR)──> Superseded by ADR NNNN
    │
    └──(abandoned)──────> Rejected
```

## Superseding

A new ADR supersedes an old one when the decision changes. The new ADR says so
explicitly, in the header and the body:

```markdown
# ADR 0012: Replace row-level security with per-tenant database roles

Status: Accepted
Date: 2026-09-18
Supersedes: ADR 0007 (linked below as the prior decision)
Informs: ADR 0003 (single Postgres instance)
```

```markdown
## Context

ADR 0007 chose RLS in March (its full text is at the end of
`references/worked-example.md`). Two things have changed: (1) plan-cache
pressure from RLS predicate pushdown on the orders table cost p99 ~40ms at
3k writes/s, measured in incident INC-2291; (2) the compliance requirement is
now per-role audit logging, which RLS cannot express.

## Alternatives considered

### Keep RLS and tune it (the status quo)
### Per-tenant schema
### Per-tenant database role with a connection-pooled session variable
  <chosen; wins because it keeps one schema and expresses the audit hook>

## Consequences
### Negative
- The `set_config('app.tenant', ...)` call is now required on every connection;
  a missed call is a cross-tenant read, so the integration test in
  `tests/test_tenant_isolation.py` is a hard gate.
```

The reasoning in `Context` is what makes the chain readable years later. "We
changed our mind" is useless; "the plan-cache regression in INC-2291 plus the
new audit requirement" is a decision record.

## Linking ADRs to code

An ADR that references no code decays into history. Link, by convention, from
the code back to the ADR:

- A one-line comment at the decision point, referencing the file, not a wall of
  prose:

  ```python
  # Requires a tenant on every connection; see ADR 0012.
  # tests/test_tenant_isolation.py enforces this.
  cur.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
  ```

- A generated `docs/adr/README.md` index with a table: number, title, status,
  date, supersedes/superseded-by. Generate it in CI; a hand-maintained index is
  the first thing to go stale.
- From the ADR to the code: a "Where this lives" section with the paths of the
  modules the decision governs. Update it when the code moves; it is one line
  and it is the difference between a usable record and a historical curiosity.

## The directory

```
docs/adr/
  README.md          <- generated index: number | title | status | date | links
  0001-single-postgres-instance.md
  0003-event-schema-versioning.md
  0007-use-postgres-row-level-security.md      Status: Superseded by ADR 0012
  0012-replace-rls-with-per-tenant-roles.md
  template.md
```

Open with `template.md` in the directory so the format is copy-pasteable, and
add a two-line contribution rule to `CONTRIBUTING.md`: an ADR is required for
decisions that are expensive to reverse, and an accepted ADR is never edited
after acceptance.

## Gotchas

- **An ADR records a decision, not a design.** A spec or design doc that explains
  *how* something works and leaves *what was chosen and why* implicit produces
  readers who cannot tell a deliberate trade-off from an accident. The test: if
  the record could be rephrased as "we will do X because it is the right way",
  it argues rather than decides — put the constraints and the losing option in.
- **Proposing an ADR in the same PR that implements it is a decision in
  reverse.** The record is written after the choice is already sunk, so it
  rationalises rather than records. The ADR is `Status: Proposed` and lands
  before or with the work; implementation follows acceptance.
- **An ADR that only ever gets appended to is a decision log, not a set of
  records.** Nothing in the directory tells a new engineer what is currently
  true. Every Accepted ADR is live; only `Superseded` ones are history. A
  generated index is the only cheap way to make that visible.
- **`Superseded`, `Deprecated`, and `Rejected` are three different things.**
  Deprecated: the choice is still in force but being removed, and its follow-up
  ticket is the plan. Superseded: a different decision replaced it, so the old
  one is wrong for today's code and must name its successor. Rejected: it was
  proposed and lost; the file is the only trace of the option. Superseding is a
  chain, not a flag, and a status line with no link is a dead end — a reader
  arriving from a 2019 ADR must reach the live decision in one hop, so put the
  link in the old ADR's header, the new ADR's `Supersedes:`, and the generated
  index, or the chain breaks at the first merge that forgets one of the three.
- **Correcting a wrong number or a deleted link is an edit; correcting a
  conclusion is not.** Fixing a typo, a benchmark figure that no longer resolves,
  or a path that moved is always allowed. If the *reasoning* was wrong, the old
  ADR was still the record of a real belief and a new ADR supersedes it —
  editing it silently destroys the evidence that the assumption existed.
- **A consequential decision with a historically plausible reason is exactly the
  one that must not be "tidied up".** Most tempting rewrites happen to entries
  that look embarrassing or obsolete; those are the ones a future team needs most,
  because they show the constraint that has since disappeared.
- **The number is an identifier, so the gap is permanent and fine.** Never reuse
  a number, and never renumber to close a hole. `0011` deleted from the index
  forever means "do not create it" — which is what you want from a record that
  once existed.
- **Write the context with the numbers you had, not the ones you have now, and
  date them.** "2k requests/sec" written in 2023 is what made the decision right;
  editing it to today's traffic makes the reasoning unfalsifiable, and the next
  reader cannot tell whether the original sizing was wrong or the situation
  changed.
- **`Revisit when` is enforced by nothing, so an unowned trigger does not
  exist.** "When it gets slow" is never checked. Put a threshold, a date, and the
  person or team who reopens it.
- **An ADR committed in the repo's own history is the archive — a Wiki page is
  not.** Pages get edited in place, lose diffs, get truncated by database
  revisions, and disappear with a reorg. Markdown files in the repository get
  reviewed in a pull request, which is where the disagreement about the
  decision belongs.
- **An ADR whose `Status: Accepted` was never actually agreed is worse than no
  ADR** — it gives a unilateral choice the visual authority of a team decision.
  If nobody was on the hook, `Deciders` says so, or the status stays
  `Proposed`.
- **"We will revisit this" in a PR description is not a record and does not
  survive the branch.** If a decision was made in review, write it down the same
  day, while the reasoning is still in people's heads; reconstructing the context
  from a Slack thread three months later produces an ADR that documents nothing.

## Review checklist

- [ ] It answers a question a new engineer would actually ask.
- [ ] Context states the constraints and the numbers, not the conclusion.
- [ ] Decision is one or two sentences in the active voice, unambiguous about
      what is decided today.
- [ ] Alternatives include at least one serious rival, with a real reason it
      lost — "we didn't consider it" is not an alternative.
- [ ] Consequences have a Negative section with at least one real cost, and
      who pays it.
- [ ] Status/Date/Deciders filled in; `Revisit when` has a concrete trigger.
- [ ] Number is the next free one; filename is `NNNN-kebab-title.md`.
- [ ] If it supersedes something, `Supersedes:` is set, the old ADR is marked,
      and the Context explains *why the world changed*.
- [ ] No accepted ADR is being edited in this PR; if one is, it is a supersede,
      not a rewrite.

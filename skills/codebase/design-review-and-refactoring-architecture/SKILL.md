---
name: design-review-and-refactoring-architecture
description: Review and improve a codebase's structure - cohesion, coupling, dependency direction, module boundaries, and circular dependencies - and turn a big-bang rewrite into incremental, shippable improvement. Use when doing architectural review, planning a refactor or migration, deciding whether to split a monolith, untangling a circular dependency, or deciding what to fix first.
compatibility: Language-agnostic; the dependency-cycle detection commands are named per language.
metadata:
  version: "1.0"
---

# Design Review and Refactoring Architecture

This is the **structure** review: how the pieces connect, which way the
dependencies point, and how to get from here to somewhere better without a
rewrite. The method-level work (refactoring one function, code smells) is
`refactoring-technique`; this is the map-level view.

The governing rule: **structure is an argument about change.** Good structure
is the one that makes the *next* change cheap. Judge a design by what it
predicts for future edits, not by how it looks today.

## The three questions of a design review

For the system as a whole, and for each module:

1. **Cohesion** — is everything in this module changed for the same reasons?
   A module is cohesive if a single sentence describes why all of it changes
   together ("everything about calculating order totals"). If you need "and"
   ("order totals and user emails and …"), it is not cohesive; split it.
2. **Coupling** — how many other modules must change when this one changes?
   Count *types* in signatures, imports, shared tables, and shared config.
   Count them; do not eyeball it.
3. **Direction** — do the dependencies point the way information flows? A
   module should depend on more stable, more general things, never on the
   volatile specifics that use it.

## Dependency direction and the stable-abstractions principle

The one rule that does most of the work:

> **Dependencies point from volatile to stable.** Code that changes often
> depends on code that changes rarely. Both depend on things that never change
> (a language, a wire format, a database).

Concretely, this means: a domain module must not import an HTTP handler, a
framework, or a database driver. Those are adapters at the edge. If the domain
imports the web framework, you can never test the domain without the framework,
and you can never replace the transport.

The dependency-inversion reading, applied to most repos:

```
        adapters        ->  application  ->  domain
   (http, cli, db,        (use cases,      (entities,
    queue, vendor)        orchestration)    value objects,
                                          invariants)
```

Everything points left-to-right; `domain` imports nothing but the standard
library. The violation to look for first is a domain file importing
`fastapi`/`express`/`django`/`sqlalchemy` — one grep, and it tells you how
entangled the core is.

Practical test: **can you write a unit test of the core logic with no framework
in the test's imports and no database?** If not, the boundaries are in the
wrong place, and the test suite's speed and reliability are paying for it.

## Finding the structural problems

**Circular dependencies** are the highest-signal structural smell: they prove
that the boundary is wrong somewhere. Detect them before theorising:

```bash
# Python: import-linter style check, or the dependency-cruiser equivalent
pip install import-linter && lint-imports
# or quickly, with madge (JS/TS):
npx madge --circular --extensions ts,tsx src/
# Go:
go list -deps ./... | grep -v '^github.com'   # then look for internal cycles
```

When a cycle exists, there are exactly three fixes, in order of preference:

1. **Extract the shared concept into a third module both can depend on.** The
   cycle is a symptom of a missing abstraction — the two modules are both
   using a concept neither owns.
2. **Invert one side** (depend on an interface/abstraction the other
   implements), when the relationship is genuinely one-directional at runtime.
3. **Merge the two modules**, if they are not separable. A cycle is sometimes
   the honest signal that these are one module. Shipping a "clean" boundary
   that requires 20 forwarding methods to avoid the cycle is worse than the
   cycle.

Never break a cycle by deleting an import and hoping; you have moved a runtime
failure into a subtle one.

**Shotgun surgery** — one logical change needs edits in many files — is the
symptom of logic living in the wrong place. Find the *data* the change touches;
the module that owns that data is where the change belongs.

**God files** — one file edited for unrelated reasons — split by *reason to
change*, not by line count. A 600-line file that is a linear, well-named
pipeline is fine; a 150-line file that is edited for three unrelated reasons is
not.

**Feature envy across a boundary** — a module reaching into another's internals
— is usually a missing method on the owner. Add the method to the owner; do not
add another public field.

**The Layered Cake with no seams** — everything calls everything in the same
package, and the only structure is directory names — is the hardest to fix and
the most common. Start by extracting the domain (see below), not by
reorganising directories.

## The incremental path: no big-bang rewrite

A rewrite fails for reasons that have nothing to do with the design: it takes
three times as long, the old system keeps needing features, nobody knows the
edge cases the original handled, and the rewrite is never "done" enough to cut
over. **Refactor in place, ship every step, keep the system working.** The
rewrite stays the right answer only for a genuinely small system (weeks, not
months, of work) with a hard cutover date and no feature pressure.

The sequence, each step a shipped, reversible PR:

1. **Add a test net at the seam you are about to change.** A characterisation
   test or a contract test on the boundary. Nothing structural until this
   exists.
2. **Extract the domain inward.** Move pure business logic out of the handler /
   service / ORM file into modules that import nothing but the standard
   library and each other. This is a copy-then-delete, so it is provably
   behaviour-preserving, and the tests prove it. You now have a core.
3. **Turn the database access into an interface the core defines.** The core
   declares `OrderRepository`; the adapters implement it. (In Python, a
   `Protocol`; in TS, an interface; in Go, a narrow interface declared by the
   consumer.) The dependency now points the right way.
4. **Invert one edge at a time.** Replace a direct call from stable to volatile
   with a call to an abstraction the volatile side implements. One edge per
   PR, each reversible.
5. **Move behaviour to the owner of the data.** Every shotgun-surgery hotspot
   is one PR: the change that is easiest *now* becomes local to one module.
6. **Collapse the forwarding layers** that steps 2–5 created. Temporary
   indirection is a cost; remove it once the boundaries hold.
7. **Enforce it.** A lint rule or an architecture test (e.g. an import-linter
   contract, ArchUnit, `dependency-cruiser`) that fails the build when a
   forbidden edge reappears. Without this the codebase drifts back within a
   quarter.

Each step is shippable, each can be reverted independently, and the value
arrives from step 2 onward. Track the structural metrics (below) so progress
is visible and so nobody can quietly undo it.

## The structural metrics worth tracking

Track these in CI or on a dashboard, so the argument about structure has
numbers. A rising count is not a code review comment; it is a trend with an
owner.

| Metric | How | Healthy |
|---|---|---|
| Dependency cycles | `madge --circular`, `lint-imports` | 0, and CI-enforced |
| Forbidden edges (e.g. `domain → adapters`) | architecture test / lint rule | 0 |
| Files above a size threshold (loc) | `cloc`, `scc`, `tokei` | trend down; a new >500-loc file needs a reason |
| Coupling fan-in/fan-out per module | `dependency-cruiser -T json` or the IDE's metric | high fan-out only for stable modules |
| Test runtime / suite size | CI | growing slowly, not doubling per feature |
| Change frequency per module (git) | `git log --format= --name-only` | the hot module is the one to protect, or split |

**Change frequency is the best smell detector you have**: a file that changes
in 40% of commits is a module whose cohesion is wrong, whatever the code
looks like. Watch the *distribution*, not the average.

## When a big-bang rewrite is genuinely the right answer

Rare, but real. All of these, together:

- The system is small (a few thousand lines) and the target is understood.
- The old system's shape makes the required change impossible, not merely
  painful — a data model that cannot represent the new domain at all.
- There is a hard external deadline (a platform migration, a compliance
  requirement) and incremental work cannot reach it in time.
- There is a cutover: the old and new can run side by side, and the data
  migration is a real, designed, tested step (not a `dump | restore`).

Even then: **strangle, do not bulldoze.** Put the new implementation behind
the same interface, route traffic to it gradually, keep the old path alive
until the new one is proven, and delete the old only when nothing routes to
it. A "rewrite" with no incremental cutover is a bet that you will finish
before the requirements change — a bet teams lose.

## Review checklist

- [ ] Each top-level module has a one-sentence reason-to-change, and no
      module's sentence needs "and".
- [ ] No cycles (`madge`/`lint-imports` clean), or each one is understood and
      justified in writing.
- [ ] Dependency direction: the core imports nothing from the edges; the edges
      know about the core. Checked by grep *and* by a CI rule.
- [ ] The core can be unit-tested with no framework and no database; the test
      suite reflects that (fast core tests, slow adapter tests).
- [ ] Blast radius per module counted, not guessed; the worst offender has an
      owner and a plan.
- [ ] No "utils/common/helpers" module; shared code is shared *deliberately*
      and lives with its owner.
- [ ] Shotgun-surgery hotspots identified from git history, and the top one is
      on the refactor list.
- [ ] The plan is a sequence of shipped, reversible PRs, each with a test net
      and a metric that moves.
- [ ] A CI rule enforces the boundaries being introduced, so the improvement
      cannot silently regress.
- [ ] Any proposed rewrite has a stated cutover, a data-migration plan, and a
      rollback.

## Gotchas

- **Directory reorganisation is not architectural improvement.** Moving files
  without changing dependencies is churn: a large diff, no behaviour change,
  and a merge conflict for every open branch. Move code only when a dependency
  edge is changing, and do the move in a commit that changes behaviour (or is
  at least a real refactor with tests).
- **Reducing line count is not the goal.** Deleting a feature is not a
  refactor; deleting dead code is. A longer file that is cohesive beats a
  shorter one that is a bag of unrelated things.
- **A "clean architecture" with 12 layers of interfaces and no tests is worse
  than a coupled monolith with good tests.** Structure is a means. If the
  boundaries add indirection you cannot navigate, they have negative value.
- **Coupling to a stable third-party API is fine.** Coupling to a *volatile*
  internal module is the problem. Do not build an abstraction to insulate
  yourself from something that is not going to change.
- **The most-changed file is often the correct place for the code**, not the
  wrong one — if it is the natural home of a volatile concern. Splitting it
  because it is hot is a mistake; check its cohesion first.
- **Circular dependencies are sometimes a language-level artefact** (a type
  in `types.ts` importing from `api.ts` for a union member). Fix with a shared
  leaf module, not by removing the type.
- **Eager boundary enforcement without a working test suite creates friction
  and gets disabled.** Enforce after the tests exist, and enforce the *one*
  rule that matters most first (e.g. "domain imports no framework").
- **Refactoring "for the architecture" during a growth phase** is usually the
  wrong priority; feature pressure will shape the architecture faster than
  deliberation. Take the cheap structural wins (delete dead code, fix a cycle,
  move one hot method) and revisit the big structure when there is slack.
- **Every boundary you add is a place bugs hide at runtime** (injected wrong,
  mocked in tests, bypassed in production) and a place a future developer will
  route around because it is annoying. Add the boundary because the coupling
  cost is real, not because the diagram looks nicer.

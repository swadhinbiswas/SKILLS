---
name: refactoring-technique
description: Refactor safely and in small steps - a code-smell catalogue with the standard refactoring for each, characterisation tests before touching legacy code, and the cases where refactoring is the wrong move. Use when refactoring a messy function or module, before a rewrite, when tests are missing and you need to change code anyway, or when a diff is "just cleanup".
metadata:
  version: "1.0"
---

# Refactoring Technique

Refactoring changes the *structure* of code without changing its *behaviour*.
Every other thing — fixing a bug, adding a feature, changing a signature — is
a different activity that happens to share a diff. Keeping them separate is
the entire discipline:

- **A refactor commit changes no observable behaviour.** If a bug is fixed, it
  is not a refactor.
- **Never mix them.** A refactor that also changes behaviour cannot be
  reviewed, cannot be bisected, and cannot be reverted.

## The precondition: tests

**No tests, no refactor.** Except in one case (below).

The rule exists because refactoring is a bet that you understood the code's
behaviour. Tests are the receipt. Without them, "refactoring" is rewriting, and
rewriting a function you do not understand is how outages are made.

Two ways to get the tests:

1. **The change is behaviour-preserving in intent** (pure restructure, obviously
   correct): write the characterisation tests first — see below — then refactor.
2. **The change is meant to alter behaviour** (fix the bug, change the
   contract): write the test that captures the *new* expected behaviour first,
   watch it fail, then change the code. The test is the specification; you are
   not refactoring, you are developing, and you skip this skill's discipline.

## Characterisation tests: for code you cannot test yet

The purpose is **not** to prove the code is correct. It is to freeze what it
*currently* does, so any change from here on is visible. (Michael Feathers'
term; it is the right one.)

Method, on the messiest function you must change:

1. **Find the seams.** Identify the inputs and the observable outputs —
   return values, thrown exceptions, database writes, files written, HTTP calls
   made, log lines. The outputs are what the test asserts on; the DB and
   network calls are what you stub.
2. **Stub the world, keep the logic.** Replace the network/DB with fakes that
   record calls. Keep the logic under test real.
3. **Cover the paths, in this order** — they are ordered by risk, not by
   importance:
   - the happy path,
   - every early return and every `raise`/`throw`,
   - every boundary (empty, one, max, over-max, zero, negative, very long),
   - the "weird but real" paths: a field that is `null`, a list with one
     element, a value of the wrong type that somehow got in.
4. **Assert on outputs, not on internals.** Assert the return value, the
   exception type and message, the recorded calls (that a retry happened, that
   a cache was written). Do not assert on private function calls.
5. **Where the behaviour is arbitrary, say so in the test name**, so the next
   person knows it is not a requirement:
   ```python
   def test_empty_cart_returns_zero_not_error():
       # Characterisation: current behaviour. Arguably a bug; see ISSUE-402.
       assert cart_total([]) == 0
   ```
6. **Do not fix anything in this pass.** Characterisation tests record. If a
   test reveals a genuine bug, file it and keep the test as-is until the fix
   lands separately.

Then: refactor, re-run, and the tests tell you whether you broke something.

**When tests are truly impossible** (hardware I/O, a legacy protocol, an
unmockable dependency), the fallback is a **golden/snapshot test**: capture the
current output on a set of inputs, snapshot it, and diff after each step. It is
weaker than a real test but it converts a blind rewrite into a visible-change
rewrite. It also goes stale silently, so date the snapshot and review it.

## Smells and the refactoring for each

Fowler's catalogue, condensed to the ones that actually appear, with the move
that fixes each. Use the smallest move that fixes the smell.

| Smell | Looks like | Fix |
|---|---|---|
| **Long function** | > ~30 lines of logic, or > 3 levels of nesting, or several `if` blocks that each do a phase | **Extract Method** on each phase; then the function reads as a list of steps |
| **Deep nesting** | `if` inside `if` inside `for` inside `if` — 5+ levels | **Replace Nested Conditional with Guard Clauses**: early `return`/`continue`, flatten. Then **Invert Conditions** |
| **Long parameter list** | 6+ parameters, several of the same type | **Introduce Parameter Object**; at call sites, use named arguments or a builder |
| **Data Clumps** | the same 3–4 fields travel together through 6 signatures | **Extract Class** into a `CustomerRef`/`Money`/`DateRange` value object; now a change is one place |
| **Primitive Obsession** | strings for status/enums/currency/IDs, `int` for money | **Replace with Value Object**: an enum, a `Money` class, a typed ID. Pass the object; the compiler now checks the call sites |
| **Feature Envy** | a method reaches into another object's data more than its own | **Move Method** to the other class; if both need it, **Extract Class** the shared data |
| **Data Clumps / duplicated conditionals** | the same `if (type == A || type == B)` in four files | **Replace Conditional with Polymorphism** (strategy/visitor) or centralise in one `switch` with an exhaustive check |
| **Switch statements** | a `switch` on a type that grows a branch per new case | **Replace with Polymorphism**, or an exhaustive-checked dispatch that fails to compile on a missing case |
| **Divergent Change** | one file edited for every unrelated change (a god file) | **Split Class** along the reasons it changes; then **Extract Module** per responsibility |
| **Shotgun Surgery** | one logical change requires edits in 7 files | **Move behaviour** into the class that owns the data; the change becomes one edit. Often needs an **Inversing** or a new collaborator |
| **Message Chains** | `a.getB().getC().getD().getE()` | **Introduce Facade** / **Extract Method**; hide the navigation |
| **Middle Man** | a class that only forwards to another | **Remove Middle Man** — delete it and call the target |
| **Speculative Generality** | an interface with one implementation "for flexibility" | **Remove** it; add the abstraction when the second implementation exists |
| **Temporary Field** | a field set by one caller and read by one method, never both | **Extract Class** the behaviour that uses it, and pass it as a parameter |
| **Duplicated Code** | the same 5+ lines in two places | **Extract Method/Function**; if it varies, **Extract Class** with a parameter or hook. Only if the duplication is *coincidental* — two similar things that change for different reasons should stay separate |
| **Comments as code** | a comment explains what the next line does | Delete the comment; rename or extract so the code says it |
| **Refused Bequest** | a subclass ignores or throws from an inherited method | **Composition over Inheritance**; or narrow the hierarchy so the base is honest |

**Order of operations, always the same:**

1. **Ensure a test** (characterisation if needed).
2. **Apply one refactoring** from the table — one kind, to one smell.
3. **Run the tests.** Red: revert that step, look again.
4. **Commit.** One smell, one refactoring, one commit, green. The commit
   message says which smell and which move (`refactor: extract
   validateOrderItems from processOrder`).
5. Repeat.

Small, green, committed steps are not bureaucracy. They are the only reason a
ten-step refactor is safe: when step 7 breaks something, you bisect to a single
commit and revert it, rather than debugging a 400-line diff that touched
everything.

## Mixed refactoring and change: the practical escape

Most real work is a behaviour change *and* a restructure. The safe protocol:

- **Split into two commits**: first the behaviour change with its test
  (reviewable as a change, bisectable to "what behaviour changed"), then the
  refactor with the same tests (reviewable as a no-op, revertible on its own).
- Or **do the refactor first**, on a separate branch, so the behaviour change
  lands on a clean, small diff on top of it.
- Never one commit that both restructures 300 lines and changes what the
  system does. It cannot be reviewed, and if it breaks you cannot tell whether
  the restructure or the change broke it.

## When *not* to refactor

Do not refactor when:

- **The code works, is small, and nobody is complaining.** "It is ugly" is not
  a reason. The cost of a refactor is paid now; the benefit is speculative.
- **You are about to delete the code.** Refactoring code you are going to
  remove next week is wasted work. Delete first (see below).
- **It is on the critical path of an active incident.** Fix forward; refactor
  later, in a calm week. A refactor during an incident changes the thing you
  are debugging.
- **There are no tests and you cannot write them** in reasonable time — that is
  the case for a rewrite or a rewrite-of-one-function behind a snapshot test,
  not for a piecemeal refactor.
- **The code is not as bad as it looks.** A 200-line file that is a linear
  pipeline with good names is fine. Length is not the problem; coupling and
  hidden control flow are.
- **You are refactoring to satisfy a linter or a metric.** Automated tools
  (formatters, linters) handle style. Do not hand-refactor for a tool that can.
- **The refactor is coupled to a feature you have not shipped yet.** Do the
  feature; refactor when the shape is proven. Premature structure is
  speculative generality with extra steps.
- **The team is busy and the tests are flaky.** Fix the flakiness first; a
  refactor into an unreliable suite is a refactor into doubt.

## Deleting code first

The highest-value "refactoring" is deletion, and it is skipped because it is
not a refactor. Before restructuring, ask: **is this still used?**

- Dead code, unused flags, abandoned abstractions, an old compatibility shim
  for a caller that no longer exists. Delete it. The "it might be used" flag
  is exactly what git history is for.
- Feature flags with a removal date in the past: delete both branches.
- A `LegacyAdapter` for a client that was decommissioned two years ago.
- Comments referring to a system that no longer exists — they actively mislead.

Deleting code needs no characterisation tests (nothing depends on it, if you
verify with search/grep and, better, by removing it and watching the tests
stay green). It is the fastest, safest, and largest win available.

## A worked example: refactoring one function

The target:

```python
def process(data):
    if data is None:
        return {"ok": False, "err": "no data"}
    items = []
    for it in data["items"]:
        if it.get("qty", 0) > 0:
            if it.get("sku"):
                price = self.catalog.price(it["sku"])
                if price is not None:
                    items.append({"sku": it["sku"], "qty": it["qty"], "price": price})
    if not items:
        return {"ok": False, "err": "no items"}
    total = sum(i["price"] * i["qty"] for i in items)
    return {"ok": True, "items": items, "total": total}
```

Smells: deep nesting, mixed return shapes (`ok`/`err` vs `ok`/`items`/`total`),
a long parameter list in `process(data)` (well, one — but `data["items"]` is a
primitive obsession of dict-keys-as-schema), and the fact that the caller must
know two different response shapes.

The steps, one commit each:

1. **Characterisation tests.** Assert the two `{"ok": False, "err": …}` shapes
   exactly, including the exact error strings, and the success shape for a
   known input. Note in the test names that the error strings are current
   behaviour. Green.
2. **Guard clauses** to flatten the nesting (`Extract Method` +
   `Replace Nested Conditional with Guard Clauses`). Same behaviour, tests
   unchanged. Green, commit.
3. **Extract Method**: `resolve_items(raw_items) -> list[LineItem]`, and
   `process` becomes a four-line sequence. Green, commit.
4. **Introduce Parameter Object**: take `data["items"]` (and the rest) as an
   explicit `OrderDraft` dataclass rather than a dict, so the schema is in the
   type, not in every caller's memory. Call sites updated in the same commit.
   Tests updated mechanically. Green, commit.
5. **Replace primitive obsession in the return**: an `OrderResult` type with
   `ok: bool` and either `items/total` or `error` — a sum type. Now the caller
   cannot forget the error case. Green, commit.
6. **Delete**: the `"err"` string literals that no caller compares, if a grep
   proves it. Green, commit.

After step 5 the function is *shorter and clearer*, and the important win is
not the function — it is that the two-shape return can no longer be
half-handled by a caller, and `OrderDraft` means a missing `items` key is a
type error at the call site instead of a `KeyError` at 3am.

## Gotchas

- **"Behaviour-preserving" is a claim, not a fact.** Characterisation tests
  are the only evidence. A refactor that changes an error *string*, a *log
  line*, an *ordering* of a list, or a *float rounding* is a behaviour change
  in a system that has a log parser, a test asserting a message, or a
  downstream consumer. Watch for these specifically; they are the changes that
  survive review and break production.
- **Reordering a list is observable.** If a function returns items in the input
  order, sorting them in a refactor "because it looks tidier" changes
  pagination, UI order, and test expectations.
- **Tests that assert on internals block refactoring.** If every test mocks the
  function you are about to extract, you will have to rewrite the tests
  alongside it — which means you no longer have a safety net. Extract first,
  then fix the tests, verifying at each step.
- **A green suite is not always a real net.** Check that the tests actually
  run and assert (a skipped or mis-scoped test is worse than none), and that
  the suite fails when you break something. If you cannot, the first refactor
  is "make the tests trustworthy".
- **Refactoring and dependency upgrades together** is a bisect nightmare. One
  at a time.
- **Refactoring in a feature branch that lives for weeks** guarantees a painful
  merge. Keep refactor branches short — days, not weeks.
- **Commented-out code is not documentation**; it is a merge conflict waiting
  to happen. Delete it; git remembers.
- **Renaming across the codebase in one commit** is fine with an IDE refactor
  and brutal by hand. Use the tool, then run the tests; a partial rename that
  compiles is a silent runtime break in a dynamically-referenced symbol.
- **Abstraction before duplication is a common trap** (and Fowler himself says
  it): you do not know it is duplication until you have seen it change twice
  for the same reason. Two similar blocks that have each changed once are not
  yet duplication.
- **The most common failure is the big-bang rewrite**: "this function is a
  mess, let me rewrite it". It takes three times as long, loses the edge cases
  you never saw, and the tests only covered the ones somebody remembered. Small
  steps, every one of them reversible.

## Files

- Read `references/characterisation-test-recipes.md` when you need concrete
  recipes for pinning down legacy behaviour (dict-returning functions, mocks
  that are too tight, golden/snapshot tests, and how to test a function with
  no seams).

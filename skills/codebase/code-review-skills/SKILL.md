---
name: code-review-skills
description: Review a change properly - hunt for the intent in the diff, check correctness, security, and tests before style, and write feedback that is prioritised and actionable. Use when reviewing a PR or diff, when asked "can you review this", when unblocking a teammate's review, or when a review came back with vague or nitpicky comments.
metadata:
  version: "1.0"
---

# Code Review

A review exists to catch the problems the author could not see, and to make
the change understandable to the next person. It is not a gate to be passed by
adding a comment on every line.

The single most valuable thing a reviewer can do is **read the diff for intent
before reading it for syntax**. Most bad reviews are a list of style
observations on a change whose real problem nobody mentioned.

## Read for intent first

Before the line-by-line pass, answer four questions from the diff alone:

1. **What is this change trying to do?** State it in one sentence. If you
   cannot, that is the first comment: *"What is this for? I see the refactor of
   X but not what bug it fixes."*
2. **What is the *smallest* way to achieve it?** If the change is 400 lines and
   the intent is "return 404 for unknown tenants", ask what the other 380 are
   for. Most over-large diffs are an unstated second refactor bundled in.
3. **What does this break?** Trace the change to its callers, its data, and its
   consumers. A renamed field or a changed default breaks things the diff does
   not touch.
4. **What is the risk of this specific change?** Migrations, auth, money,
   concurrency, and anything that is hard to roll back get a second full read.

Then read for correctness. Style comes last, and only where it is enforced by
the project or genuinely costs someone.

## The order of checks

Review in this order. Stopping early on a blocking correctness bug is not
"incomplete review" — it is the correct use of your time.

1. **Correctness** — does it do what it says, including at the edges?
2. **Security** — authz, injection, secrets, PII, unsafe deserialisation.
3. **Data & migrations** — irreversibility, locks, backfill, rollback.
4. **Concurrency & failure** — races, retries, timeouts, partial failure,
   idempotency, resource leaks.
5. **Tests** — does a test fail without this change? Does it cover the edges?
6. **Performance** — only where the change is in a hot path or adds a query.
7. **Design & naming** — will this be understandable in six months.
8. **Style & formatting** — enforce the formatter, do not argue about it.

## What to look for, concretely

**Correctness**

- Off-by-one and inclusive/exclusive bounds (`>= start` vs `> start`; ranges
  that double-count a shared boundary).
- Empty and single-element collections; `None` vs missing vs empty string; a
  function that returns `[]` where the caller expects `None`.
- Error paths: what happens if the second of three calls fails after the first
  succeeded? Is there a rollback, or a half-written state?
- Early return / continue inside a loop that should cover all items.
- Truthiness traps: `if value:` misses `0` and `""`; `if value is None:` misses
  an empty list. Say which one you mean.
- Timezone and clock: naive vs aware datetimes, `datetime.utcnow()` vs
  `now(timezone.utc)`, DST, leap seconds, and comparing a `time.time()` to a
  monotonic value.
- Money: floats for currency, rounding direction, currency mismatch.
- Equality on floats, and on objects that do not define `__eq__`.
- Default arguments evaluated once at import (the classic mutable-default bug).
- Integer division and overflow on fixed-width types.

**Security**

- Authorisation on **every** new entry point. A new route that reads a
  customer-owned resource without a tenant/ownership check is the most common
  serious finding. Check the *object* is owned, not just that the token is
  valid.
- Injection: string-built SQL, `shell=True` or `os.system` with any
  interpolated value, `eval`, `pickle.loads` on anything from a request,
  `yaml.load` without `SafeLoader`, template rendering of user input.
- Path traversal: joining user input to a filesystem path without normalising
  and checking containment.
- Secret handling: keys in source, in logs, in error messages, in a URL query
  string (it lands in access logs), or in a default parameter.
- PII: new fields in a log line, an analytics event, or an error payload.
- SSRF: a URL parameter fetched server-side, with redirects followed.
- Mass assignment: request body bound directly onto a model, so a client can
  set `is_admin` or `price`.
- Crypto: hand-rolled crypto, ECB mode, a hardcoded IV, `Math.random()` for
  tokens.

**Data and migrations**

- Is it reversible? Write the down path, or state explicitly that it is
  forward-only and why.
- Does it lock a hot table, rewrite it, or add a `NOT NULL` column with a
  volatile default? (Expand → backfill → contract.)
- Is the backfill batched, with a pause, and safe to re-run (idempotent)?
- Is the schema change deployed before or after the code that uses it, and does
  that ordering work in both directions during a rollback?

**Concurrency and failure**

- Shared mutable state across requests/tasks/threads; a module-level dict that
  grows without bound (a memory leak that only appears under load).
- Check-then-act (`if not exists: create`) without an atomic operation or a
  unique constraint.
- A timeout with no cancellation, or a retry without backoff — or a retry of a
  non-idempotent operation.
- Resources acquired without `try/finally` (or `with`): files, sockets, DB
  connections, locks, transactions. A leaked connection under an exception path
  is an outage an hour later.
- Error swallowed (`except: pass`) or logged and re-raised (double reporting).
- A circuit breaker, timeout, or retry that can be triggered by a malicious or
  careless client (a client can force a restart, a lock, or a cache flush).

**Tests**

- **The mutation check**: if you delete or invert one line of the new code,
  does a test fail? If not, the test is not testing the change.
- Edges and boundaries: empty, one, many, max, over-max, negative, zero,
  unicode, a very long string, a concurrent access.
- Error paths tested, not just the happy path.
- The test asserts behaviour, not implementation — a test that asserts a mock
  was called with `(1, 2)` breaks on every refactor and catches no bugs.
- Flake risk: sleeps, wall-clock assertions, real network, test-order
  dependence, shared mutable fixtures.
- Did an existing test get weakened or deleted to make this pass? A deleted
  test in a bug-fix PR is a red flag; ask what it was protecting.

**Performance** (only where it matters)

- An N+1 query in a loop — the single most common performance bug in review.
- A query in a loop at all; a missing index on a new filter/join column.
- A large allocation or copy in a hot path; a full table scan where a key
  lookup was intended.
- A lock held across I/O.
- Only raise these with evidence: an EXPLAIN, a benchmark, a count of rows.
  "This might be slow" is a nit unless the path is hot.

## Writing feedback: blocking vs nits

Label every comment so the author knows what to do before merging. This is the
whole difference between a review that gets acted on and one that gets
resented.

- **Blocking** — must change before merge. Correctness, security, data loss,
  an irreversible migration, a missing test for new behaviour. Say what the bug
  is and what to do.
- **Should fix** — not a bug, but a real cost: a missing edge case in a test, an
  unhandled error path, a name that will mislead. Ask for it in this PR or a
  tracked ticket, your choice.
- **Nit / non-blocking** — style, preference, optional. Prefix with `nit:` and
  do not block on it. Many reviewers batch nits into one comment at the end
  rather than scattering them.

Format every comment so it is actionable in one read:

```
[blocking] This returns 200 for a deleted order because the query filters
`deleted_at IS NULL` but the handler passes the raw id. The `GET /orders/{id}`
contract says 404. Add `AND o.deleted_at IS NULL` or map the empty result to
404 in the handler — which matches the other three handlers in this file?
```

The four parts: **label, the concrete problem (with the input that triggers
it), the impact, and the fix.** A comment a reviewer could paste into the
author's editor is a good comment. "Consider extracting this into a helper" is
not — it is an opinion about taste with no defect attached.

Rules:

- **Explain the why, once.** "This leaks the connection if `commit` raises —
  `conn` is not in a `with` block and `finally` is missing." The author can
  now judge whether you are right; that is the point of the comment.
- **Quote the line.** "In `checkout.py:88`" — not "somewhere in the handler".
- **One issue per comment.** Three issues in one comment means two get skipped.
- **Say when you are unsure.** "I may be wrong about the pool semantics here,
  but if `close()` doesn't release the connection on error, this leaks." A
  reviewer who admits uncertainty gets better feedback and better trust.
- **Praise sparingly and specifically.** "The idempotency handling here is
  exactly right — the `409` path returns the original order rather than
  creating a second one." Generic praise ("nice work!") signals that the rest
  of the review was not read carefully, and it devalues a real comment.

## Anti-patterns

- **Vague approval**: "LGTM" / "looks good" on a 900-line diff. If you cannot
  name the one thing that would have made it better, you did not review it —
  and the author learns that review is a formality.
- **Rubber-stamping**: approving because CI is green, the author is senior, or
  it is Friday afternoon. CI passes things that are wrong on the first Tuesday
  after a data change.
- **Style nitpicking as the main feedback.** If your review is 15 formatting
  comments and zero comments on the logic, the author learns your reviews are
  about formatting, and the next logic bug goes unremarked.
- **Rewriting the author's design in comments.** If the approach is wrong, say
  why in one or two sentences and make it a conversation, not a 40-comment
  diff-by-comment rewrite of their PR.
- **Reviewing your own diff before the author sees it** and then "discovering"
  the same issues in review. Do a self-review first, but be honest about which
  comments are yours.
- **Bikeshedding naming while a memory leak sits in the same function.** Order
  your review by severity, always.
- **"Looks good to me" on the tests you did not read.** Tests are code; read
  them with the same attention, and specifically check the mutation test.
- **Commenting on a line the author did not write**, in a drive-by, without
  separating it from the review of the change. It muddies the approve/block
  decision.

## The review, end to end

1. Read the **description and the linked issue** first. What is the intent?
2. Read the **diff without the code open**, in one pass, to form a model of the
   change. What did they add, remove, and change the shape of?
3. **Check the intent against the code**: does it do that, and only that?
4. **Trace the blast radius**: callers of changed signatures, consumers of
   changed fields/responses, config, migrations, and anything reading the data
   this writes.
5. **Run it.** If the repo has a fast test or a `make dev` + manual path, do
   the change yourself. A review where the reviewer ran the code finds
   different bugs from one where they did not.
6. Walk the checklist in order (correctness → security → data → concurrency →
   tests → performance → design → style) and collect comments, labelled
   blocking / should-fix / nit.
7. **Write the summary first**: what the change does, what you checked, and
   your verdict (approve / approve with nits / changes requested). Then the
   comments, blocking first, so the author can act top-down.
8. **If you are approving with reservations, say them explicitly** —
   "approve, but the migration is forward-only; I want a rollback plan in the
   PR before the next release". An unqualified approval you do not believe in
   is the worst outcome.

## Summary comment template

```markdown
**Summary.** Splits `checkout` into `auth`, `charge`, and `capture` so the
capture call can be retried independently. Adds `Idempotency-Key` handling to
`POST /orders`.

**Checked.** Authz on the new routes (each re-checks order ownership), the
retry path for non-idempotent calls, the migration (additive, nullable column,
no lock > ACCESS EXCLUSIVE), and the new tests (the mutation check passes: if
the idempotency branch is removed, `test_retry_creates_one_order` fails).

**Blocking (1).**
- [blocking] `capture.py:42` — see comment.

**Should fix (2).**
- [should] `charge.py:88` — timeout path is untested; a 2-line test would cover it.
- [should] The new `export` in `charge.py` is unused; remove it or use it.

**Nits (3, non-blocking).** `nit:` prefix, listed inline.

**Verdict.** Changes requested (one blocking item). Re-request after the
idempotency fix; I can re-review quickly once `make test` is green.
```

## Gotchas

- **Read the diff with whitespace changes hidden** (`git diff -w`) to see the
  real change, then with them shown to catch accidental reformatting that
  buries a behavioural edit.
- **`git log -p` on a file you do not understand** frequently answers "why is
  this here" faster than reading the code. The commit message, especially an
  `Revert` or a `Revert "…"` chain, tells you what went wrong the first time.
- **Large diffs hide large problems.** A 900-line diff with no
  description of which lines are the substance deserves a question before a
  line-by-line read.
- **A test that was deleted in a bugfix PR** is the highest-signal thing in
  the diff. Ask what it caught.
- **"It's covered by an existing test"** — verify by hand, or by mutation. It
  is often not.
- **Interfaces and defaults**: a changed default value is a behaviour change
  even if no call site is edited, because every caller that relied on the old
  default silently changes behaviour.
- **Reviewing generated code, lockfiles, and vendored diffs** — skim for
  unexpected changes (a hand-edited lockfile, a `node_modules` commit), do not
  line-review.
- **The second reviewer is not a rubber stamp either.** The failure mode is
  the second approval arriving without anyone reading; require the second
  reviewer's comments to be specific.
- **A review that takes longer than writing the code is usually a sign the
  change is too big.** Consider asking the author to split it.
- **Time-box and say so.** If a diff genuinely needs more time than you have,
  say "I need another pass on the migration; give me until tomorrow" rather
  than approving to clear your queue.

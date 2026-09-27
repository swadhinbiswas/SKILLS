---
name: systematic-debugging
description: Find the root cause of a bug with a disciplined loop - reproduce reliably, minimise the failing case, bisect the space, form one hypothesis and test it, and instrument at the boundary - instead of pattern-matching on the error text and patching symptoms. Use when something is broken, crashes, returns wrong results, is intermittently failing, or when a fix has not worked twice. Triggers on "debug this", "why is this failing", "it crashes", "it works on my machine", "flaky", "reproduce", "this is wrong", "the error is", "still failing after I fixed it", "stack trace".
compatibility: Language-agnostic. Concrete commands assume bash with set -euo pipefail discipline; adapt interpreter-specific flags to the project.
metadata:
  version: "1.0"
---

# Systematic Debugging

Debugging is a search problem: the space of possible causes is large, and
each experiment eliminates some of it. Most people lose by making changes that
eliminate nothing — a restart, a retry, a `sleep`, a `try/except` — and then
declaring victory when the symptom happens to disappear.

The discipline that works: **never change anything you cannot explain, and
never keep a change you cannot justify.**

## The loop

```
Observe → Hypothesise → Predict → Test → (confirmed? ) → Fix the cause → Verify
                ↑                                                    │
                └──────────── not confirmed → refine ─────────────────┘
```

Every iteration is one hypothesis. If you cannot state, in a sentence,
"If X is true, I expect to observe Y, here is how I will observe it", you are
not debugging — you are changing things.

## Step 0 — Read the actual error, completely

This is the highest-leverage step and the one most often skipped.

- Read the **whole** message, including the first line and every frame. The
  top of a stack trace is a symptom; the frame where the type or value first
  becomes wrong is the cause.
- Get the real error string, not your paraphrase of it. Copy it.
- Note the type, the value, the line, the file, and the request/job/user that
  triggered it.
- **Errors are not noise to be filtered.** Every `except`, every ignored exit
  code, every `stderr` redirect to `/dev/null` between the failure and you is
  evidence destroyed. Follow the error *backwards*: which line produced this
  object, which call produced that value.

| Instead of | Read it as |
|---|---|
| "TypeError: cannot read property 'id' of undefined" | something returned `undefined` where an object was expected — find which call returned it and why |
| "connection reset by peer" | the peer closed; the interesting question is what request was in flight and how long it was open |
| `ECONNREFUSED 127.0.0.1:5432` | nothing is listening on that port, or you are in the wrong network namespace / container |
| "500 Internal Server Error" | nothing — an unhandled exception on the server. Get the server-side error first. |
| `TimeoutError` | whoever timed out, with which deadline, after which operation. A timeout is a symptom of a dependency being slow or a lock being held. |
| `AssertionError: expected 3 to equal 4` | the *values* are the evidence. Which side moved, and when did it last change? |
| `NullPointerException` at line 88 | what is null there, and what invariant says it cannot be? |

**The most common wasted hour in debugging** is a "familiar" error that is
actually a different error with the same shape. `NullPointerException` on
Android, a `nil` map in Go, a `TypeError` in a JS callback — different causes,
same word. Do not pattern-match on the message.

## Step 1 — Reproduce it reliably

If you cannot reproduce it on demand, you cannot study it, and every other
step is guesswork.

- Get the exact command or request. The working local invocation is the
  reproduction; without it, nothing below is possible.
- Make it deterministic: fixed seed, fixed clock, fixed data, fixed
  environment, no dependency on network, no dependency on timing.
- **Write the reproduction down as a script** that a colleague can run.
- Record the **exact** environment: OS, runtime version, dependency
  lockfile, environment variables (redacted), and the git SHA.
- "Works on my machine" is a *clue*, not a rebuttal. The difference between
  the machines is the bug's location. Diff them: OS version, locale, timezone,
  line endings, CPU count, file-system case sensitivity, Python/Node/Java
  version, dependency versions, env vars, network vs localhost.

```sh
# a reproduction is a program, not a description
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export TZ=UTC LC_ALL=C
python3 -m pytest tests/test_checkout.py::test_rounding -x -q
```

If it reproduces *only* sometimes, go to Step 2 anyway — intermittency is
information about the shape of the bug (ordering, timing, shared state), not
an obstacle.

## Step 2 — Minimise

A 400-line reproduction is better than a bug report, but a 5-line one is
better still. Minimisation narrows the space of hypotheses faster than
reading does.

Bisect the input along every axis until each further reduction stops helping:

- **Halve the input.** Does a 1000-row file fail but 500 not? Does a
  30-second script fail but 3 not? There is usually a threshold, and the
  threshold is the clue.
- **Halve the code path.** Comment out half the function. Remove the feature
  flag. Replace the real dependency with a stub.
- **Halve the environment.** Local vs container, one core vs all, no network,
  single thread.
- **Remove the parts that are not the symptom.** Keep the parts that make it
  fail.

Record the minimised form in the issue. A minimised case is something you can
re-run 500 times in a loop, which is what makes the next steps possible.

## Step 3 — Bisect the space

Decide which dimension the cause lives in *before* instrumenting. Guessing
wrong costs more than a few minutes of thought.

| Dimension | Bisect with |
|---|---|
| Which commit introduced it | `git bisect run ./repro.sh` (see `git-bisect-debugging`) |
| Which input causes it | halve the input; delta-debug the data; compare failing and passing cases field by field |
| Which layer | collapse layers: real DB → local fixture, real network → loopback, real service → stub. Find the highest layer where it still fails. |
| Which thread / request / call | one thread, one request, no concurrency |
| Which branch of the code | `coverage`-guided: instrument, run, look at the difference between the failing path and a passing run |
| Time | add timestamps at boundaries and compute gaps; find the interval that is long |

The layer-collapse move is the most powerful and most skipped. If it still
fails with a fake HTTP server, it is not networking. If it passes with a
fixture and fails with the real DB, it is data or schema, not code.

## Step 4 — One hypothesis, one test

Write it down before testing:

> **Hypothesis:** `total` is `None` because `discount_codes` is absent from the
> cart payload, and `apply_discount` indexes it without a default.
> **Prediction:** if I send a cart with no `discount_codes` key, it raises
> `TypeError: 'NoneType' object is not subscriptable` at line 88; adding the
> key (even `[]`) makes it pass.
> **Test:** run the reproduction with and without the key.

Discipline that matters:

- **One hypothesis at a time.** Two simultaneous changes give you a result
  you cannot attribute.
- **Prefer a prediction over an inspection.** "Add a log and look" is weaker
  than "assert `x is None` here; if the assertion does not fire, my
  hypothesis is wrong." An assertion that *fails* proves something in one run.
- **A test that cannot fail is not a test.** A `console.log` that you then
  have to eyeball is weaker than an `assert` that exits non-zero.
- **Let the hypothesis be wrong.** If the prediction fails, the hypothesis is
  wrong. Do not adjust the evidence; discard the hypothesis. Two or three
  discarded hypotheses are normal; a discarded hypothesis you never wrote
  down is a day lost.
- **Never instrument production without approval.** Logs at high volume change
  behaviour, cost money, and can leak data. See
  `production-incident-response`.

### The three cheapest experiments

In order, before reading any source:

1. **Flip the condition.** Comment out the line you suspect, or make the
   branch take the other path. If the symptom disappears, the line is
   *involved* — not necessarily the cause, but you have a handle.
2. **Print the value at the boundary.** Not "the value is wrong" but the
   actual value, its type, and where it came from. `repr()` in Python,
   `%#v`/`%T` in Go, `JSON.stringify` in JS, `%+v` in Rust.
3. **Add an assertion or a hard stop.** Fail loudly at the point where the
   invariant is first violated, then let the stack tell you how it got there.

```python
# Flip the condition: does the error disappear?
#   if user.is_admin:  ->   if False:
# Flip the data: does an empty list make it pass?
#   assert discount_codes is not None, repr(payload)
```

## Step 5 — Fix the cause, and only the cause

The failure mode of most debugging is **fixing symptoms**: catching the
exception, adding a retry, adding a `sleep`, adding a `None` check at the
crash site, restarting the process, widening a timeout, bumping a pool size.
These make the error go away without making the bug go away, and they are the
reason a codebase accumulates `if x is None: return` in twenty places.

Test for it: **would this fix still be needed if the underlying bug were
fixed?** If yes, you have added a workaround, not a fix.

Symptom fixes are correct in exactly two cases: as a **defensive guard** at a
real boundary (validating external input, handling a documented failure mode)
with a clear comment, or as a **temporary mitigation** during an incident,
labelled as such and tracked to removal.

A real fix:
- explains the symptom, and
- is the narrowest change that removes the cause, and
- comes with a test that fails before it and passes after, and
- leaves the code simpler or no worse.

## Step 6 — Verify

- [ ] The minimised reproduction now passes
- [ ] The original reproduction passes
- [ ] The test fails without the fix (`git stash` the fix and re-run — do this,
      it is the only way to know the test tests anything)
- [ ] The test covers the *cause*, not the symptom: a test asserting
      "returns 0 instead of raising" will pass with every symptom fix
- [ ] Neighbouring cases still work — run the full suite, and the tests for
      the same module
- [ ] You can explain, in one sentence, why it was wrong and why the fix is
      right

## Instrumentation, in order of preference

| Tool | Use for | Cost |
|---|---|---|
| **Assertion** | proving a hypothesis at a point | none in production; throws |
| **Debugger / REPL** | single-stepping, inspecting live state | interactive only |
| **Structured log with a correlation id** | "which request did this happen in" | low |
| **Metric / counter** | "how often, and when" | low |
| **Trace / span** | "where in the call chain, across services" | moderate |
| **Heap or allocation profile** | growth, retention, churn | high |
| **Print / printf** | last resort; local only | noisy |

Logs should be **structured and correlatable**: emit a request id, a session
id, a user id, and the operation, and make it greppable. Unstructured
`print`-style logging cannot be queried, and you will end up reading it in
the wrong order.

Where to put a log line: at the **boundary** where data enters the system
(request parsed, message consumed, file read) and at the **decision point**
where behaviour branches. Logging inside a hot loop is not debugging, it is
a denial of service.

## Gotchas

- **"It stopped happening" is not a fix.** Without a cause, the next
  environmental change brings it back. A restart that fixes it is a data point
  about state, not a resolution.
- **Two bugs can look like one**, and fixing the visible one reveals the
  second. If the symptom *changes* after a fix rather than disappearing, you
  probably had two problems.
- **Heisenbugs get worse when you add logging.** A log statement changes
  timing, memory layout, and I/O interleaving. If adding a log makes the bug
  disappear, you have a race condition or a memory-safety issue — a
  genuinely valuable finding.
- **The error you see is not necessarily where the cause is.** Null
  dereferences surface at the use, hundreds of lines from the assignment.
  Exceptions surface at the top of the stack, not the bottom. Follow the data,
  not the stack.
- **`catch (Exception) { log.error(...) }` turns a bug into a silent
  wrong-answer bug.** Broad catches hide the cause and change the symptom.
  Catch what you can handle; let the rest propagate with its stack.
- **A retry that "fixes" it is a timing bug you have not found.** Retries hide
  races, stale caches, and uncommitted writes.
- **Reinstalling dependencies, clearing caches, and deleting lockfiles are
  not debugging steps.** They change the experiment. Record which ones you
  tried — if the problem went away, you have learned the environment matters,
  not why.
- **"Fixed on my machine" usually means the reproduction was not the same.**
  Diff the environments (Step 1) instead of shipping.
- **Changing several things before re-testing** is the most expensive habit
  in debugging: you have lost the ability to attribute the result. If you are
  desperate, change things one at a time anyway and record each attempt.
- **When a fix is hard to find, it is often because the cause is not in the
  code you are looking at.** Check: the build artefact actually being run
  (is the deployed image the one you built?), the config actually in effect
  (not the file on disk — the env var that overrides it), the dependency
  version actually resolved, the feature flag state at runtime.

## When to stop and escalate

- Three or more hypotheses tested and discarded with no narrowing → the model
  of the system is wrong. Go back to Step 1 and get a cleaner reproduction
  from a fresh pair of eyes.
- The bug is not reproducible anywhere, including production → it is
  environmental or data-dependent. Get the actual failing input and the exact
  environment; do not guess.
- The fix would require changing an interface other teams depend on → write
  up what you know, the evidence, and the options, and get a decision. Do not
  unilaterally change a contract mid-incident.
- The system is live and people are affected → switch to
  `production-incident-response` and mitigate first. Come back to root cause
  after users are unblocked.
- You need to change production state to test (a config flag, a data fix, a
  restart) → get explicit human approval. See
  `production-incident-response`.

## The anti-pattern, in one list

- Patching the symptom and calling it a fix
- Changing more than one thing at a time
- Reading code instead of running code
- Guessing the cause from the error message alone
- Keeping a change you cannot explain
- Declaring victory when the symptom disappeared
- Adding a retry, a sleep, or a wider timeout
- Declaring it unfixable after one attempt

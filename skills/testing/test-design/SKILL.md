---
name: test-design
description: Design tests that are worth keeping - behaviour over implementation, where unit vs integration vs contract vs E2E tests genuinely belong, what not to test, AAA and given-when-then structure, test names that document themselves, mocking limits, and error-path coverage. Use when adding tests, when a suite is slow or flaky, when coverage is high but bugs still ship, or when someone asks "how many tests do we need". Triggers on "flaky test", "too many mocks", "test coverage", "E2E test", "refactor breaks tests", "test is brittle".
compatibility: Language-agnostic; examples in Python (pytest) and TypeScript (Jest/Vitest).
metadata:
  version: "1.0"
---

# Test Design

A test suite is an asset only if changing behaviour correctly breaks it and
changing implementation correctly does not. Every rule below is that idea
applied.

## The decision that matters: what to test

Test the **contract** of a unit — the promises it makes to callers — and
nothing else.

| Layer | Tests | Don't test |
|---|---|---|
| Pure logic, domain rules, parsing, formatting | Every branch including boundaries and error paths | Framework plumbing |
| Service with real collaborators behind an interface | Request → observable state change; authorisation and validation failures | That the DI container resolved the class |
| HTTP/GraphQL handler | Status codes, response shape, error body, side effects | Which helper called which helper |
| Database access | Migrations apply; constraints actually reject bad rows | SQL string text |
| UI component | What the user sees and can do, at the DOM/a11y level | Component instance state, hook call counts, snapshots of markup internals |

### What not to test, ever

- **Getters, setters, `__init__` field assignment.** Zero value, real
  maintenance cost.
- **Framework code.** Your test is not asserting that Django resolves a URL.
- **Constant returns and pass-through delegation.** `return self.x.x()`
  has no branch worth covering.
- **Private methods directly.** A private method is an implementation detail;
  reach it through the public method. It is fine to be covered *incidentally*.
- **Exact log text, internal state, call ordering of collaborators.** These
  change without behaviour changing, so they cause failures that mean nothing.
- **"Coverage" for its own sake.** Executed line ≠ tested.

### When a test is a liability

Delete or rewrite it, do not just fix it, when:

- It fails intermittently and nobody can name the cause after 20 minutes.
- It asserts on a private field or exact internal call sequence.
- It duplicates another test; the first failure found is the useful signal and
  the rest are noise.
- It needs a hard-coded sleep, a fixed date, or a specific execution order.
- It breaks on every refactor but has never caught a real bug.
- It is slow (>1s) and covers logic that a fast unit test would cover better.
- Its name no longer describes what it asserts.

Suite debt compounds: every 10% of tests that are noise is permission to
ignore the suite, and ignored suites have a 100% pass rate with zero bugs found.

## Structure: AAA and given-when-then

Use AAA when the code is procedural. Use given/when/then when the *scenario*
is the interesting part — a table cell that reads as a spec, a failure that
names its own precondition.

```python
def test_expired_token_is_rejected(self):
    # given
    token = issue_token(user, ttl=timedelta(seconds=-1))

    # when
    result = authenticate(token)

    # then
    assert result == Err(AuthError("token expired"))
    assert result.error.code == "token_expired"   # stable code, not the message
```

```ts
it('returns 409 when the seat is already taken', async () => {
  // given
  await bookSeat(seatId, userA.id);

  // when
  const res = await request(app).post('/bookings').send({ seatId, userId: userB.id });

  // then
  expect(res.status).toBe(409);
  expect(res.body.code).toBe('SEAT_TAKEN');
});
```

Rules that follow from the structure:

- **One behaviour per test.** One failing assertion cluster, one fix.
- **No logic in the test.** No loops building inputs, no `if`, no arithmetic you
  would have to debug. Move that into a builder or parametrise it.
- **No `sleep`.** Use fake clocks, event waiters, or poll with a deadline.
- **Assert on values, not on the absence of exceptions** where a value is
  available. `assert result.status == "deactivated"` beats "did not raise".
- **Never assert on `mock.called`** when you can assert on the outcome. The
  outcome test survives the refactor that replaces the call with a direct call.
- **A test with no assertions is either dead code or a missing assertion.**

## Test names are the documentation

A name should let someone delete the body and still know what is guaranteed.
Name by behaviour and condition, not by method under test.

| Weak | Strong |
|---|---|
| `test_price` | `test_applies_member_discount_before_tax` |
| `testUser` | `test_renders_empty_state_when_cart_has_no_items` |
| `test_error` | `test_returns_422_when_email_is_not_a_valid_address` |
| `test_2` | `test_retries_3_times_then_propagates_the_last_error` |

For BDD frameworks use full sentences; for everything else use
`test_<condition>_<expected>` with underscores (languages without spaces).

One behaviour per test also means the **test name is the assertion**: if you
cannot express the expectation in the name, the test is doing two things.

## The pyramid, honestly

The classic triangle is roughly right about *shape* and wrong about *why*.

| Layer | Share of the suite | Runtime | What breaks it |
|---|---|---|---|
| Unit | ~70% | ms | refactors, dependency changes |
| Integration (real DB/HTTP) | ~20% | 100ms–10s | dependency version bumps, CI parallelism |
| Contract (API/schema compatibility) | ~5% | ms–s | breaking API changes by teammates |
| E2E (browser/full system) | ~5% | minutes | everything, and it is the least informative failure |

- **The shape comes from cost, not virtue.** A unit test is fast and local, so
  there can be thousands. An E2E test is slow and local-only in its failure, so
  there can be ten.
- **E2E tests diagnose worse than they catch.** A failure says "checkout is
  broken", not which field or which function. Keep them for the handful of
  journeys whose failure means an outage, and push the detail down into
  integration and unit layers.
- **Contract tests are what lets you delete the E2E matrix.** If each consumer
  records what it needs, you can check compatibility per-commit instead of
  waiting for a full E2E run to go red.
- **Injected bugs live in the layers you skipped.** The most productive thing
  you can do when choosing a layer is to inject a bug into the code and check
  that some test fails. A suite that survives mutation testing has a layer
  distribution problem.

## Over-mocking

Mocks are a tax. Every mock is a lie about the world that must be maintained
by hand, and it dies the moment the code is refactored.

- **Mock only what crosses a process or is nondeterministic**: network, clock,
  randomness, payment provider, mailer, queue. Prefer a real in-memory or
  containerised instance of the thing (SQLite, `fakeredis`, a real Postgres
  container) over a mock — it is faster to set up than you think and it cannot
  drift.
- **Do not mock the class under test's own collaborators' internals.** Assert
  on the returned value or the persisted state.
- **A mock that returns another mock is unreadable.** If your test needs
  `mock.return_value.mock.return_value.side_effect`, the real code is hard to
  use; that is a design finding, not a test problem.
- **Do not copy the implementation into the expectation.** `assert
  result.total == sum(item.price for item in items)` reimplements the logic and
  breaks whenever it changes.
- **Patch where it is looked up, not where it is defined.** In Python,
  `patch("mypkg.service.Client")` (where used) is correct;
  `patch("mypkg.client.Client")` is not, and fails with `AttributeError` the
  moment someone refactors the import.
- **Over-specified mocks are the main cause of "tests break on refactor".** If
  a test asserts `assert mock_db.execute.call_args_list == [call(...), call(...)]`,
  the test is a second copy of the code.

## Error paths

Untested error paths are the most common source of production incidents that
were "obvious in hindsight", because the code is written once and only the
happy path is ever exercised by hand.

Cover, for every unit that can fail:

1. **Each distinct failure the code can produce** — an explicit error return, a
   rejected promise, a raised exception. Not "errors" in general; each one.
2. **The fallback path** — defaults, partial results, degraded mode.
3. **Boundaries** — 0, 1, max, max+1, negative, empty string/collection, the
   largest value the type allows, and one past it. Parametrise these.
4. **Invalid input at the boundary that owns it** — validation belongs in one
   place; test it there and do not re-test it in every caller.
5. **Partial failure** — the third item in a batch failing must not corrupt the
   first two. Test atomicity, not just success.

Prefer asserting on a **stable error code** over an error message: codes are
part of the contract; messages are for humans and get reworded.

```python
# One test per failure mode. The name is the spec.
@pytest.mark.parametrize("error", [AuthError, RateLimitError, UpstreamTimeout])
def test_verify_rejects_on_transient_failures(error):
    # given
    verifier = Verifier(stub=StubVerifier(raises=error))

    # when
    result = verifier.verify(Credentials(token="t"))

    # then
    assert result.is_error
    assert result.error.code in TRANSIENT_CODES
```

## Safety

- **Never make the suite hit production or any shared real environment.** Test
  credentials and endpoints must be guarded; assert in the harness that the
  target host is not a production hostname before running.
- **Never let a test send real email, real SMS, or real payments.** Use
  provider sandbox modes or a local fake; assert the send was routed to the
  fake, and fail the test if it was not.
- **Never weaken or delete a failing test to make CI green** without a written
  issue and an owner. `xfail` and `skip` must carry a reason and a tracking
  link, and `xfail(strict=True)` so a fixed bug fails the build until someone
  removes the marker.

## Gotchas

- **"No tests on main" is the most common cause of untested critical paths.**
  Coverage of `main` measures the last deploy, not the PR.
- **A test suite that is 5 minutes long gets run less, and run partially.**
  Split the slow tier into a separate job so the fast gate stays fast.
- **Asserting on a rendered HTML snapshot** fails on every whitespace change
  and teaches the team to regenerate snapshots without reading them. Assert on
  roles and text instead, or keep snapshots at a single coarse level.
- **Testing the happy path only** is the shape of a green suite that misses
  every incident. If you have one happy-path test and no error-path tests, the
  suite is decoration.
- **Tests that depend on execution order pass in one order and fail in CI**,
  where the order differs. Randomise the order (`pytest-randomly`, or
  `pytest -p no:randomly` to turn it off) to find them, and compare two fixed
  seeds.
- **A test that passes when run alone and fails in the suite** is shared state:
  a module-level mutable, a singleton, a cache, a leaked thread, or a temp
  file. Fix the state, not the order.
- **`assert` is stripped in optimised Python (`python -O`).** Never use bare
  `assert` for control flow or side effects in library code.
- **Testing what a mock returns** proves the mock returns what you told it to.
  When in doubt, delete the mock and see if the test still passes.

## Files

- `references/test-smells.md` — catalogue of specific smells with the refactor
  for each, plus the review checklist for a PR that adds tests. Read it when
  reviewing a test PR or when a suite has become slow and flaky.

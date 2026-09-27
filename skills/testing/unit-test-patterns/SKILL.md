---
name: unit-test-patterns
description: Concrete unit test patterns with working code in Python (pytest) and TypeScript (Jest/Vitest) - table-driven and parametrised tests, given-when-then, builder and fixture helpers, custom assertions, deterministic time and randomness via injection, async testing, snapshot discipline, and property-based testing basics. Use when writing or refactoring unit tests, when a test suite is repetitive or slow, when tests fail only at midnight or only once in twenty runs, or when someone mentions flaky, nondeterministic, or timeout-related test failures. Triggers on "parametrize", "pytest.mark.parametrize", "it.each", "freezegun", "fake clock", "hypothesis", "jest fake timers", "test flakiness".
compatibility: Examples target Python 3.11+ with pytest, and TypeScript with Jest or Vitest. Property-based examples use Hypothesis (Python) and fast-check (TypeScript).
metadata:
  version: "1.0"
---

# Unit Test Patterns

Mechanics for writing many small tests cheaply, and making the two things that
break them (time, randomness) controllable by construction.

## Table-driven / parametrised tests

The default for any test with more than two cases. One body, many named cases;
failures are reported per case, not per suite.

```python
import pytest

@pytest.mark.parametrize(
    ("total_cents", "member", "expected_cents"),
    [
        pytest.param(0, False, 0, id="zero-total-no-discount"),
        pytest.param(10_000, False, 10_000, id="exact-cent-boundary"),
        pytest.param(10_000, True, 9_000, id="member-10pct"),
        pytest.param(1, True, 0, id="member-rounds-down-to-zero"),
        pytest.param(-500, False, None, id="negative-rejected"),
    ],
)
def test_order_total(total_cents: int, member: bool, expected_cents: int | None):
    # given
    order = Order(total_cents=total_cents, member=member)
    # when
    result = order.charged_total()
    # then
    assert result.cents == expected_cents
```

```ts
it.each([
  { total: 0, member: false, expected: 0 },
  { total: 10000, member: false, expected: 10000 },
  { total: 10000, member: true, expected: 9000 },
  { total: 1, member: true, expected: 0 },
])('charges $expected for total=$total member=$member', ({ total, member, expected }) => {
  const order = new Order({ totalCents: total, member });
  expect(order.chargedTotal().cents).toBe(expected);
});
```

- **Give every case an `id`.** An unnamed `[-500, False, None]` in a failure
  report is unusable.
- **Parametrise the boundary list, not the arithmetic.** The table holds data;
  the body holds no `if`.
- **Keep one axis per test.** "Discount" and "tax" belong in two tests even if
  they share a fixture; combined they make a 2D matrix and a failure tells you
  only that "some combination is wrong".
- **Test the error column with `pytest.raises` inside the parametrised body**
  only when the error is the same *kind*; different exceptions per case is a
  sign the cases are different tests.
- **Do not parametrise through a shared mutable object.** Build fresh input
  per case or copy it, or the second case inherits the first case's mutations.
- If you find yourself generating cases in Python (`for x in range(1000)`), you
  want property-based testing instead.

## given-when-then

Same three steps as AAA, but named, and used where the *scenario* is the
documentation. BDD frameworks (pytest-bdd, Cucumber, Playwright) and table
tests both render well this way.

```ts
describe('Order.discountFor', () => {
  it('returns zero for an order with no lines', () => {
    // given
    const order = Order.empty();
    // when
    const discount = order.discountFor(Campaigns.SPRING);
    // then
    expect(discount.cents).toBe(0);
  });
});
```

Keep given/when/then sections visibly separated with comments or blank lines.
Once the `// when` block grows three calls, the test is asserting several
behaviours — split it.

## Builders and fixtures

The rule: a fixture that takes five positional arguments to be readable is a
builder. Keep builders in test-support code, not in production modules.

```python
# tests/builders.py
from dataclasses import dataclass, field

@dataclass
class UserBuilder:
    _email: str | None = None      # None = generate a deterministic default
    _verified: bool = True
    _roles: set[str] = field(default_factory=lambda: {"member"})

    def with_email(self, email: str) -> "UserBuilder":
        self._email = email
        return self

    def unverified(self) -> "UserBuilder":
        self._verified = False
        return self

    def build(self) -> User:
        # Derived defaults are computed at build() time, not at construction.
        return User(
            email=self._email or f"user{id(self)}@example.test",
            verified=self._verified,
            roles=frozenset(self._roles),
        )

def a_user(**overrides) -> User:
    """Test-only shorthand. Never import this from production code."""
    return UserBuilder().build().replace(**overrides)
```

```ts
// tests/builders.ts
let seq = 0;
export function aUser(over: Partial<User> = {}): User {
  return {
    id: `u_${++seq}`,           // unique per call: no cross-test collisions
    email: 'user@example.test',
    verified: true,
    roles: ['member'],
    ...over,
  };
}
```

- **Deterministic defaults, explicit overrides.** A counter-based unique id
  avoids cross-test collisions; `uuid4()` does not (and makes failures
  irreproducible).
- **Builders return new objects; never mutate a shared instance.** Return `self`
  from `with_*` for fluency, but do not hand out the builder's own collections.
- **Factories, not fixtures, for data with identity.** A pytest fixture is
  per-test lifecycle; a builder is per-object construction. Mixing them makes
  "why is this row already in the DB?" unanswerable.
- **Never import builders from `src/`.** It is the fastest way to make a
  builder part of the shipped API.
- **Use a fixture for expensive shared setup** (a client, a container, a
  loaded fixture file), and a builder for the object under test's inputs.

## Custom assertions

Domain vocabulary beats repeated field-by-field comparison. Put them in
`tests/matchers.py` / a jest `expect.extend`, and make the failure message say
what was expected.

```python
def assert_api_error(result, code: str, *, status: int | None = None):
    assert result.is_error, f"expected {code}, got success: {result.value!r}"
    assert result.error.code == code, (
        f"expected error code {code!r}, got {result.error.code!r} "
        f"({result.error.message!r})"
    )
    if status is not None:
        assert result.error.status == status

# usage
assert_api_error(result, "SEAT_TAKEN", status=409)
```

```ts
expect.extend({
  toBeWithin(received: number, target: number, tolerance: number) {
    return {
      pass: Math.abs(received - target) <= tolerance,
      message: () => `expected ${received} to be within ${tolerance} of ${target}`,
    };
  },
});
```

Rules: assert **one domain concept per helper**; never swallow the underlying
diff — include both actual and expected in the message; make every helper fail
loudly with a useful string, because that string is the whole value of the
test. Do not build helpers that branch on the framework's internals.

## Time, deterministically

Never let production code call `now()` directly. Inject a clock, and in tests
use a fake you control.

**Design (do this once, in production code):**

```python
from typing import Protocol
from datetime import datetime, timedelta, timezone

class Clock(Protocol):
    def now(self) -> datetime: ...

class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

class FixedClock:
    def __init__(self, start: datetime): self._now = start
    def now(self) -> datetime: return self._now
    def advance(self, delta: timedelta) -> None: self._now += delta
```

Always return **timezone-aware UTC** datetimes. A naive `datetime.now()` in
production code is a bug that only appears in one timezone.

**Python:** `freezegun` for patching third-party `datetime` calls you cannot
inject, `time-machine` as the faster alternative; both are test-only
dependencies, not runtime ones.

```python
def test_token_expires_exactly_at_ttl():
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    svc = TokenService(clock=clock, ttl=timedelta(hours=1))
    token = svc.issue(user)
    assert svc.verify(token).is_ok          # t=0
    clock.advance(timedelta(minutes=59, seconds=59))
    assert svc.verify(token).is_ok          # t=59:59 - still valid
    clock.advance(timedelta(seconds=1))
    assert_api_error(svc.verify(token), "token_expired")
```

**TypeScript:** inject a `now(): Date`, and use fake timers for third-party
libraries.

```ts
it('refreshes the access token 60s before expiry', () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
  const token = new TokenService({ ttlMs: 3_600_000 }).issue(user);
  vi.advanceTimersByTime(3_600_000 - 60_000);
  expect(token.needsRefresh()).toBe(true);
  vi.advanceTimersByTime(60_001);
  expect(token.isExpired()).toBe(true);
  vi.useRealTimers();      // in afterEach, always
});
```

GOTCHA: `jest.useFakeTimers()` fakes `Date`, `setTimeout`, `setInterval` and
`process.hrtime` by default but **not** `queueMicrotask`, real I/O, or
`performance.now()` unless you list them. In Jest, restore in `afterEach` or
the *next* test in the file runs with a frozen clock and hangs.

GOTCHA: freeze time once per test at a **fixed, readable instant**
(`2026-01-01T00:00:00Z`), not `Date.now()`. A fixed instant makes the failure
readable and reproducible.

GOTCHA: patching time but not the sleep it implies produces a test that waits
forever. If the code polls on a timer, advance the timer in a loop until a
condition holds, with a bound.

## Randomness, deterministically

Inject a `Random`-like object (or a seed) at the boundary; do not patch
`random` globally in library code.

```python
class ScriptedRandom:
    def __init__(self, values): self._values = iter(values)
    def choice(self, seq): return next(self._values)
    def randint(self, a, b): return next(self._values)

def test_retry_uses_the_exponential_delays_we_asked_for():
    rnd = ScriptedRandom([0, 2, 4])
    delays = next_delays(attempt=3, base=1, cap=8, rnd=rnd)
    assert delays == [0, 2, 4]
```

When you genuinely need statistical behaviour, assert on a **property**
(over 1000 draws, all values in range) or use a fixed seed and assert the exact
output — never assert on one random draw.

## Async testing

- **Await the promise you made; do not sleep.** The rule for Python's
  `asyncio` and JS's promises alike.
- **Assert on the settled value, not on "did not hang".** A timeout failure
  says nothing about correctness.
- **Handle rejections explicitly** (`pytest.raises`, `await expect(...).rejects`,
  or a try/except that fails if nothing is raised). An unhandled rejection in
  Python prints a warning and can pass the test.
- **Do not mix `asyncio.run` per test with a module-scoped event loop.** Fixtures
  bound to different loops fail with
  `RuntimeError: Task got Future attached to a different loop` /
  `attached to a different loop`. Use one loop (e.g. `pytest-asyncio` with
  `loop_scope="session"` and session-scoped async fixtures) or
  `anyio`.
- **Bound every await with a timeout** in a test, so a hang fails in seconds:
  `await asyncio.wait_for(do_work(), timeout=5)`,
  `await expect(promise).resolves` under Jest's default 5s, or
  `testTimeout` in Vitest.
- **Test cancellation**: spawn, cancel, and assert the cleanup ran. Missing
  cancellation tests are why services leak connections.
- In JS, an unawaited promise is a silent failure. `expect.hasAssertions()` (or
  a strict lint rule) catches tests that ended before the async work resolved.

## Property-based testing

Use it where enumerating cases is hopeless: parsers, codecs, money/quantity
rounding, schedulers, state machines, serialisation round-trips, validators.

**Python (Hypothesis):**

```python
from hypothesis import given, strategies as st

@given(st.integers(min_value=0, max_value=10**9))
def test_split_cents_never_loses_money(cents: int):
    # property: the parts always add back up to the whole
    whole, remainder = split_cents(cents, 3)
    assert whole * 3 + remainder == cents
    assert 0 <= remainder < 3

@given(st.lists(st.builds(Order)))
def test_totals_are_additive(orders):
    assert Order.combine(orders).total_cents == sum(o.total_cents for o in orders)
```

**TypeScript (fast-check):**

```ts
import fc from 'fast-check';

it('never loses money when splitting', () => {
  fc.assert(
    fc.property(fc.integer({ min: 0, max: 1e9 }), (cents) => {
      const { whole, remainder } = splitCents(cents, 3);
      return whole * 3 + remainder === cents && remainder >= 0 && remainder < 3;
    }),
    { numRuns: 200 },
  );
});
```

- **State the property as one sentence** in the test name; that sentence is
  the specification.
- **Always keep the deterministic unit tests.** Property tests find edge
  cases; they do not tell a reader what the function does.
- **Bias the generator** toward the boundaries you care about
  (`st.one_of(st.integers(), st.just(0), st.just(2**31), st.just(-1))`); uniform
  random rarely hits the interesting values.
- **Set a seed in CI so a failure is reproducible.** Hypothesis prints the
  failing example and falsifying seed; record it as a regression test with that
  value. Run the found case deterministically first in CI (`--hypothesis-seed`).
- **A shrinking loop that takes minutes** means the property is badly stated.
  Shrink slowly; expect a counterexample in seconds.

Snapshot rules: serialise a value, not rendered markup; freeze time and
randomness first; one snapshot per test; and never let CI regenerate snapshots
automatically (`--ci`, or a `--update` flag in the pipeline), because that turns
the test into a rubber stamp. A 5000-line snapshot diff is a failed review, not
a review item.

## Gotchas

- **`assert` in Python is removed under `python -O`.** Use it in tests, never
  for control flow in library code.
- **Floating-point equality.** Assert with `pytest.approx` / `toBeCloseTo`, and
  do money in integer minor units; floating point equality tests fail on a
  different CPU and look like flakes.
- **Dict/set ordering assumptions.** Assert on the whole value for ordered
  collections, on a sorted list for sets, and never on `repr()` of an object
  whose repr you do not control (memory addresses leak into snapshots).
- **A `for` loop inside one test gives you one failure id for N cases.** Use
  parametrisation so the failing input is named.
- **Idempotence bugs hide in builders that mutate defaults**
  (`field(default_factory=list)` is correct; `=[]` is shared across instances).
- **Test doubles that are not reset between tests** (a module-level `mock`
  auto-specced once at import) leak call history and make the second test in
  the file fail. Prefer a fixture that constructs fresh doubles per test.
- **Over-parametrised builders hide the case that matters.** If every test uses
  `UserBuilder().with_email(...).with_roles(...)`, the object model is harder
  to read than the literal it replaced. Use the literal for 1–2 fields and a
  builder for 6+.
- **Refactoring production code to make it testable** is allowed and good; but
  if every public function needs a `*_for_test` wrapper, the seam is in the
  wrong place — inject the dependency instead.
- **Timing assertions inside a unit test** (`elapsed < 0.1`) are load-bearing
  flakes on shared CI runners. Move them to a benchmark job with a documented
  tolerance, not to the merge gate.

## Files

- `references/pattern-recipes.md` — copy-paste-ready snippets for the less
  common patterns: custom pytest plugins/fixtures, Jest `setupFilesAfterEach`
  hygiene, async property tests, and golden-file helpers. Read it when a
  specific pattern you need is not shown above.

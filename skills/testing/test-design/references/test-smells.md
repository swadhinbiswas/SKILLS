# Test smells and their fixes

Each entry: the smell, why it costs, the refactor. Read this when reviewing a
test PR, or when a suite has become slow and flaky and you need a migration
plan rather than a fix.

## 1. Asserting on call history

```python
mock_client.send.assert_called_once_with("user@example.com")
mock_client.reset_mock.assert_called_once()
```

Every call in the implementation is now frozen. Adding a log line inside the
code under test breaks the test with no behavioural change.

**Fix:** assert on the effect the caller can observe — the row written, the
request body the caller received, the returned value. Use a fake that records
state (`FakeMailer.sent`) rather than a mock that records calls, and assert on
`len(fake.sent) == 1` and `fake.sent[0].to`.

## 2. Multiple behaviours in one test

```python
def test_user_creation():
    user = create_user(...)
    assert user.name == "x"
    assert user.is_active
    assert len(mailer.outbox) == 1
    audit.log.append("created")
    assert db.count("users") == 1
```

If the second assertion fails you cannot tell which guarantee broke, and
fixing one may break another.

**Fix:** split along behaviours. Related assertions that describe *one*
guarantee (`returns a 422 and a `VALIDATION` code`) stay together. If you
cannot name the split, you cannot name the tests.

## 3. Arrange-act buried in the test body

```python
def test_discount():
    total = 0
    for p in products:
        total += p.price * p.qty * (0.9 if user.member else 1.0)
    assert calc(total, promo) == 84.10
```

The setup contains the same arithmetic as the production code, so the test is
a second implementation that fails for the wrong reason.

**Fix:** use a builder or a parametrised table so each case is one line of
inputs and one line of expected output. The inputs should look like data, not
like code.

## 4. Conditional logic in tests

```python
if len(items) > 0:
    assert ...
```

The branch itself is untested, and the test's meaning changes with data.

**Fix:** `@pytest.mark.parametrize` (or `it.each`) so each branch becomes its
own named case, and the parametrised ids show up in the failure output.

## 5. Sleeps and timing assumptions

```python
retry(delay=0.5)      # test sleeps to let the worker finish
assert job.done
```

This is the single largest source of CI-only flakes. It is also slow: the
suite now pays 0.5s per case whether or not the work takes 0.5s.

**Fix, in order of preference:** (1) make the code return a handle (a future, a
channel, a queue) and await the specific event; (2) poll with a deadline and a
short interval (`wait_until(lambda: job.done, timeout=5, interval=0.01)`); (3)
only then, freeze the clock and advance it explicitly.

## 6. Order dependence and shared state

Symptoms: passes alone, fails in the suite; passes on a developer machine,
fails on CI; fails only on the second run of the file.

**Fix order:** module-level caches, singletons and connection pools →
class-attribute mutable defaults → leaked async tasks/threads →
environment variables mutated by a test and never restored → fixed
`cwd`/`os.chdir`. The durable fix is a per-test fixture that constructs fresh
state and tears it down; the diagnostic is a randomised-order run
(`pytest-randomly`, or `pytest -p no:randomly` off) and a single-threaded run
to separate "shared state" from "concurrency".

## 7. Snapshot tests nobody reads

A 4000-line snapshot diff after a dependency bump is not a review. Reviewers
approve it because reading it is impossible, so the snapshot stops protecting
anything.

**Fix:** keep snapshots coarse (one screen), or move to explicit assertions on
roles/text/accessible names. If you must keep a snapshot, add a size guard in
review and treat any snapshot change as a review-blocking event.

## 8. Over-mocked boundaries

```python
@patch("app.repo.User")            # the ORM row class
@patch("app.repo.Session")         # the DB session
@patch("app.cache.get")            # the cache
def test_profile_update(...):      # now nothing under test is real
```

The test can pass while the SQL is invalid, because no SQL was ever built.

**Fix:** push the mock up to the process boundary. Test `repo.update_user()`
against a real database (container or SQLite) and mock only the auth provider
or the outbound HTTP call.

## 9. Duplicated coverage

The same guarantee asserted in a unit test, an integration test, and an E2E
test. Cost: three places to update, three chances to fail for one regression.

**Fix:** keep the fastest layer that would catch the regression, delete the
duplicates, and keep a single E2E that proves the wiring end to end. E2E tests
assert *that the journey works*, not every rule along the way.

## 10. Brittle selectors and structural assertions

```python
assert page.locator("div.container > div:nth-child(2) > span").inner_text() == "42"
assert json.dumps(response, sort_keys=True) == '{"a": 1, "b": 2}'   # new key breaks it
```

**Fix:** query by role/label/test id, and assert on the specific fields you
care about (subset assertions) rather than the whole document.

## 11. "Fixing" a flake by sleeping or retrying

Retries hide the defect, multiply runtime, and make the failure rate
unmeasurable. A suite that retries 3 times and reports 99% pass has a 1%
failure rate it can no longer observe.

**Fix:** quarantine the test (do not run it on the merge gate), file an issue
with the test name, and fix the cause. Unquarantine when the cause is fixed.
If you must keep a retry, cap it at one and emit a warning that fails nothing
but is reported — a visible counter, not a silent retry.

## 12. Tests that cannot fail

- `try: ... except Exception: pass` in a helper swallows the real error.
- Asserting `assert result is not None`.
- A test whose only assertion is `assert True` (often left after refactoring).
- Testing a function that cannot fail: no branches, no I/O, no state.

**Fix:** mutation-check the suite: flip a condition in the code, confirm a
test goes red. If nothing goes red, the layer has no teeth.

## PR review checklist for new tests

- [ ] Test name states a behaviour and a condition, not a method name.
- [ ] One behaviour per test; failures localise to one line.
- [ ] No sleeps, no wall-clock, no randomness, no order dependence.
- [ ] Mocks only at process/nondeterminism boundaries; no `assert_called_with`
      on internal collaborators.
- [ ] Error path and at least one boundary case covered alongside the happy
      path.
- [ ] Fixtures build state; they do not assert.
- [ ] Test is independent of the file it lives in and can run in any order.
- [ ] Runtime is in the tens of milliseconds; anything slower is an integration
      test and should be marked and split into the integration job.
- [ ] A new `skip`/`xfail` has a reason and a tracking link, and `xfail` is
      strict.

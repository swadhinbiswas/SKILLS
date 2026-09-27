# Characterisation-test recipes

Recipes for pinning down existing behaviour when there are no tests. The goal
is always the same: make the next change visible.

## Recipe 1: the function that returns a dict with an error key

Legacy shape, very common:

```python
def get_user(uid):
    # -> {"ok": True, "user": {...}}  or  {"ok": False, "err": "..."}
```

**Test the exact error strings.** They are current behaviour, however ugly:

```python
def test_unknown_user_returns_exact_error_shape():
    # Characterisation: the exact string is load-bearing for one caller that
    # displays it. Do not change without checking that caller. See ISSUE-517.
    assert get_user("nope") == {"ok": False, "err": "user not found: nope"}
```

Do not "clean up" the string in a refactor. Grep for the literal first:

```bash
rg -n 'user not found' --hidden -g '!*.pyc'
```

If the only hit is the implementation, you may change it — and that is a
behaviour change, so it gets its own commit.

## Recipe 2: mocks that are too tight

The blocker: the existing tests mock the very method you want to extract, so
they assert implementation, not behaviour.

```python
# Too tight: passes only if the code still calls catalog.price() exactly once
# per item. Any refactor of pricing breaks it.
catalog_mock.price.assert_called_once_with("SKU-1")
```

**Replace one tight assertion at a time**, not the whole file:

1. Keep the test, but add a *behaviour* assertion next to it (the returned
   total). Now the test proves something regardless of the call pattern.
2. Once the behaviour assertion exists, the tight assertion is no longer the
   safety net; delete it.
3. Refactor.

Doing this test-by-test means the suite is never weaker than it was for more
than one commit.

## Recipe 3: a function with no seams

If the function talks directly to a socket, a file, or the clock, and cannot
be given an injection point without changing it, you have three options, in
order of preference:

1. **Introduce a seam and a characterisation test in the same commit** — pull
   the I/O call into a parameter with a default (`now=datetime.now`). This is
   a tiny, obviously-safe change; then the test pins the behaviour.
2. **Golden/snapshot test** — run the function over a fixed input set with
   fixed stubs for everything *outside* it, capture the full output (including
   DB writes as a list of tuples, files as a dict of name→content), and
   snapshot it. After each refactor step, diff. Staleness is the risk: date
   every snapshot in its filename or header (`# snapshot: 2026-03-04`) and
   treat a diff as "read it and decide", not "update it".
3. **Record/replay** — if the environment allows, record real traffic
   (`VCR`, `nock`, `mitmproxy`) and replay it as a fixture. Excellent for
   HTTP, poor for anything with a clock.

## Recipe 4: pinning down error paths

Error paths are where refactoring breaks things, because they are the least
tested. Enumerate them from the code, not from imagination:

```bash
# every raise/throw/return-of-error in the function under test
rg -n 'raise |throw |return \{"ok": False' src/legacy.py
# every caller that catches something
rg -n 'except |catch \(|\.catch\(' src/
```

Then, for each, a test that triggers it with the *smallest* input that reaches
it, asserting the exception type and message. A test that asserts only
"raises" is weaker than it looks — `ValueError("x")` and `RuntimeError("y")`
are different contracts to the caller.

## Recipe 5: the boundary-value matrix

The highest-yield tests for pure-ish functions. One parameterised test covers
the whole class of edges:

```python
@pytest.mark.parametrize("qty,expected", [
    (0, 0),        # zero
    (1, 1500),     # single unit
    (-1, 0),       # negative: current behaviour silently ignores
    (999, 1498500),# max
    (1000, 0),     # over max: rejected today — do not assume it stays
])
def test_quantity_boundaries(qty, expected):
    # Characterisation: negative qty is ignored, not an error. That is
    # probably a bug; see ISSUE-402. Pinned until it is fixed.
    assert line_total(qty) == expected
```

Note the comment on the surprising row. That comment is why the test exists:
the next reader must know the value is *pinned*, not *endorsed*.

## Recipe 6: pinning down time and randomness

Never assert on a live clock or a random value. Freeze both at the seam:

```python
import freezegun  # or unittest.mock.patch("module.datetime")

@patch("src.legacy.uuid4", return_value=UUID("00000000-0000-0000-0000-000000000001"))
@patch("src.legacy.now", return_value=datetime(2026, 3, 4, tzinfo=timezone.utc))
def test_id_and_timestamp_are_pinned(mock_now, mock_uuid):
    assert create_order(cart) == Order(id="00000000-0000-0000-0000-000000000001",
                                        created_at="2026-03-04T00:00:00Z")
```

If the code calls `datetime.now()` directly and you cannot patch it without
editing the function, that edit is a legitimate first refactor step (extract a
`_now()` helper) — but do it as its own commit, after the snapshot test
exists.

## Recipe 7: knowing when you have enough

You have enough characterisation when:

- The function's success path is covered by at least one test.
- Every `raise`/error return is triggered by a test.
- Every boundary that the caller could hit is in the matrix.
- A deliberate break (change a constant, invert a condition) makes at least
  one test fail. **Do this once, deliberately, to prove the net is real.**

That last check is the one people skip, and it is the one that tells you
whether the refactor is safe. If nothing fails, your tests are decorative, and
you are about to rewrite blind.

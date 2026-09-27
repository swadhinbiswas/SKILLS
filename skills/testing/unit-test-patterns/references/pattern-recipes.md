# Pattern recipes

Copy-paste-ready snippets for the patterns that are less common than the ones
in SKILL.md. Verify flag names against `--help` for the version in your repo.

## pytest: a plugin that owns the integration fixtures

Put shared integration fixtures in `tests/conftest.py` (or a plugin package)
so no test file imports them and no test can forget them.

```python
# tests/conftest.py
import os, uuid
import pytest

@pytest.fixture(scope="session")
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.fail("TEST_DATABASE_URL is not set; integration tests need a real database")
    if "prod" in url or "production" in url:
        pytest.fail(f"refusing to run tests against {url!r}: looks like production")
    return url

@pytest.fixture(scope="session")
def migrated_db(db_url) -> str:
    """Apply migrations once per session, then hand every test a clean schema."""
    from myapp.db import engine
    from myapp.migrations import apply_all
    apply_all(engine)
    yield db_url
    engine.dispose()
```

Guard against a missing or production-looking target in the fixture itself.
That assertion is the last line of defence before a test suite truncates a
production table.

## pytest: turn a class of tests on with one flag

```python
def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true",
                     default=False, help="run tests that need real dependencies")
    parser.addoption("--run-slow", action="store_true", default=False)

def pytest_collection_modifyitems(config, items):
    def skip_if(flag, marker):
        if not flag:
            skip = pytest.mark.skip(reason=f"needs --{marker.replace('_', '-')}")
            for item in items:
                if marker in item.keywords:
                    item.add_marker(skip)
    skip_if(config.getoption("--run-integration"), "integration")
    skip_if(config.getoption("--run-slow"), "slow")
```

Running `pytest -m "not integration"` in the fast CI stage and
`pytest --run-integration -m integration` in the slow stage gives two fast,
separate jobs instead of one slow job with a skip count.

## pytest: caplog and captured stdout

```python
def test_rejects_unknown_field(caplog):
    with caplog.at_level("WARNING", logger="myapp.parse"):
        result = parse(payload)
    assert result.is_error
    assert "unknown field" in caplog.text
    assert caplog.records[0].levelname == "WARNING"
```

Assert the *presence* of a category of log line, not its exact wording —
wording changes and this is a frequent source of churn.

## pytest: async

```python
import pytest, pytest_asyncio

@pytest_asyncio.fixture
async def client():
    async with make_client() as c:
        yield c

@pytest.mark.asyncio
async def test_create_then_get(client):
    created = await client.post("/orders", json={"sku": "A1"})
    assert created.status_code == 201
    got = await client.get(f"/orders/{created.json()['id']}")
    assert got.json() == created.json()
```

- Bind fixtures to the same loop scope as the tests. If you see
  `attached to a different loop`, that is a session-scoped loop with
  function-scoped fixtures (or vice versa) — set `loop_scope="session"` on
  `pytest-asyncio` and make async fixtures session-scoped, or use `anyio`.
- For real-time deadlines use `asyncio.wait_for(coro, timeout=5)`; without it a
  regression is a CI timeout of 10 minutes instead of a 5-second failure.

## Jest / Vitest hygiene

```js
// vitest.config.ts — the two settings that stop most cross-test leakage
export default defineConfig({
  test: {
    restoreMocks: true,     // resets mock implementations and calls between tests
    clearMocks: true,
    unstubEnvs: true,       // undoes vi.stubEnv
    unstubGlobals: true,
    setupFiles: ['./tests/setup.ts'],
  },
});
```

Jest equivalents: `restoreMocks: true` and `resetMocks: true` in the config
(recommended over calling `jest.restoreAllMocks()` in each file). For Vitest,
`vi.useRealTimers()` in a global `afterEach` in `setup.ts` is mandatory once
any test uses fake timers:

```ts
// tests/setup.ts
import { afterEach } from 'vitest';
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
```

## Jest: a fake instead of a mock

```ts
class FakeMailer implements Mailer {
  readonly sent: Mail[] = [];
  async send(mail: Mail) { this.sent.push(mail); }
  async sendBatch(mails: Mail[]) { this.sent.push(...mails); }
}

it('sends one welcome mail with the activation link', async () => {
  const mailer = new FakeMailer();
  const svc = new SignupService({ mailer, tokens: tokensStub() });

  await svc.register({ email: 'a@example.test' });

  expect(mailer.sent).toHaveLength(1);
  expect(mailer.sent[0].to).toBe('a@example.test');
  expect(mailer.sent[0].subject).toMatch(/activate/i);
});
```

A fake is state, not a call log: it survives refactoring from
`send()` to `sendBatch()` and gives a far better failure message.

## Golden files

```python
from pathlib import Path

GOLDEN = Path(__file__).parent / "golden"

def check_golden(actual: str, name: str, *, update: bool = False) -> None:
    path = GOLDEN / f"{name}.txt"
    if update or not path.exists():                 # explicit --update-golden only
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual)
        return
    expected = path.read_text()
    if expected != actual:
        import difflib
        diff = "\n".join(difflib.unified_diff(
            expected.splitlines(), actual.splitlines(),
            fromfile=f"golden/{name}.txt", tofile="actual", lineterm=""))
        assert False, f"golden mismatch for {name}:\n{diff}"
```

Wire `update=True` behind a single CLI flag and never set it from CI. The
pattern only works while regenerating is a deliberate, reviewed act.

## Async property tests

```python
import pytest
from hypothesis import given, settings, strategies as st

@settings(max_examples=25, deadline=None)   # deadline off for I/O-bound props
@given(st.integers(min_value=0, max_value=10**6))
def test_batch_sums_match_sequential_sums(n: int):
    import anyio
    async def run(n):
        items = [LedgerEntry(amount=i) for i in range(n)]
        assert await Ledger.combine_async(items) == Ledger.combine(items)
    anyio.run(run, n)
```

Lower `max_examples` for I/O-bound properties — 25 examples against a real
database is already slow, and raising it is how property suites become the
slowest job in CI.

## Fast-check with async and shrinking

```ts
it('never rejects a valid request', async () => {
  await fc.assert(
    fc.asyncProperty(validRequestArb, async (req) => {
      const res = await client.post('/orders', req);
      return res.status < 400;
    }),
    { numRuns: 50, verbose: true },
  );
});
```

`verbose: true` prints the seed and the shrunk counterexample — turn it on when
a CI failure needs a local reproduction. Re-run that seed locally with
`fc.configureGlobal({ seed })` or `fc.assert(prop, { seed, path })`.

---
name: contract-and-api-testing
description: Catch breaking API and schema changes before they deploy using consumer-driven contract testing (Pact-style), OpenAPI/JSON Schema validation, and schema diffing gates in CI. Use when a service changes an API, when two teams own a shared contract, when a shared library bump breaks consumers, or when someone asks how to test backward compatibility, deprecate a field, or add a response field safely. Triggers on "breaking change", "API contract", "pact", "OpenAPI diff", "schema compatibility", "deprecate endpoint", "backward compatible".
compatibility: Examples use Pact, openapi-diff/OpenAPI diff tooling, JSON Schema, and GitHub Actions steps. Verify the exact action and CLI names against your versions before using them.
metadata:
  version: "1.0"
---

# Contract and API Testing

Two things must never happen: a consumer discovers a break in production, or
a "harmless" added field breaks a strict consumer. Contract tests make both
impossible by making the *promise* the artefact under test.

## Three layers, each catching a different break

| Layer | Question | Runs | Catches |
|---|---|---|---|
| Schema validation (OpenAPI / JSON Schema) | Does every response match the declared shape? | Every request, in a proxy/fake or the service test | Wrong types, missing fields, undocumented fields |
| Schema diffing (CI gate) | Is this change backward compatible for *declared* consumers? | On every PR that touches the spec | Renamed/removed fields, tightened validation, narrowed enums |
| Consumer-driven contracts (Pact) | Does the provider still satisfy what *real* consumers actually call? | On the provider's CI, against the recorded pact | Changes nobody declared, unpinned assumptions, new required params |

Use schema validation always (cheap). Add schema diffing as a mandatory CI gate
for any public or cross-team API. Add Pact when the provider has consumers it
cannot see the tests of — a platform team publishing twenty internal services
should default to Pact. For a single-team monolith, schema diffing alone is
usually enough.

## Layer 1: schema validation in every test

Do not hand-assert response shapes. Validate against the spec.

```python
import json
from jsonschema import Draft202012Validator
from pathlib import Path

SPEC = json.loads(Path("openapi/order-service.yaml").read_text())
SCHEMAS = {
    name: Draft202012Validator.from_schema(s)
    for name, s in SPEC["components"]["schemas"].items()
}

def assert_matches_spec(name: str, payload: dict) -> None:
    errors = sorted(SCHEMAS[name].iter_errors(payload), key=lambda e: list(e.path))
    assert not errors, "\n".join(
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors
    )
```

```ts
import Ajv from 'ajv';
import spec from './openapi/order-service.json';

const ajv = new Ajv({ strict: false, allErrors: true });
const compiled = Object.fromEntries(
  Object.entries(spec.components.schemas).map(([name, schema]) => [name, ajv.compile(schema)]),
);

export function assertMatchesSpec(name: string, payload: unknown) {
  const validate = compiled[name];
  if (!validate(payload)) {
    throw new Error(`${name} violates spec:\n` + ajv.errorsText(compiled[name].errors, { separator: '\n' }));
  }
}
```

Two modes, both useful:

- **Inline:** call `assert_matches_spec("Order", response.json())` in a handful
  of handler tests, including the error bodies. Catches a lie in your own code
  fast.
- **Shadow/recorded mode:** a proxy (a reverse proxy in front of the service, or
  a VCR/WireMock-style recorder) validates *every* response in a soak run
  against the spec. This finds the endpoints nobody tests, which is where the
  undocumented shape lives.

GOTCHA: JSON Schema `additionalProperties: false` in your spec will make every
new field a validation error — which is correct, and is the point: it forces the
PR that adds the field to also update the spec and the consumers.

## Layer 2: schema diffing as a CI gate

Diff the spec against the base branch and fail on a breaking change. Breakage
is classified in two ways: **wire compatibility** (can an existing client still
parse this?) and **semantic compatibility** (does the new server still honour
what the old spec promised?). The second is the one naive differs miss.

```yaml
# .github/workflows/api-compat.yml (skeleton — pin the action to a SHA before use)
name: api-compat
on: pull_request
jobs:
  openapi-diff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>
        with: { fetch-depth: 0 }         # the diff needs the base branch's spec
      - name: Diff OpenAPI against base (breaking changes fail)
        run: |
          set -euo pipefail
          npx --yes @redocly/cli lint openapi.yaml
          npx --yes openapi-diff breaking main.yaml openapi.yaml --fail-on ERR
```

`openapi-diff` exits non-zero when there are `ERR`-level (breaking) changes and
zero for `WARN`/`INFO`. `--fail-on ERR` is the gate; `WARN` means "suspicious"
(use it as a report, not a gate, until you have triaged it). Alternatives in
the same space: `oasdiff` (Go binary, same ERR/WARN/INFO severity model, has a
`breaking` subcommand), and Spectral for linting. Verify the current CLI flags
with `--help`; these tools iterate.

What counts as breaking (any tool agrees):

- Removing or renaming a path, a field, or an operation.
- Adding a **required** request parameter or request field.
- Adding a field to a response marked `additionalProperties: false` or a
  `strict` client.
- Narrowing a type, reducing `maximum`, shortening an enum, adding a
  `pattern`/`format` constraint.
- Raising a minimum page size, lowering a default limit, changing a default.
- Changing a status code for an existing error case.
- Making a field nullable → non-nullable.

What looks breaking but usually is not (and should be a `WARN` you review, not
a hard fail): adding an optional request field, adding a response field with an
open object, raising a `maximum`, adding a status code. Adding a response field
**is** safe for most JSON clients and lethal for strict generated clients — that
is why the spec must say which side is closed.

Semantic checks a differ cannot do — write these as tests or as review gates:

- "Consumers may now receive 1000 items where they were promised 100" — the
  diff sees no change; a consumer test sees the page-size default move.
- "The new implementation treats `status=ACTIVE` as `PENDING`" — identical
  schema, different meaning. Pact and example-based tests catch this; a differ
  cannot.
- "The `legacy_id` field is now sometimes null" — type unchanged.

## Layer 3: consumer-driven contracts (Pact)

The consumer records the requests it makes and the responses it relies on; the
provider replays those against a real provider instance in its own CI. Neither
team has to know the other's test suite.

**Consumer side (Python, `pact-python`):**

```python
from pact import Consumer, Provider
import pytest

@pytest.fixture
def order_pact(order_service_url):
    pact = Consumer("web-checkout").has_pact_with(Provider("order-service"))
    with pact:
        yield pact
    pact.write_pact()          # writes pacts/web-checkout-order-service.json

def test_checkout_fetches_order_total(order_pact, order_service_url):
    order_pact.given("an order with two lines exists").upon_receiving(
        "a GET for the order").with_request("get", "/orders/42",
                                            query={"expand": "lines"}).will_respond_with(
        200, body={"id": 42, "currency": "GBP",
                   "total_cents": 2599, "lines": [
                       {"sku": "A1", "qty": 1, "price_cents": 1299},
                       {"sku": "B2", "qty": 1, "price_cents": 1300}]})
    assert order_service_url is not None     # exercise through your real client
```

**Provider side** — the provider CI replays every pact, per consumer:

```bash
pact-verifier --provider-base-url http://localhost:8080 \
              --pact-dir pacts \
              --provider-app-version "$GIT_SHA" \
              --state-change-url http://localhost:8080/__admin/pacts/states
```

The house convention: **every provider build verifies every stored pact** in the
same job as its unit and integration tests. A provider CI that does not is not
honouring the contract, and the consumer finds out in production.

### States, the part that is usually missing

A pact interaction needs a known starting state (`given`). Either the provider
replays a real interaction to set state up, or it exposes a state-change
endpoint for test fixtures only:

```python
@app.post("/__admin/pacts/states", include_in_schema=False)
def set_pact_state(body: dict):
    if body.get("state") == "an order with two lines exists":
        OrderFixture.reset()
        OrderFixture.create(id=42, lines=[("A1", 1, 1299), ("B2", 1, 1300)])
    return Response(status=200)
```

That endpoint is a production code path. Guard it: bind it to a test-only
router, require the provider app to run with `--testing`, and assert the
`--testing` flag is off in the deploy pipeline. It must never be reachable on
the public deployment.

### Pact lifecycle rules that keep pacts useful

- **Pacts are committed, versioned artefacts**, regenerated by the consumer in
  its own PR when its usage changes — not by hand, and not by the provider.
- **A "pact is out of date" failure is a signal, not a failure to suppress.**
  Either the provider changed (fix it or version the API) or the consumer
  changed (the consumer PR should have regenerated the pact; the check fails
  when the pact file is missing or stale relative to the consumer's expectations).
- **Pacts grow forever if nobody prunes them.** Periodically regenerate them
  from consumers, and delete pacts for consumers that no longer exist.
- **Verify every consumer on every build.** A sampled or nightly pact
  verification is a broken contract with extra steps.

## Adding a field safely — the rules

For a JSON API with generated clients on the other side:

- **Adding a response field:** safe only if the consumer's schema is open. Check
  for generated clients (OpenAPI Generator, Kiota, NSwag, Stainless) — generated
  deserialisers are often strict and will fail on unknown fields. Verify, do not
  assume.
- **Removing or renaming a field:** always breaking. Sequence it:
  1. Stop writing the field (provider) while still serving it (readers unaffected).
  2. Announce a deprecation: HTTP `Deprecation: true` and `Sunset: <date>`
     headers, plus an entry in the changelog with the date and the replacement.
  3. Wait one full deprecation window (one release cycle at minimum, longer if
     consumers are external), monitoring usage per consumer.
  4. Remove it in a versioned release, and say so in the changelog.
- **Adding a required request field:** always breaking. Add it optional with a
  default, then make it required a release later.
- **Widening a type or enum:** safe in JSON only if consumers tolerate it.
  Widening an enum is a break for exhaustive `switch` in generated clients.

## Testing internal APIs

- **Don't.** Internal calls are refactorable, so contracts there cost without
  benefit. A type signature or an architecture test (import rules, ArchUnit,
  dependency-cruiser) is the right tool.
- **Do** contract-test anything with a lifecycle longer than one deploy: public
  APIs, webhooks, event schemas, mobile clients you do not control, and internal
  APIs with three or more independent consumers on different release cadences.
- **Event and webhook contracts** need the same discipline: a schema per event
  type, a compatibility check in CI, and a versioned envelope. Consumers that
  cannot upgrade in step with producers need either an envelope version field
  or an adapter that translates old envelopes for a deprecation window.

## Safety notes

- **Never deploy a contract test's state-change endpoint to production.**
  Assert it in a smoke test: a request to `__admin/*` on the public URL returns
  404.
- **Never `pact-broker --provider-app-version` against a production URL.** The
  verifier writes results and, with `--publish-verification-results`, publishes
  them. Use a local or staging provider.
- **Never auto-approve a failing compat gate** by regenerating the spec. A
  breaking change needs an explicit, reviewed decision and a version bump.

## Gotchas

- **`additionalProperties: false` is a contract decision, not a lint setting.**
  It makes your API a "closed" contract: adding fields becomes breaking. Choose
  open (default) for most internal JSON APIs, closed for generated-client APIs.
- **A consumer's pact reflects only what that consumer exercised.** Coverage gaps
  look like "no consumers use that endpoint" — which is false. Track pact
  coverage per endpoint, not just per contract file.
- **Verification results are not a substitute for the provider's own tests.**
  Both must run in the same pipeline, or the provider can merge something that
  passes its own suite and breaks every consumer.
- **`fetch-depth: 0` (or a full clone) is required** for any spec/base-branch
  diff; a shallow clone of depth 1 makes the diff silently compare against
  nothing or fail.
- **Diff against the right base.** `main` is the merge base for most PRs; for
  release branches, diff against the last released tag so unreleased breaking
  changes are not double-reported.
- **Enum and optionality changes are the most-missed breaks.** A field that goes
  from `enum: [A, B]` to `enum: [A, B, C]` is wire-compatible and
  semantically breaking for exhaustive consumers. Report it as `WARN` and
  require human review.
- **Nondeterministic field ordering and generated timestamps** make pacts
  unreplayable. Use a state-change URL that sets a fixed clock, and match
  request bodies with matchers, not exact JSON.
- **A pact that nobody regenerates becomes a lie** — it keeps asserting a
  contract the consumer no longer uses, and the provider contorts to satisfy it.

## Files

- `references/schema-diffing.md` — severity model and worked examples for
  `openapi-diff`/`oasdiff`, JSON Schema compatibility rules, and how to write a
  custom rule for a rule a differ cannot express. Read it when wiring the compat
  gate or when the gate is producing false positives.

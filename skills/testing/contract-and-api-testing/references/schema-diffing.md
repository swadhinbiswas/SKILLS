# Schema diffing: severity, examples, and custom rules

## Severity model (openapi-diff, oasdiff)

Both tools classify every difference into three levels. The gate should fail on
the top level only; the middle level is a review prompt.

| Level | Meaning | Example | Gate |
|---|---|---|---|
| `ERR` | Definitely breaking for an existing client | removed property, added required request param, narrowed type | fail |
| `WARN` | Breaking for some clients (strict, exhaustive, generated) | added response property, added enum value, `nullable` removed, default changed | review (fails if you choose to make it strict) |
| `INFO` | Cosmetic or additive | added optional property, added endpoint, description change | ignore |

```bash
# oasdiff
oasdiff breaking main.yaml head.yaml            # exit 0 unless breaking found
oasdiff -v ERR summary main.yaml head.yaml
oasdiff -f json summary main.yaml head.yaml > compat.json   # machine-readable

# openapi-diff
npx openapi-diff breaking main.yaml head.yaml --fail-on ERR
npx openapi-diff summary main.yaml head.yaml
npx openapi-diff changelog main.yaml head.yaml
```

Verify the current flag spellings with `--help`; these tools have renamed
subcommands before.

## Worked examples: is this change breaking?

| Change | Level | Why |
|---|---|---|
| `Order.total_cents: int` → `Order.total_cents: string` | ERR | Client `int` parse fails |
| `Order.status: enum[A,B]` → `enum[A,B,C]` | WARN | Wire-OK; exhaustive `switch` has no `C` case |
| `Order.status: enum[A,B]` → `enum[A]` | ERR | Client may send or receive `B` |
| New optional request property `coupon_code` | INFO | Server must tolerate its absence |
| New required request property `coupon_code` | ERR | Old clients omit it; server must 400 or default it |
| New response property `discount_cents` | WARN | Wire-OK for open clients; fatal for strict generated clients |
| `limit: integer(int32, max 1000)` → `max 100` | ERR | Old client requesting 500 now rejected |
| `limit: integer, default 20` → `default 50` | WARN | Same shape, different behaviour; not seen by any differ as `ERR` |
| `422` error response removed from an operation | ERR | Client branches on 422 |
| `404` error response added | WARN | Old clients treat it as an unhandled status |
| `field: string` → `field: string, format: date` | WARN | Wire-OK; clients parsing the value may differ |
| `field` nullable → required | ERR | Old payloads may omit it |
| New path `/orders/{id}/refunds` | INFO | Additive |
| Removed path `/orders/{id}/refunds` | ERR | Existing callers 404 |
| `id: int` → `id: int64` | ERR in practice | JSON has no int32; OpenAPI type maps differ by generator |
| Enum value removed from a **request** enum | ERR | Old clients send the removed value |
| Tightening `format: email` validation | WARN | Old valid-per-spec input now 400s |

The last two rows are the ones humans misjudge. Make the gate's severity table
part of your API's written policy so review decisions are not re-litigated.

## JSON Schema compatibility rules (for payloads, events, webhook bodies)

If you diff JSON Schemas rather than OpenAPI, the compatibility question is
about a document that must still validate against the *old* schema (for
requests) or still be parseable by old consumers (for responses).

Backward compatible (old document still valid against new schema) when:

- You add an optional property **and** `additionalProperties` is not `false`.
- You loosen a constraint (wider `enum`, higher `maximum`, remove `pattern`).
- You add a property to an `allOf`/`oneOf` branch in a way that keeps old
  documents valid.

Breaking when:

- `additionalProperties: false` is added anywhere, or a required property is
  added, or a property becomes `const`.
- A `oneOf`/`anyOf` gains a branch that an old document could previously match
  (ambiguity, not a parse failure — a semantic break for validators that pick
  the first match).
- A property is renamed, retyped, or its `format` is narrowed to one a
  consumer's parser does not accept.
- A numeric `multipleOf` is added, or a `maxLength` is lowered.
- A `default` is added to a property the consumer computes: the value
  consumers see changes without a shape change. This is the sneakiest one —
  no differ flags it.

Implement the diff over normalised JSON Schema (resolve `$ref`, normalise
`type` arrays, then compare per property path). Do not rely on textual diff:
formatting and key order alone would produce noise.

## Custom rules a differ cannot express

These are semantic checks; write them as a script in the same CI job. The gate
is then: run the differ, then run your rules, fail if either fails.

```python
# tools/api_semantic_rules.py — checks that the spec promises still hold.
import sys, yaml, pathlib

def main(path: str) -> int:
    spec = yaml.safe_load(pathlib.Path(path).read_text())
    violations: list[str] = []

    for route, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if not isinstance(op, dict):
                continue
            if method != "get" and "default" not in op.get("responses", {}):
                violations.append(f"{method.upper()} {route} has no documented default error response")
            for name, p in (op.get("parameters") or []):
                if p.get("required") and p.get("schema", {}).get("default") is None:
                    violations.append(f"{method.upper()} {route}: required param {name!r} has no default (old clients will break)")
            for status, resp in (op.get("responses") or {}).items():
                if status.startswith(("4", "5")) and "content" not in resp:
                    violations.append(f"{method.upper()} {route}: error {status} has no content schema")

    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if schema.get("additionalProperties") is False and schema.get("properties"):
            closed = list(schema["properties"])
            if len(closed) < 2:
                violations.append(f"{name}: additionalProperties=false with a single field; adding any field becomes breaking")

    for v in violations:
        print(f"api-semantic-rule: {v}", file=sys.stderr)
    return 1 if violations else 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "openapi.yaml"))
```

Rules worth writing for any real API:

- **Every non-2xx response has a schema**, so clients can rely on an error
  body (a `code` field, in particular).
- **No required parameter without a default** on an operation that existed
  before.
- **Page-size defaults and maximums are recorded in a small checked-in
  contract file** (`api-contracts/page-limits.yaml`) so the gate can compare
  them: `order-search` promised `{default: 20, max: 100}` last release.
- **A field that was `nullable` is still `nullable`** unless the change is
  major-versioned.
- **Deprecated operations still appear in the spec** with `deprecated: true`
  and a `Sunset` header documented, so the deprecation window is visible in the
  contract rather than only in a changelog.

## Running the gate on the right base

- **Pull requests:** diff against the merge base
  (`git merge-base HEAD origin/main`) so unreleased changes on the branch are
  compared once, not repeatedly.
- **Release branches:** diff against the last release tag
  (`git describe --tags --abbrev=0`) so already-released breaking changes are
  not re-reported every PR.
- **Public APIs with published specs:** diff against the published artifact
  from the registry, not the git branch — that is the thing consumers used.
- **Store the report as an artifact** and post it as a PR comment. The comment
  is what gets read; the job exit code is what gets enforced.

---
name: structured-output-and-json
description: Get reliable, parseable JSON and typed objects out of a model using structured outputs / response schemas, JSON mode, or prompt-enforced formats, plus defensive parsing, schema design that avoids retries, and recovery from the invalid-output failure loop. Use when a model returns markdown-fenced JSON, refuses to produce valid JSON, returns unexpected types or nulls, when a program must consume LLM output, or when the user says "return JSON", "response_format", "json_schema", "zod", "parse error", "invalid JSON", "pydantic model output".
compatibility: Provider-agnostic. Structured-output support, schema dialect, and refusal handling differ by vendor and change often; check current docs for the model you call.
metadata:
  version: "1.0"
---

# Structured Output and JSON

If a program consumes it, it must be structured. Never parse a model response
with a regex and hope.

## Pick the strongest mechanism the model supports

| Mechanism | Guarantees | Use when |
|---|---|---|
| **Structured outputs / response schema** (constrained decoding against a JSON Schema) | Field names, types, enums, required-ness, nesting | Anything a program parses. Default. |
| **JSON mode** | Valid JSON, schema only as far as the prompt enforced | Provider has no schema support but you need no-fence JSON |
| **Prefill / response prefix** | The model continues from your opening tokens | You need a specific opening (e.g. `{`) or want to pin a field |
| **Grammar / format constraints** | Full format incl. non-JSON | Non-JSON structured targets (SQL, regex, tool DSLs) |
| **Prose instruction ("respond with JSON")** | Nothing | Never, in production |

Provider API surface changes: whether it's called `response_format`,
`json_schema`, `structured_outputs`, or a `tools` entry with a schema differs.
The *intent* is always "constrain decoding to this schema" — check the current
docs for the exact field names and the JSON Schema subset supported for your
model.

**Constrained decoding is not validation.** Even under a strict schema:
- the model may put a *wrong* value in a correctly-typed field;
- the model may emit its `refusal` stop reason instead of a response;
- older models or a "compatibility" fallback path may silently drop the schema
  and fall back to plain JSON mode.

**Validate on the way out regardless.** Treat schema success as "parseable", not
as "correct".

## Schema design that avoids retries

Most retries come from a schema the model cannot satisfy. Design for the model.

- **Always include the properties you care about; require only what is truly
  essential.** A `required` array that omits something optional is a common and
  silent source of nulls.
- **Prefer enums over open strings.** `severity: "high" | "medium" | "low"` is
  enforced; a free-text severity is your parsing problem.
- **Avoid empty objects and deeply nested required objects.** Flatten to two or
  three levels; a 4-level required object is where call validity collapses.
- **Make the type the constraint you mean.** Use `integer` for counts and ids,
  `boolean` for flags, `array` with `minItems` when at least one is required.
  `"type": "string"` with "return a number" in the description is a bug.
- **Constrain string formats**: `pattern` for regex-shaped values, `minLength` /
  `maxLength`, and a `description` that states the unit and an example. Providers
  vary in how much of JSON Schema they enforce, so put the same constraint in
  the description as belt-and-braces.
- **No `oneOf`/`anyOf`/`allOf` unions in the schema** unless you have verified
  support — the model must pick a branch, and the constrained decoder often
  cannot represent it. Use a single flat shape with an enum discriminator and
  optional fields instead.
- **No `$ref`/`definitions` cycles.** One level of reuse is fine; recursion
  breaks constrained decoders. Model trees as explicit nested objects.
- **Cap free-text fields with `maxLength`** so one runaway field cannot blow the
  output budget.
- **Do not ask the model to compute what code computes.** No `total`, no
  `timestamp`, no `slugs`, no derived percentages. Compute them after parsing.
  Model maths is a correctness bug you have opted into.
- **Optional-but-desired fields: give them a default and a null-able type**
  (`["string", "null"]` where supported) rather than omitting the field, so the
  model does not have to choose between absent and empty.

A schema that works:

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["intent", "entities", "needs_human"],
  "properties": {
    "intent": { "type": "string", "enum": ["refund", "bug", "how_to", "chitchat"] },
    "entities": {
      "type": "array", "maxItems": 8,
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["type", "value"],
        "properties": {
          "type": { "type": "string", "enum": ["order_id", "email", "product"] },
          "value": { "type": "string", "maxLength": 128,
                     "description": "Order ids look like ord_1234; bare digits for order_id." }
        }
      }
    },
    "needs_human": { "type": "boolean",
                     "description": "true when the request is angry, legal, or ambiguous." }
  }
}
```

The generated code path (Python 3.11+, stdlib-first; `model` is your call):

```python
from __future__ import annotations
import json, logging
from typing import Any, TypedDict, Literal

class RefundToolCall(TypedDict):
    type: Literal["refund", "bug", "how_to", "chitchat"]

def parse_tool_call(raw: str) -> dict[str, Any] | None:
    """Strict-then-tolerant parse. Returns None only if nothing usable is left."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Tolerant fallback for JSON mode / model fallback paths: strip fences.
    text = raw.strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
        try:
            return json.loads("\n".join(lines))
    except Exception:
        return None
```

## Defensive parsing at the boundary

Every model response crosses a trust boundary. Write one strict, boring
function that all call sites use.

- **Parse strictly first** (`json.loads` on the whole string). Only then, as a
  documented fallback, strip markdown fences or find the outermost `{...}`.
- **Then validate the parsed value against your model/schema**, with a real
  validator (Pydantic, Zod, `jsonschema`, or a hand-written `TypedDict` +
  explicit checks). Parsing success is not validation success.
- **If validation fails, do not loop blindly.** See recovery below.
- **Log the raw response** (truncated, and scrubbed) on failure, plus the model,
  prompt version, and finish reason. A parser error with no captured body is
  undebuggable.
- **One retry, at most, and only with a corrective message** - see below.
- **Never `eval`/pickle model output**, and never interpolate it into SQL, shell,
  or a template. It is untrusted data (see
  `skills/ai/llm-privacy-and-safety/SKILL.md`).
- **Return a typed error to the caller, not an exception that crashes the
  request** - the caller usually has a degraded path.

## The invalid-output failure loop

The classic bug: parse fails → retry the *same* prompt → fails identically →
retry 5 times → 5x cost, 5x latency, same failure. The fix is to make the
retry *different*.

1. **Diagnose the failure class** from the captured body: truncated
   (`finish_reason: length` → raise `max_tokens` or lower the required work),
   fence-wrapped (→ strip, or you fell out of schema mode), wrong shape
   (→ stricter schema, not a retry), refusal (→ the request or context tripped
   a policy; escalate or degrade), empty (→ transient; safe to retry once).
2. **Repair-then-retry, once.** Send a short corrective turn: the invalid
   response plus `"Your previous output was not valid against the schema. The
   error was: {parse_error}. Return the corrected object only."` This works
   far better than a fresh identical call.
3. **For `finish_reason: length`, a repair will not help.** You were truncated.
   Raise the ceiling, shrink the required output, or split the task. A truncated
   JSON string is the most common "invalid JSON" cause and the most commonly
   misdiagnosed as a formatting problem.
4. **After the repair attempt, take the deterministic path**: coerce what you
   can (missing optional field → default, wrong type → parse-or-drop), return a
   partial result flagged as partial, or fall back to a rule-based/legacy
   response. Never throw away the request.
5. **Stop and fix the system** if the repair rate is above a few percent. That
   is a schema or prompt defect, not bad luck - fix it and add the case to your
   eval set (`skills/ai/llm-evaluation/SKILL.md`).

## Refusals and stop reasons

- A safety-triggered generation returns a **refusal**, not malformed JSON, and
  the shape is provider-specific. Check the stop reason / refusal field *before*
  trying to parse, and route refusals to a distinct user-facing path.
- **`stop_reason: length`** means truncated output - treat as a distinct,
  observable failure mode, not a parse error.
- **Tool-calling responses** return tool-call arguments as their own field; do
  not assume they are in `content`, and never mix the two shapes.

## Gotchas

- **Do not nest structured output inside prose.** "First think, then output
  JSON" invites preambles like "Sure! Here is the JSON:" that break strict
  parsing and defeat constrained decoding. Reasoning and output are separate
  fields or separate calls.
- **Fences appear when you fall back to prompt-enforced JSON.** That is a
  signal your schema mode silently dropped, not a formatting quirk to strip
  forever.
- **Markdown-fenced JSON from a chat UI is a different problem** than a
  server response - a UI strips fences for you, a server does not.
- **`additionalProperties: false` where supported** meaningfully reduces
  hallucinated extra fields, which downstream code then chokes on.
- **Schema support is model- and mode-dependent.** A schema on an older model may
  be ignored or approximated. Verify by testing that an impossible enum value
  actually comes back as a refusal/valid value, not as your string.
- **`max_tokens` too low with a schema forces truncation, which is invalid
  JSON.** Size the ceiling from the worst-case valid object plus a margin.
- **Version the schema with the prompt.** Changing the schema is a breaking
  change for every consumer; version it, and let old and new coexist during
  rollout.
- **Don't test the parser with a well-behaved fixture.** Unit-test the
  truncated, fenced, wrong-type, extra-field, and empty cases; that is where
  parsers live.

## Checklist

- [ ] Strongest supported mechanism selected (schema > JSON mode > prefill)
- [ ] Schema uses enums, types, bounds, and `additionalProperties: false`
- [ ] No computed fields, no unions, no deep required nesting
- [ ] One strict parse + validate function, used by every call site
- [ ] Refusal and `stop_reason: length` handled before parsing
- [ ] At most one repair retry, and it is corrective, not identical
- [ ] Failure bodies logged (truncated, scrubbed) with model and prompt version
- [ ] Callers have a deterministic degraded path
- [ ] Impossible-enum test confirms the schema is actually enforced
- [ ] Fenced / truncated / wrong-type fixtures in the unit tests

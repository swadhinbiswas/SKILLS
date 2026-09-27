---
name: rest-api-design
description: Design an HTTP API that stays coherent after v1 - resource modelling, nouns over verbs, status codes as a contract, RFC 9457 problem details for every error, idempotency keys, ETag/If-Match conditional writes, PATCH vs PUT, content negotiation, and versioning. Use when the user says "design a REST API", "endpoints for X", "API contract", "which status code", "error response format", "idempotency", "optimistic locking", or is reviewing an existing API before a client is written against it.
compatibility: Applies to any HTTP/1.1 or HTTP/2 service; examples use JSON and are language-agnostic.
metadata:
  version: "1.0"
---

# REST API Design

Produce an endpoint table, a status-code contract, and one error shape that is
identical across every endpoint. The goal is an API a client can write against
from the documentation alone and that you can still change in year two.

## Workflow

- [ ] 1. Model resources before endpoints (nouns, hierarchy, identifiers)
- [ ] 2. Pick one identifier style and stick to it
- [ ] 3. Decide `PUT` vs `POST` vs `PATCH` per mutation, and what `409` means
- [ ] 4. Choose the error shape once and apply it everywhere
- [ ] 5. Add `ETag`/`If-Match` for any resource two clients may write
- [ ] 6. Add `Idempotency-Key` to every `POST` that charges, sends, or creates
- [ ] 7. Write the endpoint table, then review it against the Gotchas list

## Step 1 — Resources, not endpoints

A resource is a thing with an identity. The test: **can two URLs point at the
same state?** If yes, you have two resources; if no, you have a verb.

```
POST /orders            -> creates Order        (Order has its own id)
GET  /orders/{id}       -> reads it
POST /orders/{id}/cancel-> this is a verb, and it usually should not exist
```

Prefer modelling the state change as a new resource or a field over an
action endpoint. `PATCH /orders/{id}` with `{"status": "cancelled"}` is one
code path; `POST /orders/{id}/cancel`, `POST /orders/{id}/archive`, and
`POST /orders/{id}/refund` are three, each with its own auth, its own error
cases, and its own deprecation path.

Use an action endpoint only when the action is not a state transition of the
resource: things that genuinely start something (`POST /reports` — a report
takes minutes and has its own lifecycle) or are not idempotent by nature.

Nesting is for containment, not for hierarchy theatre. `/orders/{id}/items` is
good because an item does not exist outside an order. `/orders/{id}/customer/
address/country` is not. Cap nesting at two levels and let the leaf carry a
globally unique id.

## Step 2 — Identifiers

Pick one and never mix:

| Style | Example | Notes |
|---|---|---|
| Opaque server id | `GET /orders/01J8Z...` | Default. No structure to leak, no guessable sequential ids |
| Natural key | `GET /orders/2026-00041` | Only if it is a real business key you guarantee stable |
| Prefixed route | `GET /orders/ord_01J8Z...` | Useful when several resource types share an id space |

Never expose sequential database ids. They leak volume and they let a client
enumerate your customers by walking `/orders/1`, `/orders/2`, ….

Timestamps: `created_at` in RFC 3339 UTC with a `Z` suffix. Money: integer
minor units plus an ISO 4217 currency, never a float. `{"amount": 1999,
"currency": "USD"}`, not `{"amount": 19.99}`.

## Step 3 — Methods and status codes

**`POST` creates.** Return `201 Created`, a `Location` header pointing at the
new resource, and the resource body. If the client supplied an id, echo it; if
you generated one, put it in the body too.

**`PUT` replaces.** The full representation. Absent fields are cleared, not
preserved. `PUT` on a non-existent resource creates it. `PUT` must be
idempotent by definition: calling it twice leaves the same state. If your
"update" is not idempotent, it is a `POST`.

**`PATCH` changes some fields.** The default for user-driven updates, because
it avoids the "did I forget to send the unchanged field" class of bug.

**`DELETE`** should be idempotent: deleting an already-deleted resource returns
`204` again, not `404`.

| Situation | Status | Notes |
|---|---|---|
| Read succeeded | `200` | |
| Created | `201` | + `Location` |
| Accepted for later processing | `202` | + a status resource you can poll; no `Location` to the result |
| Successful mutation, no body | `204` | No body, and no `Content-Type` |
| Malformed JSON / bad params | `400` | Machine-parseable detail |
| Not authenticated / not authorized | `401` / `403` | Never `403` for "you are not logged in" |
| Not found | `404` | Also used to hide existence from unauthorized callers |
| Version conflict | `409` | Uniqueness, state-machine violation, or a failed `If-Match` |
| Precondition required | `428` | If you require `If-Match` and it is missing |
| Precondition failed | `412` | `If-Match` / `If-None-Match` did not hold |
| Payload too large | `413` | |
| Unsupported media type | `415` | |
| Rate limited | `429` | **Must** carry `Retry-After` |
| Server error | `500` | Never leak internals in the body |
| Dependency down | `503` | + `Retry-After` when you know the delay |

Read `references/status-codes.md` when the situation is not in that table —
it has the full decision procedure and the traps (`400` vs `422` vs `409`,
`401` vs `403`, `404` vs `403` for privacy).

## Step 4 — The error shape (emit this)

Use **RFC 9457 problem details** (`application/problem+json`). One shape, all
endpoints, forever. The agent should emit this template and fill it in:

```json
{
  "type": "https://api.example.com/problems/insufficient-funds",
  "title": "Insufficient funds",
  "status": 409,
  "detail": "The wallet balance of 400 USD is less than the 1250 USD required.",
  "instance": "/v1/transfers/7f3a/transfers",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "errors": [
    { "field": "amount", "code": "amount_too_large",
      "message": "Must not exceed the available balance." }
  ]
}
```

Field rules:

- `type` is a stable URI you control. Clients branch on this or on `code`,
  **never** on `detail` or `title` prose.
- `title` is a short human summary, same value for the same `type` every time.
- `detail` is for this one occurrence, and must never contain a stack trace,
  SQL fragment, internal hostname, or stack frame.
- `instance` is the request path. `trace_id` is the correlation id you also put
  in your logs — without it, a user-reported failure is unactionable.
- `errors` is your extension: a list of machine-readable field errors for
  validation failures, `[]` or absent otherwise. Put your extensions at the
  **top level**, not under a namespace, and never add them conditionally to the
  *standard* fields.

Fastly's `application/problem+json` is the de-facto shape; it is what RFC 9457
standardised. Send `Content-Type: application/problem+json` for all of it,
including `400` and `500` — not `application/json`.

### Validation errors

One request, many problems: report all of them, and do not partially apply.
Validate the whole body, then write.

```json
{
  "type": "https://api.example.com/problems/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "The request body has 2 invalid fields.",
  "errors": [
    { "field": "email",    "code": "invalid_format", "message": "Must be a valid email address." },
    { "field": "quantity", "code": "out_of_range",   "message": "Must be between 1 and 100." }
  ]
}
```

## Step 5 — Conditional writes (ETag / If-Match)

Any resource that two clients might edit concurrently gets an `ETag`. It is
one header on read and one check on write, and it eliminates a whole class of
lost updates.

```http
GET /v1/orders/42
→ 200
   ETag: "v7-9f2c1a"
   Last-Modified: Tue, 03 Mar 2026 10:15:00 GMT
```

```http
PATCH /v1/orders/42
If-Match: "v7-9f2c1a"
Content-Type: application/json

{ "status": "shipped" }
→ 412 Precondition Failed          (someone else wrote first)
→ 200 with the new ETag             (you won)
```

Implementation, using a row `version` column:

- `ETag: "v<version>-<hash of updated_at>"` — or just `"7"`. It only has to
  change when the representation changes.
- `If-Match: *` means "only if it exists". Use it to make a create-or-replace
  `PUT` safe.
- **If the `If-Match` value does not match, fail the whole write with `412` —
  do not silently retry the update on top of the new version.** A silent retry
  is a lost update wearing a disguise.
- Clients must read the new `ETag` from your response. A client that reuses a
  stale `ETag` forever will get `412` on every write — that is a client bug,
  and your `detail` message should say "resource changed; re-read and re-apply".
- `If-None-Match: *` on `GET` means "304 Not Modified if it exists" — the
  cheap way to implement a cache revalidation.

## Step 6 — Idempotency keys

`PUT` and `DELETE` are idempotent by the spec. `POST` is not, and retries on
unreachable-but-processed `POST`s are the single most common source of
duplicate charges and duplicate rows. Fix it with a client-supplied key.

```http
POST /v1/payments
Idempotency-Key: 8f14e45f-ceea-467a-9c2a-6b3f1c9d0e77
Content-Type: application/json

{ "amount": 1250, "currency": "USD", "order_id": "01J8Z..." }
```

Server rules:

1. Scope the key to the authenticated principal. Two tenants must never share
   a key namespace.
2. Store `(principal, key) -> (request_fingerprint, status_code, response_body,
   created_at)` and set a TTL. **24 hours is a sane default**; match it to your
   retry window, not to your data retention.
3. Replay the *stored* response for a repeat key, including the original status
   code. Return `409` if the same key arrives with a *different* body — that is
   a client bug and silently returning the old result hides it.
4. Insert the key **in the same transaction** as the effect. If you write the
   key first and the transaction later fails, a legitimate retry is now
   permanently rejected.
5. Return the key back in a response header so a client debugging a duplicate
   can find it.

Key format: a UUIDv4 or a high-entropy random string. `Idempotency-Key:
<order_id>` is not a key, it is a collision waiting for a retry after a
partial failure.

## Step 7 — Content negotiation and versioning

**Negotiate on need, not on decoration.** If you ship JSON only, say so in
`Content-Type` and support `Accept-Charset: utf-8` implicitly. Add negotiation
for `Accept` (JSON vs XML) and `Accept-Language` only when a client actually
needs it, and return `406 Not Acceptable` when you cannot satisfy `Accept`.
Always honour `Content-Type` on writes and return `415` for an unsupported one.

Pagination parameters (`limit`, `cursor`, `sort`) belong in the query string;
they are not a second content type. See
`skills/api-design/pagination-and-filtering/SKILL.md` for the cursor design.

**Versioning.** Default: **do not version until you have to.** A field a client
ignores is not a breaking change. Break a client only by removing or renaming a
field, changing a type, tightening validation, or changing a status code or
error `code`.

When you must break things, prefer, in order:

1. **Additive change** — new optional field, new endpoint. Free.
2. **Parallel running** — new field alongside the old one, deprecated in docs,
   sunset on a stated date.
3. **Media-type versioning** — pin the version to the URL path
   (`/v1/...`, the pragmatic default) or to a custom media type parameter
   (`Accept: application/vnd.example.order+json; version=2`). Path versioning
   is what most clients, proxies, and humans handle well; media types are
   cleaner but you must accept `Accept` headers on every single request and
   many gateways drop the parameter.
4. **A new major domain** — `api.example.com/v2` when the shape changed too
   much to carry.

Whatever you pick: publish a machine-readable changelog and a per-version
`sunset` date, and answer `Deprecation: true` and `Sunset: <HTTP-date>`
headers on the deprecated version (RFC 8594). A deprecation nobody can
discover is not a deprecation.

## Output template

When asked to design an API, emit this, in this order:

```markdown
## Resources
| Resource | Identifier | Notes |

## Endpoints
| Method | Path | Success | Auth | Idempotent | Errors |
|---|---|---|---|---|---|

## Error contract
(RFC 9457 example, Content-Type, and the field semantics)

## Concurrency
Which resources carry ETag, and what If-Match does on each write path.

## Idempotency
Which POST endpoints require Idempotency-Key, the TTL, and the fingerprint rule.

## Versioning policy
What counts as breaking here, and the deprecation mechanism.
```

## Gotchas

- **`400` is for malformed syntax, `422` is for well-formed but semantically
  invalid, `409` is for a conflict with current state.** Most teams pick one and
  then clients cannot tell a retryable failure from a permanent one. Pick all
  three and use them.
- **Never return `200` with `{"success": false}`.** Status code and body must
  agree. Clients (and every proxy, retry policy, and monitoring dashboard)
  branch on the status.
- **`403` leaks existence.** `GET /orders/{someone_elses_id}` returning `403`
  confirms the order exists. Return `404` unless the caller has a legitimate
  need to know.
- **A `500` with a stack trace in the body is a vulnerability**, not a
  debugging aid. Log the trace with the `trace_id`; return the `trace_id`.
- **`Location` on a `201` should be fetchable with the same credentials.** If
  creation returns a resource the client then cannot `GET`, you have built two
  auth paths.
- **Field-level `PATCH` with `null` is ambiguous.** Decide and document: JSON
  Merge Patch (`application/merge-patch+json`, RFC 7396) means `null` clears
  the field and `null` is not a value; JSON Patch (`application/json-patch+json`,
  RFC 6902) is an operation list and is exact but verbose. Pick **one** per
  endpoint and set the `Content-Type` to say which. If you accept plain
  `application/json` for `PATCH`, you have silently chosen "null means null"
  and your clients cannot clear a field.
- **Bulk endpoints are not free.** `POST /orders/bulk` needs a per-item result
  array, a partial-failure mode, and its own idempotency story. Prefer a `GET`
  plus per-item `POST`s under a queue until the volume justifies it.
- **Long operations return `202` plus a status resource**, not a `200` with
  `{"status": "processing"}` that the client has to poll with an undocumented
  interval.
- **Delete-vs-GDPR:** a hard delete must be documented as
  asynchronous, and a "deleted" tombstone that 404s on `GET` is a common and
  much cheaper approach than deleting rows a foreign key still points at.

## Safety notes

- Additive changes only. Do not remove or rename a published field, change a
  type, or tighten validation on an existing endpoint without an explicit
  version bump and a written migration note for clients.
- When reviewing an existing API, report the exact fields whose semantics
  would change for existing clients. Do not silently "clean up" a published
  contract.
- `Idempotency-Key` storage is a write to your database. Say so before adding
  it to a hot endpoint, and give the TTL.

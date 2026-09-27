---
name: rest-api-implementation
description: Implement a robust REST API in a server framework - request validation, one error response shape, correct status codes, idempotency keys, pagination, and request/response middleware. Use when actually building endpoints (Fastify, Express, Nest, Flask, Spring, Go) and not just designing the contract, or when a user says "implement this endpoint", "validate the request body", "return the right status code", "add pagination", "idempotency", "rate limit middleware". Triggers on "build the endpoint", "POST handler", "request validation", "error middleware", "status code", "cursor pagination", "Idempotency-Key", "API implementation".
compatibility: Framework-agnostic with Fastify/Express and Flask examples; HTTP/1.1 semantics, RFC 9457 problem details, RFC 8594 deprecation. Pair with the api-design/rest-api-design skill for contract decisions.
metadata:
  version: "1.0"
---

# REST API Implementation

This is the **build** side: given a contract, produce endpoints that validate
input, return one consistent error shape, use correct status codes, handle
retries idempotently, paginate, and layer middleware cleanly. Contract
decisions (resource modelling, versioning strategy, identifier style) live in
`api-design/rest-api-design`; implement what it decides.

## Workflow

- [ ] 1. Define the request/response schemas once and **validate every
      request** at the edge (no unvalidated data reaches a handler)
- [ ] 2. One global error handler that emits RFC 9457 problem details for
      every failure
- [ ] 3. Status codes match the outcome (`201`+`Location`, `409` on conflict,
      `422` on invalid fields, `429`+`Retry-After`, `5xx` never leaks internals)
- [ ] 4. `Idempotency-Key` on every non-idempotent mutation that matters
- [ ] 5. Cursor (or keyset) pagination on every list endpoint, with a bounded
      `limit`
- [ ] 6. Middleware: request id → logging → body parse/validate → auth →
      rate limit → handler → error
- [ ] 7. Contract tests against the real server (status, shape, headers)

## Request validation at the edge

Every request field is untrusted. Validate **before** the handler runs, reject
with all problems at once, and never partially apply a request.

```python
# Fastify: schema in, validated data out. Handler only sees valid data.
from pydantic import BaseModel, Field, ValidationError

class CreateOrder(BaseModel):
    model_config = {"extra": "forbid"}          # typo'd fields 422, not ignored
    sku: str = Field(min_length=1, max_length=64)
    qty: int = Field(ge=1, le=1000)
    notes: str | None = Field(default=None, max_length=500)

app.post("/orders")
async def create_order(body: CreateOrder, request):   # Fastify parsed+validated
    order = await service.create(body)                  # body is trustworthy
    return JSONResponse(status=201, content=order, headers={"Location": f"/orders/{order.id}"})
```

- **Never trust `request.body` inside a handler.** Parse and validate it in
  middleware or a typed framework (Fastify's schema validation, Nest's
  `ValidationPipe`, Spring's `@Valid`, Zod in Express). The handler receives
  a validated, typed object.
- **Reject unknown fields** (`extra="forbid"` / `additionalProperties: false`)
  so a client typo (`emial=`) is a clear `422`, not a silently dropped field.
- **Report every field problem at once** with the field path, not just the
  first. `loc` is a tuple for nested fields (`body.address.zip`).
- **Enforce a body-size limit** before parsing (see Gotchas) and a
  content-type check (`415`).
- Validate **path params and query params too** — `/orders/{id}` with
  `id: "abc"` should `400`/`422`, not reach the database.

## One error shape: RFC 9457 problem details

Every error, from every layer, is the same shape. One global handler, not
per-endpoint try/catch.

```python
# Global error handler. All failures funnel here.
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
import uuid

PROBLEM = "application/problem+json"

@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    rid = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception("unhandled", extra={"request_id": rid}, exc_info=exc)
    return problem(500, "Internal server error",
                   detail="An unexpected error occurred.",
                   trace_id=rid)     # client can quote this; internals are logged only

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return problem(exc.status_code, exc.detail, trace_id=request.state.request_id)
```

```json
{
  "type": "https://api.example.com/problems/validation-failed",
  "title": "Validation failed",
  "status": 422,
  "detail": "The request body has 2 invalid fields.",
  "instance": "/v1/orders",
  "trace_id": "0af7651916cd43dd8448eb211c80319c",
  "errors": [
    { "field": "sku",  "code": "too_short", "message": "Must be at least 1 character." },
    { "field": "qty",  "code": "out_of_range", "message": "Must be between 1 and 1000." }
  ]
}
```

- Send `Content-Type: application/problem+json` for **all** errors, including
  `400` and `500`.
- `type` is a stable URI clients branch on; `code` per field is machine
  readable; `detail` is human prose and must **never** contain a stack trace,
  SQL, an internal hostname, or another user's data. Log the internals with the
  same `trace_id` you return.
- Include `trace_id` (or `X-Request-Id` echoed in a header) on every response
  so a user-reported failure is traceable to a log line.
- A `5xx` returns a generic message; the real cause is in the logs, correlated
  by `trace_id`. A `500` with a stack trace in the body is a vulnerability.

## Status codes — the implementation decisions

Status codes are part of the contract, not decoration. The common mistakes and
the fix:

| Situation | Do | Not |
|---|---|---|
| Created a resource | `201` + `Location` header + body | `200` with no location |
| Accepted for async work | `202` + a status resource to poll | `200 {"status":"processing"}` |
| Success, no body | `204`, **no** body, no `Content-Type` | `200` with `null` |
| Already deleted, delete again | `204` (idempotent) | `404` (surprising to clients) |
| Malformed JSON / bad syntax | `400` | `422` |
| Well-formed but invalid field | `422` with per-field `errors` | `400` (loses the field detail) |
| Conflict with current state (duplicate email, bad state transition) | `409` | `422` (client won't retry; 409 signals "state changed") |
| Unauthenticated | `401` + `WWW-Authenticate` | `403` |
| Authenticated but not allowed | `403` | `401` |
| Hiding existence from an unauthorized caller | `404` | `403` (which leaks that it exists) |
| Body too large | `413` | `400` |
| Rate limited | `429` + **`Retry-After`** (required) | `429` bare |
| Dependency down | `503` + `Retry-After` if known | `500` |
| Anything unexpected | `500`, generic body, logged | `500` + internals |

- `POST` that creates returns `201`; the `Location` header must be fetchable
  with the **same credentials** as the create.
- Never `200 OK` with `{"success": false}` in the body — status and body must
  agree, or every proxy, retry policy, and dashboard branches wrongly.
- Choose `400` vs `422` vs `409` once and apply it: `400` = the request is
  malformed (could not even parse); `422` = parsed but a value is invalid;
  `409` = valid but conflicts with current state. The whole point is letting a
  client tell "retrying might help" from "this will never work".

## Idempotency for non-idempotent mutations

`PUT`/`DELETE` are idempotent by definition; `POST` is not. Any `POST` that
charges, sends, creates, or triggers a side effect needs an `Idempotency-Key`
header, because a client retrying an unreachable-but-processed `POST` is the
top cause of duplicate charges and duplicate rows.

```python
@app.post("/payments")
async def create_payment(request: Request, body: CreatePayment):
    key = request.headers.get("Idempotency-Key")
    if not key:
        return problem(400, "Idempotency-Key header required for this endpoint")

    fingerprint = sha256(canonical_json(body))    # same key, different body?
    prior = await idempotency.get(key)
    if prior:
        if prior.fingerprint != fingerprint:
            return problem(409, "Key reused with a different request body")
        return prior.response                    # replay the ORIGINAL status + body

    # Insert key AND effect in ONE transaction so a rollback can't orphan the key.
    async with db.transaction():
        await idempotency.insert(key, fingerprint)
        result = await payments.create(body)
    return JSONResponse(status=201, content=result)
```

Server rules:

1. Scope the key to the authenticated principal — two tenants must never share
   a key namespace.
2. Store `(principal, key) → (request fingerprint, status, response body,
   created_at)` with a TTL matched to the client retry window (**~24h** is a
   sane default), not to your data retention.
3. Replay the **stored response including the original status code**.
4. Return `409` if the same key arrives with a **different** body — that is a
   client bug and silently returning the old result hides it.
5. Insert the key **in the same transaction** as the effect; if you write the
   key first and the transaction later fails, a legitimate retry is now
   permanently rejected.
6. The key must be high-entropy (UUIDv4). `Idempotency-Key: <order_id>` is a
   collision waiting to happen.

`If-Match`/`ETag` is the sibling mechanism for **lost updates**: any resource
two clients may edit carries an `ETag`; a `PATCH` with a stale `If-Match` gets
`412`. Do **not** silently retry the update on top of the new version — that
is a lost update in disguise.

## Pagination on every list endpoint

```sql
-- Keyset / cursor: O(limit), stable under concurrent inserts, no OFFSET scan.
SELECT id, created_at FROM orders
WHERE (created_at, id) < (:cursor_created_at, :cursor_id)   -- tuple comparison
  AND status = :status
ORDER BY created_at DESC, id DESC
LIMIT :limit_plus_one;   -- fetch one extra to know if there's a next page
```

- **Never `OFFSET` for pagination** past a few pages: `OFFSET 100000` scans and
  discards 100k rows (O(offset)) and shifts under concurrent writes (a row
  inserted at the top makes an item repeat). Use a **cursor** (the last row's
  sort key) or keyset.
- The cursor must encode the **full sort key**, not just the id, and be opaque
  to the client (base64 of `{created_at, id}`). Expose it as `next_cursor`.
- Fetch `limit + 1`; if you got the extra row, there is a next page — set
  `next_cursor` to the last returned row's key; if not, `next_cursor` is
  `null` and the client stops. Bound `limit` with a server maximum
  (`limit: int = 20, le: 100`) and default it.
- Return an explicit envelope so adding fields is not breaking:
  `{"data": [...], "next_cursor": "...", "has_more": true}`. (Design choices —
  offset vs cursor, filtering — are in `api-design/pagination-and-filtering`.)
- Sort order must be **total and deterministic** (always include a tiebreaker
  like `id`), or rows can repeat or vanish between pages.

## Middleware: order and responsibilities

Order matters. A canonical stack, outermost first:

1. **Request id** — generate or accept `X-Request-Id`, put it on the request
   state and the response, use it in every log line and in the error body.
2. **Logging** — method, path, status, duration, request id. Log **after** the
   response so you have the status and duration. Do not log bodies (secrets,
   size) by default.
3. **CORS** — must be before anything that can short-circuit, and must handle
   `OPTIONS` preflight with `204` + `Access-Control-Allow-*`.
4. **Body limit + content-type** — reject oversized (`413`) and wrong-type
   (`415`) **before** buffering the whole body. This is the DoS guard.
5. **Compression** — only if the client sent `Accept-Encoding`; skip for
   already-compressed types.
6. **Auth** — authenticate, put the principal on the request; authorise
   **per resource** in the handler (a valid token does not grant access to
   *this* object).
7. **Rate limit** — per principal/IP; return `429` + `Retry-After` + standard
   rate-limit headers (`X-RateLimit-Limit/Remaining/Reset` or RFC 9331
   `RateLimit-*`).
8. **Handler** — assumes a validated body and an authenticated principal.
9. **Global error handler** — outermost, so it catches everything below.

```python
# Express: order and the "async errors must be forwarded" gotcha.
app.use(requestId);
app.use(logging);
app.use(express.json({ limit: "100kb" }));     // 413 past this
app.use(rateLimit);                            // 429 + Retry-After
app.get("/orders/:id", wrap(async (req, res) => {   // wrap forwards rejections
    const order = await service.get(req.params.id);
    if (!order) return res.status(404).problem({ type: ".../not-found", title: "Not found" });
    res.json(order);
}));

// Express 4: an async handler that throws does NOT reach the error middleware
// (no auto-catch) -> unhandled rejection / hung request. Either upgrade to
// Express 5 (which auto-catches) or wrap every async handler to call next(err).
function wrap(fn) { return (req, res, next) => fn(req, res).catch(next); }
```

- **Authn (who) and authz (what) are different.** Authenticate once in
  middleware; authorise the specific resource in the handler, and return
  `404` (not `403`) when hiding existence from an unauthorized caller.
- **Rate-limit headers are part of the contract.** A `429` without
  `Retry-After` leaves the client guessing.
- Error middleware must be registered **last** and be **4-arity** in Express
  (`(err, req, res, next)`) or it is treated as a normal middleware and never
  fires on errors.

## Gotchas

- **Express 4 does not catch async errors.** A rejected promise in an `async`
  handler is an unhandled rejection, not a 500 — the request hangs or the
  process dies. Wrap handlers or use Express 5. This is the single most common
  Express bug.
- **Body size limit before parse.** `express.json({ limit: "100kb" })`;
  Fastify `bodyLimit`; otherwise a single request can exhaust memory
  (`PayloadTooLargeError`).
- **Serialise the response once.** Building the body with `JSON.stringify` /
  `res.json` on a large collection duplicates memory; stream or paginate.
  Avoid an N+1 that assembles the body by querying per item.
- **`204` must have no body** — a framework that appends a body (or a proxy
  that adds one) breaks strict clients.
- **`Location` on a `201` should be `GET`-able** with the same auth; a location
  the client cannot read forces a second auth path.
- **Timestamps in RFC 3339 UTC** (`2026-03-03T10:15:00Z`); money in integer
  minor units + ISO 4217 currency, never floats. These are response-contract
  details that bite clients later.
- **Never leak internals in `detail` or a `5xx`**: no stack traces, SQL,
  internal hostnames, or another user's data. Log with the `trace_id`.
- **Middleware registered after routes does not run for those routes** (order
  is registration order in Express) — a common "my auth check isn't firing".
- **A `POST` handler that both creates and returns `200`** forces clients to
  guess whether to `GET` the resource or re-`POST` (and re-create it). Return
  `201`+`Location`.
- **Enum/union path params** (`status` in a query) must be validated against
  the allowed set at the edge; an unknown value is a `422`, not a silent empty
  result or a 500.
- **Set the correct `Content-Type` on every response** (including problem
  details) and a `Vary` header when CORS/auth affects the cache.

## Definition of done

- [ ] Every request field, path param, and query param validated at the edge;
      unknown fields rejected
- [ ] One global error handler; RFC 9457 shape; `trace_id` on every response
- [ ] Status codes match the table; `Location` on `201`; `Retry-After` on
      `429`/`503`
- [ ] `Idempotency-Key` on every non-idempotent mutation that has a side
      effect; `ETag`/`If-Match` on any concurrently-editable resource
- [ ] Every list endpoint uses bounded cursor pagination with a total sort
- [ ] Middleware in the canonical order; async errors reach the error handler
- [ ] Contract tests assert status, shape, headers, and the error body

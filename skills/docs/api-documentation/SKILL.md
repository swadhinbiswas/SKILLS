---
name: api-documentation
description: Document an API so an independent consumer can integrate against it without reading the server code - complete endpoint reference, real request/response examples, an error catalogue, auth, rate limits, pagination, versioning and deprecation. Use when writing or reviewing API docs, an OpenAPI spec, a "how do I call this" question, or when a consumer keeps getting 422s they did not expect.
compatibility: OpenAPI 3.1 is the default source of truth; the manual reference is still written for humans.
metadata:
  version: "1.0"
---

# API Documentation

Success is measurable and binary: **a competent developer who has never seen
the service can integrate against it, unassisted, without reading server
code or asking a question in a channel.** Everything here serves that.

The most common failure is documentation that is *descriptive* (a list of
fields) rather than *operational* (here is a call that works, here is what
happens when it does not).

## What a consumer needs, in the order they need it

1. **Can I call this at all?** Base URL, auth (how to get a credential, what
   scope, where to send it), content types, and the smallest complete example.
2. **What does each endpoint do?** One line per endpoint, method, path, purpose.
3. **How do I do my task?** Per-endpoint: every parameter, a real request, a
   real success response, every error response, and the idempotency/retry
   contract.
4. **What will bite me?** Rate limits, pagination, versioning, webhooks, known
   limitations.

## The endpoint reference

One section per endpoint, in this order. Consistent shape is what makes a
reference scannable.

### `POST /v1/orders` — Create an order

Creates an order in `pending_payment` state. Idempotent when an `Idempotency-Key`
header is supplied (see [Idempotency](#idempotency)).

**Path parameters:** none.
**Query parameters:** none.

**Request body** (`application/json`):

| Field | Type | Required | Constraints | Description |
|---|---|---|---|---|
| `customer_id` | string | yes | `^cus_[A-Za-z0-9]{16}$` | Customer placing the order. |
| `items` | array | yes | 1–200 entries | Line items; see below. |
| `items[].sku` | string | yes | max 64 chars | Product SKU. Must be active. |
| `items[].quantity` | integer | yes | 1–999 | Units of this SKU. |
| `items[].unit_price_minor` | integer | no | ≥ 0 | Overrides the catalogue price. Requires `items[].price_override=true` on the customer. |
| `currency` | string | yes | ISO 4217, lowercase | `usd`, `eur`, `gbp`. Must match the customer's settlement currency. |
| `metadata` | object | no | ≤ 20 keys, ≤ 4 KB total | Echoed back on the order. **Not** returned on list endpoints. |

**Example request:**

```bash
curl -X POST https://api.example.com/v1/orders \
  -H "Authorization: Bearer $API_KEY" \
  -H "Idempotency-Key: 7c9e6679-7425-40de-944b-e07fc1f90ae7" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "cus_a1b2c3d4e5f60718",
    "currency": "usd",
    "items": [
      {"sku": "TSHIRT-M-BLK", "quantity": 2},
      {"sku": "CAP-OSY", "quantity": 1, "unit_price_minor": 1500}
    ],
    "metadata": {"cart_id": "cart_9931"}
  }'
```

**Response `201 Created`:**

```json
{
  "id": "ord_01HZX4K2M9",
  "status": "pending_payment",
  "customer_id": "cus_a1b2c3d4e5f60718",
  "currency": "usd",
  "subtotal_minor": 4500,
  "tax_minor": 383,
  "total_minor": 4883,
  "items": [
    {"sku": "TSHIRT-M-BLK", "quantity": 2, "unit_price_minor": 1500, "line_total_minor": 3000},
    {"sku": "CAP-OSY", "quantity": 1, "unit_price_minor": 1500, "line_total_minor": 1500}
  ],
  "created_at": "2026-03-04T14:02:11Z",
  "links": {"self": "https://api.example.com/v1/orders/ord_01HZX4K2M9",
            "payment_intent": "https://api.example.com/v1/payment_intents/pi_3Qa"}
}
```

**Response headers on `201`:** `Location` (order URL), `X-RateLimit-Limit`,
`X-RateLimit-Remaining`, `X-RateLimit-Reset`, `Idempotency-Replayed: true|false`.

**Errors:** see the [error catalogue](#error-catalogue). This endpoint can
return `400`, `401`, `403`, `404` (unknown `customer_id`), `409`, `422`,
`429`, `500`, `503`.

**Notes and constraints:**

- Prices are in **minor units** (cents). `1500` is $15.00. Never a float.
- An empty `items` array returns `422`, not `400` (it is structurally valid
  but semantically empty).
- The order is created in `pending_payment` and holds inventory for 15 minutes.
  Payment is a separate call.

Real examples beat prose. The example above is copy-pasteable; the note about
minor units is the kind of thing a schema cannot say.

## The error catalogue

One page for the whole API. Consumers will read only this section.

Every error response has the same shape:

```json
{
  "error": {
    "type": "invalid_request",
    "code": "item_sku_not_found",
    "message": "SKU 'CAP-OSY' does not exist or is inactive.",
    "param": "items[1].sku",
    "request_id": "req_01HZX4K2M9PQ",
    "docs_url": "https://docs.example.com/errors/item_sku_not_found",
    "retryable": false
  }
}
```

| Field | Meaning |
|---|---|
| `type` | Broad category: `invalid_request`, `authentication`, `permission`, `not_found`, `conflict`, `rate_limit`, `server`. Stable. |
| `code` | Specific, machine-readable, stable identifier. Switch on this, never on `message`. |
| `message` | Human-readable, for developers. **Not** stable; may change. Never parse it. |
| `param` | The offending field, in a path notation (`items[1].sku`). Absent when not field-specific. |
| `request_id` | Quote this in support. Also the log correlation id. |
| `retryable` | Whether an identical retry could succeed. |

Code standards:

- **`type` → status mapping is fixed**: `invalid_request`→400,
  `authentication`→401, `permission`→403, `not_found`→404, `conflict`→409,
  `unprocessable`→422, `rate_limit`→429, `server`→500/503.
- **Never a bare `{"error": "Bad request"}`.** It forces the consumer to match
  on a string.
- **`message` must not leak internals.** No stack traces, no SQL, no hostnames,
  no "an unexpected error occurred" with a 500. A 500 says
  `code: "internal_error"` and nothing else.
- **Add codes, never change them.** A removed or repurposed `code` is a
  breaking change, because consumers switch on it.

## Authentication and authorization

- **How to get a credential**, with a real command:
  ```bash
  curl -X POST https://api.example.com/v1/tokens \
    -H "Authorization: Bearer $API_KEY" \
    -d '{"scope": "orders:write", "ttl": "3600s"}'
  # -> {"access_token": "tok_...","expires_at":"2026-03-04T15:02:11Z"}
  ```
- **Where it goes**, and what happens if it is wrong: missing → `401` with
  `code: "missing_authorization_header"`; malformed → `401`
  `invalid_token`; expired → `401` `token_expired` with
  `WWW-Authenticate: Bearer error="invalid_token"`.
- **Scopes**, as a table: which scope each endpoint needs, and what a
  missing-scope `403` says. `403` means *authenticated but not allowed*; the
  message must distinguish it from `401` so the consumer knows whether to get
  a new token or ask for more access.
- **Never** document "contact us for an API key" without saying what the key
  can do and a test mode.

## Rate limits

Document the numbers, the window, the scope, and the *response* to exceeding
them. Vagueness here is the single most common integration complaint.

- **Limit and window**: `1000 requests per minute` per API key, per endpoint
  group. Say which.
- **Headers on every response**: `X-RateLimit-Limit`, `-Remaining` (requests
  left in the current window), `-Reset` (unix seconds when the window resets).
- **On `429`**: `Retry-After` in seconds; `code: "rate_limit_exceeded"`;
  `retryable: true`. Honour `Retry-After`; do not retry sooner.
- **Bursts**: a short burst allowance (e.g. an extra 100) is fine — say so, or
  integrators will over-buffer and get a `429` for the wrong reason.
- **Concurrent-request cap**, if you have one, and the `429` for it. This one
  catches people out badly because their load tests pass and production
  doesn't.
- **Behaviour under limit**: is it a hard reject or a queue? A queued request
  that returns `201` eight seconds later is a different integration problem
  from a `429`.

## Pagination

Use cursor pagination for anything that can change while a consumer pages
through it. Offset pagination on a mutable collection silently skips and
repeats rows.

- **Default and maximum page size**; what happens if the consumer asks for
  more (clamp, or `400`? say which).
- **The response envelope**:
  ```json
  {"data": [ ... ], "has_more": true, "next_cursor": "eyJpZCI6Im9yZF8wMUhZWDRLMk09"}
  ```
- **`next_cursor` is opaque**; never document its format or let consumers
  construct one. Cursors are often base64 — say "opaque" and mean it, or they
  will start parsing them and break when you change the encoding.
- **Stability**: cursors are valid for 24 h; after that, `400`
  `cursor_expired` — restart from the beginning.
- **What order** the results are in, explicitly, and that it is the only
  guarantee. "Insertion order" is a promise; "by `created_at desc, id desc`"
  is a promise. "Best effort" is a bug waiting to happen.

## Versioning and deprecation

**Default: URI major version** (`/v1/...`), additive minor changes within a
major. It is visible, routable, and trivially testable.

Document the compatibility contract explicitly:

| Change | Breaking? | Policy |
|---|---|---|
| New endpoint, new optional field in a response | No | Shipped immediately; announced in the changelog |
| New **enum value** in a response | **Yes, for strict clients** | Announce ≥ 90 days ahead, or send the new value only behind a capability flag |
| New required request field | Yes | New endpoint version or a new endpoint; never silently |
| Removing/renaming a field, changing a type, tightening validation | Yes | New major version |
| Changing an error `code` or its meaning | Yes | Never; add instead |
| Tightening rate limits | Yes, effectively | Never within a major version |

**Deprecation is a process, documented in advance.** State, in the docs:

1. **Deprecation header on every response** from a deprecated endpoint:
   `Deprecation: true`, `Sunset: Tue, 04 Nov 2026 00:00:00 GMT`, and
   `Link: <https://docs.example.com/migrations/v1-to-v2>; rel="deprecation"`.
2. **A migration guide** per deprecated version, with before/after code.
3. **A minimum notice period** (90 days is a reasonable house default) and
   **email + dashboard metric** on usage, so you deprecate what nobody uses and
   can find the consumers who matter.
4. **Runtime warning for the partner**, not just a doc page: log or return a
   `Warning` header naming the replacement. The consumers who will break are
   the ones who will not read your changelog.

## Webhooks, if you have them

- Event catalogue with a **stable `type` string and a versioned payload
  schema** (`order.paid.v1`).
- **Retry policy**: how many attempts, over what period, with what backoff, and
  what the endpoint must do to acknowledge (return `2xx` within N seconds;
  anything else is a retry).
- **Signature verification** — a real snippet, not "verify the signature":
  ```python
  # HMAC-SHA256 over the raw body, header Stripe-Signature: t=...,v1=...
  expected = hmac.new(secret.encode(), f"{t}.{raw_body}".encode(), hashlib.sha256).hexdigest()
  ok = hmac.compare_digest(expected, v1)
  ```
  And it must be over the **raw** body, before JSON parsing.
- **Ordering and duplicates are not guaranteed.** Say so, and say the consumer
  must be idempotent on `event.id`.
- **Ordering of delivery versus the API's own eventual consistency** — a
  consumer that gets `order.paid` then GETs the order and sees
  `pending_payment` is a real bug; document the propagation delay.

## Tooling: OpenAPI as support, not the point

**OpenAPI 3.1 (`openapi.yaml`) is the source of truth** and is validated in CI.
It is the substrate — but nobody integrates from a rendered schema page; they
integrate from prose, examples, and the error catalogue. A spec with no
examples and no error definitions is a spec nobody can use.

Rules:

- **`openapi.yaml` in the repo, linted in CI** (`spectral lint` or `redocly
  lint`), with `operationId`s stable (consumers generate clients from them —
  changing an `operationId` breaks every generated SDK, so it is a breaking
  change even in a "no-breaking-changes" release).
- **Every schema has `example`; every operation has `request` and response
  `examples` with values copied from a real test run**, not invented.
- **`description` fields written for a human** (the `param` semantics, the
  "why"), not just the type.
- **Generate the SDK/docs from the spec, but keep the hand-written guides**
  (quickstart, error catalogue, migration guides) in version-controlled
  markdown. Generated error tables are poor; hand-written ones are excellent.
- **Contract-test the spec against the running service** (e.g. Schemathesis
  or `dredd`), so a documented-but-unimplemented status code fails CI rather
  than a consumer's week.

## Gotchas

- **The documented error list is always optimistic.** Docs are written when
  the happy path is built; the 500s and edge cases get added later and never
  documented. Reject an endpoint whose errors can't be enumerated.
- **Status code 200 for everything** is common and destroys a consumer's ability
  to handle failure. `400` for a bad body, `401` unauthenticated, `403`
  authenticated-but-not-allowed, `404` missing, `409` conflict, `422` valid
  shape but invalid state, `429` throttled. Pick once and document it.
- **`401` vs `403` is ambiguous in most APIs and never documented.** Say which
  one you return for an expired token versus a valid token lacking the scope.
  Consumers branch on this.
- **Timestamps without a timezone** are a data-corruption bug waiting to
  happen. Say UTC, always, in ISO 8601 (`2026-01-31T09:00:00Z`).
- **Money as a float** breaks silently at scale. Document minor units
  (cents), and whether the currency is fixed per endpoint or in a field.
- **Pagination with `offset` and no total count** forces consumers to guess.
  Document whether a total is available, what the max page size is, and what
  happens on the last page.
- **Rate limits documented as "fair use"** are unenforceable. Give numbers, the
  window, the scope (per key? per IP? per endpoint?), the headers you send, and
  exactly what to do on `429` — including whether `Retry-After` is present.
- **Examples that were never run** are the single biggest source of wasted
  consumer time. Generate examples from recorded responses, or contract-test
  them. A pasted curl block that 404s is worse than no example.
- **A field's units or format is assumed, not stated.** "duration: 300" —
  seconds or milliseconds? "size": bytes or KB? State it in the field
  description; a wrong guess can corrupt a client's data, not just display.
- **Deprecation announced in prose only** is invisible to tooling. Add the
  `Deprecation` and `Sunset` headers, give a date, and link a migration guide.
  "Will be removed soon" is not a deprecation policy.
- **Generated reference docs are complete and useless.** They document every
  parameter nobody needs and explain none of the task. Keep the generated
  reference for completeness, and hand-write the quickstart, the error
  catalogue, and the migration guides.
- **Documenting a webhook without the signature algorithm** forces consumers to
  guess. Name the algorithm, show verification over the *raw* body (not a
  re-serialised object), and state whether delivery is at-least-once.

## Review checklist

- [ ] Base URL, auth (how to obtain, where to send, what failure looks like),
      and content types in the first screen.
- [ ] A smallest-complete-example that works on a fresh machine.
- [ ] Every endpoint: purpose, auth scope, every parameter with type /
      required / constraints / default, a real request, a real success
      response with headers, and every error it can return.
- [ ] Prices/quantities/units stated (minor units, ISO codes, UTC timestamps).
- [ ] Error catalogue: consistent envelope, `type`↔status mapping, stable
      `code`s, no internal leakage, `request_id` in every response.
- [ ] Rate limits: numbers, window, scope, headers, `429` behaviour,
      concurrency cap.
- [ ] Pagination: cursor opacity, page-size default and max, ordering
      guarantee, cursor expiry.
- [ ] Versioning policy table, deprecation headers, minimum notice period, and
      a migration guide.
- [ ] Webhooks: signature snippet over the raw body, retry schedule,
      at-least-once / idempotency statement.
- [ ] `openapi.yaml` lints clean, examples are from real runs, and the
      contract test passes.

# Status code reference

Decision procedure, per-situation codes, and the traps. Read this when a status
is not obviously covered by the table in `SKILL.md`.

## The decision procedure

Work down this list and stop at the first match.

1. **The request never left the client's stack, or the server rejected it
   before doing any work** (malformed syntax, bad `Content-Type`, unparseable
   body) → `400` or `415`.
2. **The request is syntactically fine but fails a business rule that does not
   depend on current state** (`quantity` must be ≤ 100) → `422`.
3. **The request conflicts with the current state of the world** (email
   already registered, order already shipped, version mismatch) → `409`.
4. **A precondition header was present and did not hold** → `412`.
5. **A precondition header was required and absent** → `428`.
6. **The caller cannot be identified or is not permitted** → `401` / `403`.
7. **The thing is not there, or you are hiding it** → `404`.
8. **The request is well-formed but the rate limit is hit** → `429` +
   `Retry-After`.
9. **You accepted the work but cannot do it now** → `503` + `Retry-After`.
10. **Anything else that went wrong on your side** → `500`.

If two codes seem right, prefer the one that tells the client whether **retrying
can help**. That is the only question every client, proxy, and retry library
actually has.

## 2xx

| Code | Use | Traps |
|---|---|---|
| `200 OK` | Successful read, or a mutation that returns a body | Most common default; do not use for async work |
| `201 Created` | `POST` that produced a resource | Add `Location`. Body is optional but send it — clients then avoid a second round trip |
| `202 Accepted` | Work started, result not ready | Must include a way to observe progress. `202` with no status resource is a black hole |
| `204 No Content` | Successful, no body | Do not send a body, and do not send `Content-Type`. Some clients error on a body here |
| `206 Partial Content` | `Range` requests (byte ranges) | Not the same as a partial `PATCH` |

`203 Non-Authoritative Information` exists for a proxy-modified response.
`205 Reset Content` is effectively unused. You will never need either.

## 4xx — the ones that get mixed up

### 400 vs 422 vs 409

| | Meaning | Client can fix by | Example |
|---|---|---|---|
| `400 Bad Request` | The server could not parse or understand it | Changing syntax | Invalid JSON, malformed query string, bad UUID in the path |
| `422 Unprocessable Content` | Parsed it; the values violate a rule that is always true | Changing values | `{"quantity": -1}`, missing required field, `email` not an email |
| `409 Conflict` | Valid request, clashes with current state | Changing state or retrying later | Email taken, `{"status": "shipped"}` on an already-shipped order, `If-Match` failure on a non-`412` path |

The RFC status name is now `422 Unprocessable Content`; it was
`422 Unprocessable Entity`. Either string is fine, the numeric code is what
matters.

**A `400` is not retryable; a `409` usually is** (after a delay, or after
re-reading). Splitting these correctly is what makes client retry policies
work.

### 401 vs 403 vs 404

- `401 Unauthorized` — misnamed; it means **unauthenticated**. Send
  `WWW-Authenticate: Bearer` (RFC 6750) or the `Basic`/`Bearer` challenge
  appropriate to your scheme so the client knows what to do.
- `403 Forbidden` — authenticated, not allowed. Re-authenticating will never
  help, so **do not send a `WWW-Authenticate` header**, and do not loop the
  client into a re-login.
- `404 Not Found` — the usual answer to "exists but is not yours". Returning
  `403` here is an existence oracle.

### 412 vs 428

- `412 Precondition Failed` — the client sent `If-Match`/`If-None-Match` (or
  `If-Unmodified-Since`) and the condition was false.
- `428 Precondition Required` — the server requires a conditional header and
  the client did not send one. Use it when you refuse blind writes to a
  contended resource. It is underused, and it is the correct status for "you
  must send `If-Match` here".
- `409` is a fine alternative to `412` if you cannot distinguish the cases;
  just be consistent and say so in the docs.

### 409 sub-cases worth distinguishing by `type`

- Uniqueness violation → `409` with `type: .../problems/duplicate`.
- State-machine violation → `409` with `type: .../problems/invalid-state`,
  and name the current and requested states in `detail`.
- Version/precondition failure → `412` (or `409`, see above).

## 429 and 503

Both are retryable and **both should carry `Retry-After`**, in seconds (an
integer) or as an HTTP-date.

- `429 Too Many Requests` — the caller exceeded a quota. Attach
  `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (or
  `RateLimit-*` as standardised in RFC 9333) so a well-behaved client can
  self-pace without a `Retry-After` round trip.
- `503 Service Unavailable` — *you* cannot serve it: maintenance, a saturated
  pool, an upstream dependency being down. Distinguish from `500`: `500` is a
  bug, `503` is a condition. Clients and load balancers back off on `503` far
  more aggressively, which is what you want.

Never use `500` for a dependency timeout. Use `503` and say which dependency
failed in the `detail` (internally; externally, say "temporarily unable to
complete the request").

## 5xx

- `500` — unhandled exception. Body carries the `trace_id` and nothing else.
- `501 Not Implemented` — the method or feature genuinely does not exist.
- `502 Bad Gateway` / `504 Gateway Timeout` — you are a proxy, or you are
  surfacing an upstream failure. If your service is not a proxy, prefer `503`.
- `507 Insufficient Storage` — genuinely useful for a file-upload endpoint that
  ran out of quota. Rare, but correct.

## Status codes that are wrong more often than right

| Tempting | Why it is wrong | Use instead |
|---|---|---|
| `200` + `{"error": ...}` | Breaks every retry policy, dashboard, and cache | The real 4xx/5xx |
| `200` + `{"success": false}` | Same | The real 4xx/5xx |
| `301`/`302` on a `POST` | The client may turn it into a `GET` and lose the body | `201` with `Location` |
| `304` on a `POST` | No meaning | Not applicable |
| `401` for "you are banned" or "your token expired" | Client will re-auth and get banned again | `403` for ban, `401` with a new challenge for expiry |
| `418` for business errors | Cute, and it breaks client error-mapping code | `409` with a typed `type` |
| `400` for a database unique-constraint failure | Client cannot tell a retry from a correction | `409` with `type: .../duplicate` |
| `500` for "we're down for maintenance" | Clients do not back off on `500` | `503` + `Retry-After` |
| `204` with a JSON body | Many clients crash parsing it | `200` with the body |

---
name: pagination-and-filtering
description: Implement list endpoints that stay correct under concurrent writes - keyset/cursor pagination instead of OFFSET, a worked opaque-cursor encoding, stable sort keys with a unique tiebreaker, filter and search parameter conventions, and page responses that never duplicate or skip rows. Use when the user says "paginate", "pagination", "infinite scroll", "OFFSET is slow", "duplicate rows across pages", "cursor", "filter and sort API", "add a search endpoint", or is writing a `GET /things?limit=&offset=` contract.
compatibility: Language-agnostic; the worked example is Python 3.11+ stdlib. SQL examples target PostgreSQL.
metadata:
  version: "1.0"
---

# Pagination and Filtering

The happy path is one line of SQL. Everything that goes wrong — duplicated
rows, skipped rows, a `COUNT` that times out, a cursor that 400s after a
deploy — is in here.

## Default: keyset pagination with an opaque cursor

`OFFSET` is the wrong default. Not because it is slow (it is O(offset) and
that matters) but because **it is incorrect under concurrent writes** and no
amount of indexing fixes that.

Say page 1 is rows 1..20 sorted by `created_at DESC`. A new row is inserted
before the client fetches page 2. With `OFFSET 20`, row 15 is now at position
16 and is returned **twice**. Delete a row and one is **skipped** silently. The
client ends up with a duplicated record and no error anywhere. This is the
"pagination is broken" bug every team hits once.

Keyset pagination does not remember a position; it remembers the *last value
seen* and asks for everything strictly after it. Inserts above the cursor
cannot move it, so nothing is skipped and nothing is duplicated.

## Step 1 — Make the sort key unique and total

A keyset cursor is only correct if the sort key is **unique**. Sorting by
`created_at DESC` alone is broken: timestamps collide, and every colliding row
is duplicated or skipped at the boundary. The rule:

> **Every list endpoint sorts by a set of columns whose combination is unique,
> and the sort key is part of the response.**

The usual fix is to append the primary key as the final sort term:

```sql
SELECT id, created_at, status
FROM orders
WHERE (created_at, id) < (:last_created_at, :last_id)   -- row-value compare
ORDER BY created_at DESC, id DESC
LIMIT :page_size;
```

PostgreSQL's row-value comparison `(a, b) < (x, y)` compares lexicographically
and uses a multicolumn index — it is the clean form. The portable form, which
you should know because most other databases need it:

```sql
WHERE created_at < :last_created_at
   OR (created_at = :last_created_at AND id < :last_id)
```

That `OR` form is what people get wrong by dropping the equality branch. Then
every page boundary drops or repeats every row that shares a timestamp.

Never sort by a nullable column without a deterministic rule. `ORDER BY
deleted_at DESC` puts all `NULL`s in one place (Postgres: `NULLS LAST` for
`DESC`) and they all collapse to the same key value, which is fatal for
keyset. Add `id` as the tiebreaker and it works.

## Step 2 — The cursor: opaque, self-describing, tamper-evident

`?after=eyJjcmVhdGVkX2F0Ijoi...` — base64url of a small JSON payload, plus an
HMAC so clients cannot hand-edit it. Opaque means the client stores and
replays it; it is not a documented query language. That freedom is what lets
you change the encoding without a version bump.

Three things the payload must carry:

1. The **sort key values** of the last row returned.
2. The **sort direction/columns**, so a client cannot replay a `DESC` cursor
   against an `ASC` list.
3. A **fingerprint of the filter set** (and the sort spec), so a cursor from
   `?status=open` is rejected on `?status=closed` instead of silently paging
   through the wrong rows.

### Worked example (Python 3.11+, stdlib)

```python
import base64, hashlib, hmac, json, time

_CURSOR_KEY = b"..."  # from your secret store; see skills/security/secrets-management


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def encode_cursor(*, sort: list[tuple[str, str]], keys: dict, filters: dict) -> str:
    """sort: [("created_at","desc"),("id","desc")]; keys: last row's sort values."""
    payload = {
        "v": 1,
        "s": sort,
        "k": keys,
        "f": _fingerprint(filters),
        "e": int(time.time()) + 86400,   # hard expiry: 24h
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(_CURSOR_KEY, body, hashlib.sha256).digest()[:16]
    return f"{_b64e(body)}.{_b64e(sig)}"


def decode_cursor(cursor: str, *, sort: list[tuple[str, str]], filters: dict) -> dict:
    try:
        body_b64, sig_b64 = cursor.split(".")
        body, sig = _b64d(body_b64), _b64d(sig_b64)
    except (ValueError, base64.binascii.Error) as exc:
        raise CursorError("malformed cursor") from exc

    expected = hmac.new(_CURSOR_KEY, body, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(sig, expected):
        raise CursorError("cursor signature mismatch")

    payload = json.loads(body)
    if payload.get("v") != 1:
        raise CursorError("unsupported cursor version")
    if payload.get("s") != sort:
        raise CursorError("cursor was issued for a different sort order")
    if payload.get("f") != _fingerprint(filters):
        raise CursorError("cursor was issued for a different filter set")
    if payload["e"] < time.time():
        raise CursorError("cursor expired")
    return payload["k"]


def _fingerprint(filters: dict) -> str:
    return hashlib.blake2s(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode(),
        digest_size=8,
    ).hexdigest()
```

Verify the round trip before shipping anything:

```python
>>> c = encode_cursor(sort=[("created_at","desc"),("id","desc")],
...                   keys={"created_at":"2026-03-03T10:15:00Z","id":"01J8Z"},
...                   filters={"status":"open"})
>>> decode_cursor(c, sort=[("created_at","desc"),("id","desc")], filters={"status":"open"})
{'created_at': '2026-03-03T10:15:00Z', 'id': '01J8Z'}
>>> decode_cursor(c, sort=[("created_at","asc"),("id","asc")], filters={"status":"open"})
CursorError: cursor was issued for a different sort order
```

Notes on the design:

- **Sizes matter.** Keyset cursors are short; a cursor that embeds a 20-field
  filter object gets long and some proxies reject long URLs. Fingerprinting
  the filter instead of embedding it keeps it small.
- **`hmac.compare_digest`, not `==`.** Constant-time comparison on anything
  derived from a secret.
- **Expiring cursors** bounds the blast radius when your sort keys change
  type. A client resuming a 3-week-old export gets a clean `400` and starts
  over rather than a confusing empty page.
- **Rotate the key** → in-flight cursors become invalid. Version the key (`k1`,
  `k2`) and try both during a rotation window if cursors must survive it.
- The HMAC is **not** an authorisation control. It stops a client from editing
  its own cursor; it does not stop it from asking for a page it is not entitled
  to. The authorisation check happens on the resulting query.

### Translating a cursor to a WHERE clause

```python
def after_clause(sort: list[tuple[str, str]], keys: dict) -> tuple[str, list]:
    """Build the row-value predicate for the next page."""
    cols = [c for c, _ in sort]
    dirs = {"asc": ">", "desc": "<"}[sort[-1][1]]
    row = "({})".format(", ".join(cols))
    params = [keys[c] for c in cols]
    return f"{row} {dirs} ({', '.join('?' * len(cols))})", params
```

Every column in the cursor must be one the database can compare, and the
values must be encoded in a form the driver round-trips exactly. A cursor
holding `"2026-03-03T10:15:00Z"` while the column is `timestamptz` is fine
through a parameterised query and is a silent off-by-one-second bug if you
string-concatenate the value into the SQL. **Always parameterise.**

## Step 3 — `has_more` without a COUNT

`SELECT COUNT(*)` over a filtered large table is the most expensive thing in
your API and it is why offset pagination gets blamed. Keyset pagination does
not need a total at all: fetch `limit + 1` rows, return `limit`, and set
`has_more` from whether the extra row existed.

```python
rows = fetch(limit=page_size + 1)          # one extra row
has_more = len(rows) > page_size
page = rows[:page_size]
```

If a client genuinely needs a total, give it a separate, cached, eventually
consistent endpoint (`GET /v1/orders/counts?status=open`) or return
`"total": null` with a note. Do not make the list endpoint do it.

## Step 4 — The page response

Envelope, always the same shape, whether there is a next page or not:

```json
{
  "data": [
    { "id": "01J8Z...", "status": "open", "created_at": "2026-03-03T10:15:00Z" }
  ],
  "page": {
    "has_more": true,
    "next_cursor": "eyJ2IjoxLCJzIjpbWyJjcmVhdGVkX2F0IiwiZGVzYyJdLCJrIjp7fQ.8Jm1k2...",
    "limit": 50
  }
}
```

- `next_cursor: null` **when `has_more` is false**. A client that follows a
  non-null cursor gets an infinite loop.
- Include `limit` so a client that lost its own settings still knows the page
  size.
- `total` is omitted or `null` unless you actually computed one.
- If you support jumping to an arbitrary page (an admin table needs it), that
  is a *different* parameter: `?page=57` with `OFFSET`. Keyset pages have no
  numbers. Do not pretend a keyset cursor can do both.

## Step 5 — Filtering and sorting conventions

**Filter parameter naming.** One default style, applied everywhere:

```
GET /v1/orders?status=open&status=pending      # OR within a field, AND across fields
GET /v1/orders?created_at[gte]=2026-01-01T00:00:00Z
GET /v1/orders?amount[min]=1000&amount[max]=5000
```

Bracket-suffix operators (`gte`, `lte`, `gt`, `lt`, `ne`, `in`, `contains`)
beat prefixes (`created_after=`) because they compose: `field[op]=value` scales
to every operator without a new parameter name. Keep `created_after=` as an
alias only if you have existing clients.

- **Repeated parameter = OR within that field** (`status=open&status=pending`).
  AND across different fields. State this in the docs; it is the part clients
  get wrong.
- **Allowlist every field and operator.** A parameter name that becomes a
  column name is how you get an unindexed `WHERE lower(email) = $1` on a
  200M-row table, or a `sort` parameter that is an injection point. Reject
  unknown parameters with `400` and a problem detail naming the parameter.
- **Bound everything**: max `limit` (50 is a fine default; 100 max), max
  filter clauses, max `IN` list size, minimum page size.
- **Every filterable field needs an index** that leads with the equality
  columns and then the sort key. `status = ? ORDER BY created_at DESC, id
  DESC` wants `(status, created_at DESC, id DESC)`. A filter that cannot be
  indexed should be rejected at design time, not discovered in production.

**Sorting.**

```
GET /v1/orders?sort=-created_at,name      # leading '-' = descending
```

- Default sort: put it in the docs and make it the *stable, indexed* order.
  Defaulting to `created_at DESC` with no `id` tiebreaker produces a
  non-deterministic order across pages.
- Allowlist sortable fields. Every allowed sort must be indexable.
- Multi-column sort is fine, but **always append the primary key** if the
  client did not include it.

**Search.** `?q=` is full-text, not `ILIKE '%q%'`, which cannot use a btree
index. Postgres: `tsvector` + GIN. If you are proxying an external search
service, keep the endpoint shape (`GET /v1/orders?q=&cursor=`) so you can
change engines later, and return the cursor your engine gave you rather than
re-deriving one.

## Edge cases

- **Rows deleted between pages.** A keyset cursor still returns a consistent
  *forward* page — you never see a row twice. Rows deleted after page 1 simply
  never appear. That is correct and expected; document it.
- **Sort key mutated mid-iteration.** If a user renames themselves and
  `display_name` is the sort key, rows move. Keyset pagination has no defence;
  this is a reason to paginate over an immutable column (`created_at`, `id`)
  and sort/filter by the mutable one only as a secondary.
- **Client changes filters mid-pagination.** The fingerprint rejects the
  cursor with `400` and a `detail` saying the filter set changed. This is
  correct behaviour, not a bug to work around.
- **Permission changes.** A cursor is not a grant. Re-run authorisation on
  every page. Never cache a page listing across a user.
- **Very deep pagination for exports.** Long-running exports should not hold a
  cursor chain in an HTTP request. Snapshot the id set (or a `WHERE` clause
  with a `created_at <= export_started_at` bound) and page over the snapshot.
  Without a bound, new rows inserted mid-export can appear in later pages of
  the same export.
- **Reverse pagination** ("load older", jumping back to a saved list). Keyset
  works in both directions if you store the previous page's first key as well
  as the next page's last key. Store both; it costs nothing.

## Gotchas

- **`OFFSET` is O(offset) *and* wrong.** The index makes it faster; it does
  not make it consistent. Presenting `OFFSET` as a performance issue misses
  the data-corruption issue, which is worse.
- **Sorting by a non-unique key is the number-one source of duplicated rows**
  in keyset pagination. Non-negotiable: unique tiebreaker, in the cursor.
- **A cursor without a filter fingerprint is a data-leak vector** — a client
  can page through a result set built with a different filter (or a filter it
  was not authorised to add). Sign the fingerprint.
- **Returning `next_cursor` on the last page** gives clients an infinite
  loop. Tie `next_cursor` to `has_more`.
- **`limit=1000` "just this once"** is how a `limit` parameter becomes a
  denial-of-service lever. Hard-cap it server-side.
- **Postgres `LIMIT` does not guarantee order without `ORDER BY`.** If you
  forgot the `ORDER BY`, the keyset logic is meaningless.
- **`IN` lists and unbounded `BETWEEN` ranges defeat indexes** on some query
  shapes and always defeat prepared-statement plan caching. Cap the list size.
- **Timestamps: store UTC, filter in UTC, render with an offset.** A cursor
  carrying a local-time string will be compared against a `timestamptz` and
  silently shift pages. Store the cursor's key values in the same representation
  the driver uses for the parameter.
- **Cursor in a cookie or a header instead of the query string** if URLs are
  getting long or the filters are sensitive. Same encoding, same rules.
- **Do not put the cursor's sort key values into logs at `info` level** —
  combined with an unfiltered endpoint they reveal other users' filter state.

## Gotcha: reverse proxy and gateway limits

Some gateways and CDNs cache on the full query string, so a per-cursor URL is
a cache miss every time and will never be served from a shared cache. If a
list endpoint is hot enough to want CDN caching, it wants a coarser
parameterisation (windowed, time-bucketed pages) rather than a cursor per
request. Decide which problem you have before adding a cache in front of a
cursor endpoint.

## Output template

When designing a list endpoint, emit:

```markdown
### GET /v1/<resource>

Sort:  default <field> <dir>, tiebreaker <pk>
Filter params: <field>[gte|lte|eq|in], repeated = OR, AND across fields
Allowed filters: (allowlist, with the index that supports each)
Allowed sorts:   (allowlist, with the index that supports each)
limit:  default 50, max 100

Response: { data: [...], page: { has_more, next_cursor, limit } }
Errors:   400 unknown/invalid filter or sort, 400 invalid or mismatched cursor
Guarantees: forward-only, no duplicates, no total count; rows deleted after
            page N are not shown; new rows inserted after page N are not shown
```

## Safety notes

- Changing a sort default or a filter's semantics on a live endpoint changes
  results for existing clients. Treat it as a breaking change and version it.
- Never add a filter parameter without an index for it, and never widen
  `limit`'s maximum without re-checking the query plan.
- Do not expose a `COUNT(*)`-based `total` on a hot filtered endpoint to
  "fix" client UIs. Give it a separate cached endpoint instead.

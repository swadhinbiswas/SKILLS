---
name: graphql-api-design
description: Design and debug a GraphQL schema that behaves under load - DataLoader for N+1, depth and complexity limits, Relay connections for pagination, typed errors with partial data instead of null-ing the response, deliberate nullability, and federation. Use when the user says "GraphQL", "resolver is slow", "N+1 in GraphQL", "query is too complex", "connection/edges/cursor", "partial data", "federation", "should we use GraphQL", or is writing a .graphql schema or resolver.
compatibility: Assumes a spec-compliant server (Apollo Server, GraphQL Yoga, graphql-js, Strawberry, Ariadne). Library names are examples; verify flags with --help or the docs.
metadata:
  version: "1.0"
---

# GraphQL API Design

GraphQL removes over- and under-fetching and hands you three new problems:
resolvers that fan out into N+1, clients that can ask for arbitrarily expensive
queries, and partial failures that must not become `null` storms. Most bad
GraphQL services are bad at exactly those three, not bad at the schema.

## First: should you use GraphQL at all?

Reach for it when **the set of screens is unknown or changing, several distinct
clients need different slices of the same data, and the join graph is
genuinely irregular**. Reach for REST when there is one mobile client, the
resource model is stable, or you mostly need file uploads, long-running
mutations, or a public documented API for third parties.

Do not use it because "it's more efficient". The network bytes are not the
problem; the N+1s you will write in resolvers are.

Signals you picked wrong, and should reconsider:

- You needed `@defer`/`@stream` everywhere to make responses tolerable.
- Client teams cache query strings against an unstable schema and break on
  every deploy.
- You have a "GraphQL" layer that is just a thin REST proxy with a resolver
  per endpoint — you now have two APIs and one of them is worse.
- The schema is a mirror of your database tables, and every change to the
  database is a breaking schema change.

If you have one or two consumers and a stable domain, REST is the cheaper
default. Say so plainly rather than designing a GraphQL API that fights the
problem.

## Step 1 — Schema design

**Model the domain, not the tables.** `type User { posts: [Post!]! }` is fine.
`type User { users_1: [User!]! }` is a table. If a type is a row you can write
`{ user { id name } }` and get back, you have a database dump with a resolver
per column.

**Inputs are not outputs.** Separate `type CreateOrderInput` from
`type Order`. The moment they share a type, adding a field to the output forces
you to decide what it means as an input.

**Enums over strings, custom scalars over loose strings.** `Status` with
`OPEN | CLOSED` fails at validation time instead of at a database constraint.
Custom scalars (`DateTime`, `URL`, `EmailAddress`, `JSON`) document and
validate intent.

**One mutation per business operation, not one per field.** A GraphQL mutation
takes exactly one top-level field; a mutation that updates five fields is one
field with one input object, and it belongs in a single transaction.

**Arguments should have defaults, not be required, when there is a sensible
one** — required arguments force every client to specify them.

## Step 2 — Nullability, decided deliberately

The single highest-leverage decision in a GraphQL schema, and almost always
answered by accident. The rules:

- **If a field's resolver cannot fail, make it non-null** (`String!`). Its type
  is a promise to clients.
- **If it can fail, make it nullable** (`String`). Then a failure nulls one
  field, not the parent object.
- **A non-null field that errors propagates the null upward.** If `User.email`
  is `String!` and the email service is down, you get
  `user: null` — or if `user` is also non-null, `data: null`, and the client
  loses the whole response. That is how one slow dependency becomes a total
  outage for a screen.
- **Lists: decide element vs list nullability separately.** `[Post!]!` means
  "always a list, never a null element". `[Post]` means a failed element becomes
  `null` in the array, which clients handle badly. Prefer `[Post!]!` for lists
  that are always fully readable, and a connection with `nullable` edges if
  items can be individually denied.

Start with a nullable schema and tighten. Tightening (nullable → non-null) is
a breaking change; loosening is safe.

## Step 3 — Connections (pagination)

Do not return `[Post!]!` for a list. A list field has no way to say "here is
the next page" and a client that needs one has to be refetched from scratch.
Use Relay Connections, which are in the spec and supported by every client
library.

```graphql
type Query {
  posts(first: Int, after: String, filter: PostFilter, orderBy: PostOrder): PostConnection!
}

type PostConnection {
  edges: [PostEdge!]!
  pageInfo: PageInfo!
  totalCount: Int          # nullable: often genuinely expensive
}

type PostEdge {
  cursor: String!          # base64("arrayconnection:<created_at>:<id>")
  node: Post!
}

type PageInfo {
  hasNextPage: Boolean!
  hasPreviousPage: Boolean!
  startCursor: String
  endCursor: String
}
```

Rules:

- Argument order is `first`/`last` + `after`/`before`. `first: 100` is a
  deliberate denial-of-service lever; cap it server-side (250 is plenty) and
  say what the cap does (`errors` with an extensions code, or clamp — pick
  one and document it).
- **Cursors are opaque** and must sort by a unique key. `created_at, id` — see
  `skills/api-design/pagination-and-filtering/SKILL.md` for the encoding
  mechanics and why a non-unique sort key duplicates rows.
- `totalCount` is the single most common cause of slow GraphQL. Make it
  nullable, or move it to a separate field the client asks for explicitly.
- Connections on a **nested** field (a post's comments) inherit every
  complexity cost. That is fine, and it is also an attack surface: see limits.

## Step 4 — N+1 and DataLoader

**This is the defect that makes GraphQL slow.** A resolver is a function; if it
calls a database or HTTP API per parent object, a list of 100 posts runs 100
queries.

```graphql
query { posts(first: 50) { edges { node { author { name } } } } }
```

If `Post.author` resolves with `db.author.find(post.authorId)`, that is 50
queries. Measure it, do not guess: log a counter around your data source, or
use the `DataLoader` dispatch instrumentation. The `__typename`-only request
that forces the whole query into the resolver tree without a round trip is
also a useful trick:

```graphql
{ __typename }  # sent to /graphql forces validation, not execution
```

### The fix: batch and cache per request

`DataLoader` (or your library's built-in loader) does two things: batches
requests that occur in the same tick into one call, and caches the result for
the lifetime of the request so `Post.author` called twice for the same author
is one lookup.

```js
import DataLoader from "dataloader";

const authors = new DataLoader(async (ids) => {
  // ONE query for all ids, always in the array order it was given
  const rows = await db.query(
    "SELECT id, name FROM authors WHERE id = ANY($1)", [ids]
  );
  const byId = new Map(rows.map(r => [r.id, r]));
  // Must return an array aligned with `ids`; a missing row is an Error,
  // not a hole. Returning a short array silently misaligns every result.
  return ids.map(id => byId.get(id) ?? new Error(`author ${id} not found`));
});

// One loader per request, not per module. A module-level loader caches
// across requests and serves stale data forever.
export function createLoaders(db) {
  return { authors: DataLoader: undefined, loaders: { authors } };
}
```

The rules that make DataLoader actually work:

- **Scope loaders to the request.** The loader cache is a correctness
  boundary, not a performance cache. A shared loader is a cross-tenant data
  leak and a permanently stale cache. Build it in a per-request context
  (Apollo: `context`; Yoga: `createContext`; graphql-js: the function
  signature's `contextValue` argument).
- **Return a `Promise` or a list of promises, not a single result.** A loader
  that returns one value for N keys is a bug that only shows up under load.
- **Batching happens per tick.** A loader called sequentially with `await`
  between calls does not batch, because each tick only sees one call. If you
  genuinely need sequential access, that is a sign you need a join, not a
  loader.
- **Use the same loader for every path that reads the same entity.** Two
  loaders for `authors` means two queries and two caches.
- **Only the top-level field's parent is batchable.** Two sibling fields on the
  same type each get their own batch. This is fine.
- Loads *inside* a mutation are a different world: a loader cached before the
  mutation still holds the pre-mutation value. Clear or version the loader
  cache after each mutation resolver (`loaders.clearAll()`), or you will read
  stale data for the rest of the request.

## Step 5 — Query cost limits (the other half)

DataLoader fixes accidental fan-out. It does nothing about a client that
legitimately asks for a 12-level nested query across a million rows. Every
server needs all three of:

1. **Depth limit** — reject `query too deep`. 7 is a reasonable default.
   Also limit recursion (a schema that can self-reference, e.g.
   `Post.relatedPosts`, needs an explicit depth cap or a query is infinite).
2. **Complexity/cost analysis** — score the query before executing it. Assign
   a cost to each field and multiply list fields by their `first`/`last`
   argument; sum and compare to a budget. Also count **list fields
   themselves**, not just leaves: a request that asks for 100 fields each
   returning 100 items is the attack, and leaf-only counting misses it.
3. **Timeout and cost-based rejection** — return an error before spending
   CPU, not after.

Also: **disable introspection in production** unless you are selling a public
API (and even then, weigh it), disable arbitrary batching/aliasing if you do
not need it, and set a per-operation timeout that is shorter than the client's.

Limits do not have to be user-supplied numbers. Some servers ship cost
analysis as a library rather than a flag — check the docs for the specific
server rather than guessing at a `--max-depth` style option.

## Step 6 — Errors and partial data

The single most-missed rule: **a resolver error nulls that field, not the
response, and the `errors` array says why.** Returning `null` from a resolver
without an error entry is indistinguishable from "this field is null" and it is
undebuggable.

```json
{
  "data": { "posts": { "edges": [ { "node": { "id": "1", "author": null } } ] } },
  "errors": [
    { "message": "Could not load author for post 1.",
      "path": ["posts", "edges", 0, "node", "author"],
      "extensions": { "code": "UPSTREAM_UNAVAILABLE", "requestId": "abc123" } }
  ]
}
```

- **`extensions.code` is the contract.** Clients branch on it, never on
  `message`. Use stable, screaming-snake codes: `BAD_USER_INPUT`,
  `UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`,
  `UPSTREAM_UNAVAILABLE`, `INTERNAL_SERVER_ERROR`.
- **Never put internal detail in `message`.** No stack traces, no SQL, no
  upstream URLs. Log the real cause with the `requestId`; return the
  `requestId`.
- **Authorisation errors go in `errors`, not as a `null` you explain in a
  README.** A resolver that returns `null` for "not allowed" tells the client
  nothing.
- **Non-null propagation is a design decision.** If `author` is `String!` and
  the author lookup fails, `node` goes null, and if `node` is `Post!`,
  the whole `edges` array goes null. If that is not what you want, make
  `author` nullable — see Step 2.
- **Never catch an error and return a wrong-shaped value.** Swallowing an
  exception to return `[]` or `0` converts a visible failure into a silently
  wrong result. Let it propagate to the `errors` array.
- **Input errors** (`BAD_USER_INPUT`) go in `errors` with `extensions.field`
  pointing at the offending argument, and the mutation's side effects are
  rolled back. GraphQL mutations are not transactional by default: a resolver
  that writes three things and fails on the second leaves partial state unless
  you wrap the mutation in a transaction or use a serialised mutation root.

## Step 7 — Federation, briefly

Apollo Federation splits a graph across independently deployed subgraphs: each
subgraph owns its types and the gateway stitches them.

- **Only when the org or the release cadence actually demands it.** Federation
  adds a gateway (a chokepoint, a routing layer, a deployment), subgraph
  version skew, and cross-subgraph failure modes, in exchange for independent
  deployment. Two teams on different release cycles is the honest trigger. A
  monolith split "because it is best practice" is not.
- The costs are real: a supergraph must be recomputed and published when any
  subgraph changes; `_entities`/`_service` are internal; cross-subgraph N+1
  is the resolver N+1 multiplied by the number of hops.
- Federation is **not** a security boundary. Every subgraph must still
  authenticate and authorise; a gateway that checks permissions once and trusts
  subgraphs is a single point of bypass.
- Start with one schema. Federation has a high adoption cost and a high exit
  cost.

## Gotchas

- **`first: 100000` is a valid query.** Without cost analysis it is a free
  denial-of-service. Limits are not optional.
- **Module-level DataLoader is the classic GraphQL bug**: it caches across
  requests, so you get stale data forever and, in a multi-tenant server, one
  tenant's row served to another. Build loaders per request, always.
- **A loader that returns a short array misaligns results silently.** If key
  2 is missing and you return `[a, c]`, key 3 gets `c`'s data. Return one entry
  per input key, using `new Error(...)` for misses.
- **Resolver order is post-order traversal**, so sibling fields at the same
  level batch, but two fields on *different* levels do not. Writing
  `await` between two loader calls in the same tick breaks the batch.
- **Aliases multiply cost.** `{ a: user(id:1){...} b: user(id:2){...} }` is two
  full resolutions under one field count. Cost analysers that ignore aliases
  can be bypassed trivially.
- **Fragments do not limit work.** A named fragment referenced 10 times
  executes 10 times. A query-size byte limit is a blunt but real backstop
  against fragment-soup amplification.
- **`totalCount` on a connection over a large table** is the most common
  accidental DoS in a GraphQL service. Make it nullable and cost it.
- **Introspection is a schema map for attackers.** Disable it on
  non-public APIs; it makes credential attacks and field-by-field probing far
  cheaper.
- **HTTP caching gets weird.** GraphQL POSTs are not cacheable by default; if
  you expose it over GET, allow only *persisted, allowlisted* query strings,
  or you have built a query-execution endpoint anyone can hit anonymously.
- **WebSocket subscriptions** need their own auth at connection *and* at
  subscribe time, plus per-connection limits. A leaked long-lived subscription
  with no idle timeout is a slow leak.

## Output template

When asked to design a GraphQL API, emit:

```markdown
## Schema
(types, inputs, enums, custom scalars; what is non-null and why)

## Connections
(list fields, argument caps, cursor sort key, whether totalCount exists)

## Data access
(loader per entity, batched query per loader, per-request scoping)

## Limits
(depth, complexity budget with the cost model, per-op timeout, introspection)

## Errors
(codes, nullability consequences, what goes in extensions vs the message)

## Rejected reasons
(if GraphQL is the wrong answer, say so and propose REST)
```

## Safety notes

- Do not raise a complexity limit, a depth limit, or `first`'s cap in
  response to a single client's request without checking the cost model and
  the database query plan underneath it.
- When adding a non-null constraint to an existing field, treat it as
  breaking: run it against production-shaped data first.
- Never ship a resolver that logs a raw error object into a `message` field.

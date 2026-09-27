# RSC boundary reference

Load this when you are looking at a specific serialisation or "use client"
error and need to know exactly what can cross the server→client boundary, or
when deciding where to put a `"use client"` directive.

## What can cross the server→client boundary

React Server Components serialise props and children into a payload rendered
for the browser. The supported value types (React 18/19 RSC payload):

| Type | Crosses? | Notes |
|---|---|---|
| string, number, boolean, `null`, `undefined` | Yes | `undefined` is dropped from JSON; prefer `null` for optional fields |
| Plain object / array of the above | Yes | Must not have a class prototype or `null` prototype |
| `Date` | Yes, as a `Date` | The RSC payload encodes a Date; but if you round-trip through a JSON-only API it becomes an ISO string. Be explicit. |
| `Map`, `Set`, `BigInt`, typed arrays | Yes | React encodes them; verify in your framework's transport |
| Promise | Yes (streamed) | Passed to a client component, React streams the resolved value; you can `await` it client-side or `use()` it |
| React element / JSX (incl. `children`) | Yes | Elements are serialised; the referenced server component stays on the server |
| A function marked `"use server"` | Yes | Becomes a server-action reference; calling it is an RPC |
| Any other function | **No** | `Functions cannot be passed directly to Client Components unless you explicitly expose it by marking it with "use server"` |
| Class instance (ORM model, custom class) | **No** | `Only plain objects ... Classes or null prototypes are not supported` — the *behaviour* (methods) cannot cross; convert to a plain object |
| A React context object (server) | **No** | A server context is not available to client components; client and server contexts are separate |
| `process.env.SECRET`, a DB client, a file handle | **No** (and unsafe) | Only `NEXT_PUBLIC_*` / `EXPO_PUBLIC_*` style public vars are even inlined client-side; never send a secret across |

## Symptom → cause → fix

| Error / symptom | Cause | Fix |
|---|---|---|
| `Functions cannot be passed directly to Client Components ... "use server"` | A plain fn / event handler passed as a prop from a Server Component | Make the component receiving it a Server Component, or mark the fn `"use server"` (an action), or move interactivity into a small client leaf |
| `Event handlers cannot be passed to Client Component props` | An `on*` prop set in a Server Component | Server Components can't create handlers; render a client child and attach there |
| `Only plain objects ... Classes or null prototypes are not supported` | Passing a class instance (Prisma model, `Object.create(null)`) | Map to a POJO at the boundary: `const dto = { id: r.id, name: r.name }` |
| `Attempted to call useState()/useContext() from the server ... on the client` | A hook imported into a module without `"use client"` | Either the module is genuinely client → add `"use client"` at the top; or the hook uses a client context a server parent can't provide → move the hook into a client child and pass the server data down as a prop |
| `useSearchParams() should be wrapped in a suspense boundary` | `useSearchParams` in a statically rendered route without `<Suspense>` | Wrap the consuming client component in `<Suspense fallback={...}>` |
| A server component's data changes but the page doesn't | The fetch was cached (Next dedupes/caches by default in some versions) | `fetch(url, { cache: 'no-store' })` for per-request, or `{ next: { revalidate: n } }` for ISR |
| The client bundle is unexpectedly huge | A page/layout is `"use client"`, or a client provider wraps the tree, or a barrel re-export pulled a server tree client-side | Push `"use client"` to leaves; split the provider; import from the file, not the barrel |
| `process.env.MY_SECRET is undefined` in the browser, or a secret leaks | Read a non-public env var in a client module | Public-prefixed vars are the only ones inlined; keep all others server-only |
| Server action runs but auth check is missing / bypassed | Action trusts the client-supplied user id or role | Re-derive identity from the session inside the action; never accept `userId` from the client as authority |

## Where to put `"use client"`

- **As low in the tree as possible.** The button, the input, the modal shell —
  not the page, not the layout.
- **At the top of the file**, before any import or export. It is a directive,
  not a statement, and it must be the first thing (comments/strings before it
  are tolerated, but don't rely on it).
- **On the boundary you need**, and it applies to the whole import graph below
  it in that module. Splitting a module so the server part and the client part
  are separate files is usually the fix for "my server file became client".
- **Not as a serialisation escape hatch.** If a prop won't serialise, convert
  the prop; don't mark the parent client.

## Server actions: the security checklist

A server action is reachable by anyone who can reach the app, whether or not
your UI calls it. Treat each one as a public POST endpoint:

- [ ] Re-read the session/identity from the request inside the action; never
      trust a user id, role, or ownership passed as an argument.
- [ ] Validate and coerce every argument (a `zod` schema, or the framework's
      `parse`), because arguments are client-controlled.
- [ ] Do the authorisation check (does this user own this row?) before the
      write, server-side.
- [ ] Return plain data; do not return a DB row or an error object containing
      internals.
- [ ] Keep secrets server-side. A secret in a value the client can read or send
      back is not a secret.
- [ ] Rate-limit or authenticate actions that do anything expensive or
      destructive.
- [ ] Remember actions are POSTs that mutate; don't expose destructive actions
      via GET-rendered links.

## Streaming and Suspense in the RSC model

- Each `<Suspense>` boundary in a server tree is an independent stream. The
  shell (everything above the first boundary) is sent first, so put a boundary
  around the slowest, not the fastest, content.
- Fetch independent resources in parallel: `const [a, b] = await Promise.all([...])`.
  A sequential `await` per component is a waterfall.
- A promise created in a server component and passed to a client component is
  streamed; the client can `await` it, or React 19's `use(promise)` reads it
  in a client component. This is how a server component can hand a client
  component "streaming data" without a loading prop.
- `loading.tsx` in Next is a `<Suspense>` boundary around the route; a
  `loading.tsx` that wraps the entire page gives you one spinner instead of
  incremental content — put the boundary where the slow part is.

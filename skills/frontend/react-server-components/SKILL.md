---
name: react-server-components
description: Work correctly in the React Server Components model (Next.js App Router and similar frameworks) - what "use client" and "use server" actually do, what can and cannot cross the server/client serialization boundary, where data fetching belongs, and how to avoid the errors that only appear in production builds. Use when a component gets a "Functions cannot be passed directly to Client Components" or "Only plain objects can be passed" or "useContext is not a function" error, when deciding which files need "use client", when a client hook or context breaks, or when reviewing an RSC architecture. Triggers on "server component", "RSC", "use client", "use server", "App Router", "hydration mismatch", "serialization", "server action".
compatibility: Next.js 13+ App Router; React 18.2+/19. Version caveats are called out inline - check your installed Next/React versions before relying on a specific behaviour.
metadata:
  version: "1.0"
---

# React Server Components

RSC splits one component tree across two runtimes: a **server** runtime that
owns data, secrets, and the filesystem, and a **client** runtime that owns
interactivity. A "client component" is still a normal React component; the
distinction is *where it runs and what data can cross the line*.

The model is framework-agnostic but every concrete detail below is Next.js App
Router, because that is the shipping implementation. Other frameworks
(Remix/React Router 7, Redwood, Hydrogen) implement the same primitives with
different file conventions.

## The mental model

- **Everything is a Server Component by default.** A component with no
  directive renders on the server. If you never write `"use client"`, you never
  ship component code to the browser.
- **`"use client"` is a boundary marker, not an import.** It marks a module
  (and everything it imports) as client-side. It does not "make a component
  client-side" if the component is imported by a server file — the whole import
  subtree becomes client code.
- **The boundary is serialisation.** Anything a server component passes as props
  to a client component is serialised to a payload for the browser. That
  includes the JSX itself, so the client component receives real React elements
  as props.
- **Server Components never run in the browser.** You cannot put `onClick`,
  `useState`, `window`, or an `import 'some-client-only-lib'` in a server
  component. Doing so is a build error, not a runtime surprise (for the
  forbidden imports/hooks), which is good: the build catches it.
- **A client component can render a server component passed as `children`.** This
  is the main escape hatch and the reason the model scales:

```tsx
// app/page.tsx  — Server Component
import { AddToCart } from './AddToCart';   // 'use client'
import { ProductList } from './ProductList'; // no directive: server

export default async function Page() {
  const products = await db.products.findMany();
  return (
    <Layout>                                  {/* stays server */}
      <ProductList products={products} />      {/* stays server */}
      <AddToCart productId={products[0].id} /> {/* client: needs onClick */}
    </Layout>
  );
}
```

## What `"use client"` and `"use server"` actually do

**`"use client"`**

- Marks the file and its entire static import graph as client code. It ships to
  the browser and is part of the client bundle.
- Anything it imports is client too — including a barrel file. A
  `components/index.ts` that re-exports a server component makes it a client
  component, silently.
- Hooks, event handlers, `window`, browser-only libraries, and React context
  are only allowed *below* this line. Once a module is a client module, those
  are fine.
- It does **not** disable the server-rendering of that component's initial HTML
  — a client component is still SSR'd; `"use client"` only decides where the
  code *runs* and whether the *bundle* is sent.

**`"use server"`** (Server Actions / Server Functions)

- Marks an **async function** as a server function that can be called from
  client code over the network. It runs on the server with access to env secrets
  and the database.
- It is a **network RPC with a build-time-generated endpoint**. Calling it is
  not a local function call: it serialises arguments, sends a request, and the
  return value comes back serialised. That is why its arguments and return must
  be serialisable, and why a `Date` arrives as a string unless you handle it.
- Always treat it as a **public, untrusted endpoint** even when only your UI
  calls it. It is the single most common source of a serious vulnerability in an
  RSC app (see Gotchas).

**Where the data flows**

- A Server Component can `await` a fetch directly in its body. There is no
  `useEffect` in a server component; fetching during render *on the server* is
  the point.
- Data fetched in a server component is serialised into the RSC payload and
  hydrated in the client tree automatically; you do not lift it into a query
  cache unless you also want client-side refetching (then use a query library's
  `initialData` or `HydrationBoundary`).

## The serialisation boundary — what can cross

Props passed from a server component to a client component are serialised. The
allowed set is roughly React's "plain JSON + a few extras":

| Allowed | Not allowed |
|---|---|
| Primitives, plain objects, arrays | Functions (except Server Actions / bound server functions) |
| `Date`, `Map`, `Set`, `BigInt` (React serialises these, with caveats) | Class instances with methods (methods are lost) |
| `Symbol.for('react.element')` / React elements (children, JSX props) | `undefined` in some strict positions? no — but it's dropped in JSON. Prefer `null` |
| Server Actions (functions marked `"use server"`) | Any other closure capturing server-only values |
| Promises passed from server → client (streamed, then awaited) | `process.env` values, DB clients, anything with behaviour |

The errors you will actually see, and what they mean:

```
Error: Functions cannot be passed directly to Client Components unless you
explicitly expose it by marking it with "use server".
```

A plain function, or an event handler, was passed as a prop. Either the
function should be a Server Action, or the component receiving it must be a
server component.

```
Error: Only plain objects, and a few built-ins, can be passed to Client
Components from Server Components. Classes or null prototypes are not supported.
```

A class instance (a Date-like ORM model, a `Map` from an SDK, a Prisma/Drizzle
row with methods) was passed. Convert it at the boundary to a plain object:
`{ id: row.id, name: row.name }` or `JSON.parse(JSON.stringify(row))` for a
deeply nested one.

```
Error: Attempted to call useContext() from the server but useContext is on
the client. It's not possible to invoke a client hook from the server component.
```

A hook was called in a module that did not have `"use client"`, likely because
of a `useContext` of a provider defined elsewhere, or the hook was called inside
a non-component function.

```
Error: Event handlers cannot be passed to Client Component props.
If you need interactivity, consider converting part of the props to a
Client Component.
```

A Server Component tried to pass `onClick`/any `on*` handler down.

## Data fetching colocated with components

The model is designed so each component owns its own data:

```tsx
// app/products/[id]/page.tsx  — Server Component
export default async function ProductPage({ params }: { params: { id: string } }) {
  const product = await getProduct(params.id);   // direct DB/fetch, no useEffect
  if (!product) notFound();
  return (
    <div>
      <h1>{product.name}</h1>
      <Price product={product} />          {/* server */}
      <AddToCart id={product.id} price={product.priceCents} />  {/* client */}
    </div>
  );
}
```

- Fetch **at the level that needs it**, not in the page and not via a global
  client cache, unless you genuinely need client-side refetch.
- Use `fetch(url, { next: { revalidate: 30 } })` (Next) or `cache()` /
  `unstable_cache` for dedupe and caching; without options Next will fetch on
  every request.
- Load independent things **in parallel** with `Promise.all`, not a waterfall of
  sequential `await`s.
- Stream slow data with `<Suspense fallback={...}>` so the shell paints
  immediately. Each Suspense boundary is an independent server stream.

## Gotchas

- **A client context provider makes all its children client.** If you wrap a
  page in a client `<Providers>` that holds a theme context, the *whole subtree*
  under it becomes client and any server child is passed as a `children` prop
  (fine) — but if you read a *server* context in that subtree it breaks. Keep
  the `"use client"` provider as shallow as possible and pass server content
  through `children`.
- **Client context and server context are different contexts.** A context
  created in a `"use client"` file is not readable from a server component. This
  is the `useContext is on the client` error.
- **`"use client"` is transitive through imports and barrel files.** One
  re-export in an `index.ts` can pull a whole server-only tree into the client
  bundle. Check your client bundle (Next build output) for server-only code.
- **Server Actions are public endpoints.** Anyone can craft a request. Always
  (a) re-authenticate and re-authorise *inside* the action, never trust the
  caller; (b) validate all input (the arguments arrive from the client, so
  treat them as untrusted); (c) never put a secret in a value the client can
  pass. This is server-action CSRF/auth bypass territory — do not skip authz.
- **Server actions must be `async`.** A non-async `"use server"` function is
  an error. And a `"use server"` **module** (top of file) turns *every* exported
  function into a server function — easy to leak an internal helper by accident.
- **Server actions and RSC payloads are POST-able CSRF targets.** Next sets
  an `Origin`/`Host` check for actions, but do not rely on that as your only
  defence, and do not put GET-able side effects in a server-rendered route.
- **A `Date` passed to a client component serialises to a string** in some
  paths; a `Map`/`Set` may not survive if the transport is JSON in your
  framework. Prefer ISO strings / plain objects at the boundary and rehydrate
  deliberately.
- **Don't pass a whole DB row.** It drags every column (and sometimes a
  prototype or relation) across the boundary and breaks serialisation. Select
  what the component needs.
- **`useSearchParams` / `useRouter` / `usePathname` are client hooks.** Using
  them in a server component errors; using `useSearchParams` in a
  statically-rendered route requires a `<Suspense>` boundary or Next bails out
  of static rendering.
- **The line count of your client bundle is the headline metric.** Turning one
  file into a client component can pull a whole library in. `"use client"` the
  *leaf* (the button), not the page.
- **Don't reach for `"use client"` to fix a serialisation error** by marking
  the *parent* client. That moves the whole subtree client-side and hides the
  real fix (serialise the prop, or use a Server Action). Mark the smallest
  thing that needs interactivity.
- **Middleware and Server Actions run on the server, but `"use client"` files
  that import `process.env.NEXT_PUBLIC_*` bake those values into the bundle.**
  Never read a non-`PUBLIC` env var in a client module.
- **HMR/ Fast Refresh treats adding/removing `"use client"` as a full
  remount.** Don't toggle directives in a running dev session and expect
  state to survive.

## Diagnosing RSC problems

1. **Build error mentions serialisation** → look at the exact prop crossing the
   boundary; convert it to a plain value or move the receiver to the server.
2. **"useContext is on the client"** → a hook in a server module; the module
   either needs `"use client"` (if it's genuinely client) or the hook call
   should be in a child client component (if it's using client context that a
   server parent can't provide).
3. **Bundle too big** → find the top `"use client"` file pulling in the heavy
   import; read the Next build's "First Load JS" per route.
4. **Data not refetching on navigation** → Next cached the fetch; add
   `revalidate`, or `cache: 'no-store'` for per-request data. Server component
   fetches default to caching in some versions — check yours.
5. **Hydration mismatch** → usually rendering `Date.now()`, `Math.random()`, or
   `typeof window` in a component that also SSRs. Move it into an effect or
   gate it behind a mounted flag.

## Review checklist

- [ ] Does each leaf that needs interactivity have its *own* small
      `"use client"` file, rather than a client page?
- [ ] Is the client bundle's largest contributor something avoidable (a
      barrel re-export, a date library, a big form lib in a shared layout)?
- [ ] Do all Server Actions re-authenticate and validate their arguments?
- [ ] Are props crossing the boundary plain data, not classes or ORM objects?
- [ ] Are independent fetches parallel and slow ones behind `<Suspense>`?
- [ ] Is any server-only secret referenced from a client module?

## Files

- `references/rsc-boundary.md` — allowed/forbidden values across the boundary
  and a symptom → cause table for RSC errors.

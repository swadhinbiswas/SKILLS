---
name: react-state-management
description: Decide where each piece of React state should live and stop the rework cycles it causes - server state via a query library, local UI state with useState, genuinely global client state in a small store, shareable state in the URL, and forms in a form library. Covers the anti-patterns that dominate code review: mirroring props into state, context re-render storms, and using a global store for what is really a URL param or a server field. Use when someone asks "should this be in context or zustand or redux", when a provider re-renders the whole app, when state is duplicated in two places, or when debugging "stale state" / "lost scroll position" / "back button does not work". Triggers on "lifting state up", "prop drilling", "global state", "zustand", "redux toolkit", "context provider", "URL as state", "server state", "useReducer vs useState", "too many re-renders".
compatibility: React 18+. Examples use React Query v5 and Zustand v4/v5 APIs; adapt names to your query/store library of choice.
metadata:
  version: "1.0"
---

# React State Management

State placement is a design decision, not a preference. Most state-management
migrations happen because the placement was never made deliberately, so the app
ends up with the same data in a component, a context, and a store.

## The default, in order

Pick the **topmost** rung that fits. Moving state up the list is cheap; moving it
back down is a refactor.

| # | Kind of state | Default home | Example |
|---|---|---|---|
| 1 | Server state (anything a fetch owns) | Query library cache | Users, orders, list results, feature flags |
| 2 | Shareable UI state | URL (`searchParams`, `hash`, path) | Current tab, filters, sort, open modal id, page |
| 3 | Form/draft state | Form library or local `useState` | Signup form, multi-step wizard, inline edit |
| 4 | Local component UI state | `useState` / `useReducer` in the component | Is the dropdown open, is the row expanded |
| 5 | Cross-cutting *client* state | Small store (Zustand) | Auth session, feature entitlement, theme, onboarding-seen |
| 6 | Complex, audited, cross-app state | Redux Toolkit (only with a real reason) | Large apps, time-travel debugging, complex write paths |

**The house default, stated once:** *server state via TanStack Query; local UI
state local; URL state in the URL; global client state in a small Zustand store.
Reach for Redux Toolkit only when the write paths are complex enough that you
want named actions and a devtools time-line — that is a real reason, not a
default.*

## Rung 1 — Server state is not your state

Server state has different rules from client state: it can be stale, refetched,
cached, invalidated, shared by many components, and it has loading and error
states that a `useState`+`useEffect` pair does not model.

The tell that you've reinvented it badly:

```tsx
// You have written a query library, badly, in every component that loads data.
const [data, setData] = useState(null);
const [loading, setLoading] = useState(true);
const [error, setError] = useState(null);
useEffect(() => { /* fetch, race handling, retry, cancel */ }, [id]);
```

Use TanStack Query (v5) — or SWR if you prefer a smaller surface. One provider at
the root, then:

```tsx
// Query keys are the cache identity. Give them a factory so typos are impossible.
const orderKeys = {
  all: ['orders'] as const,
  list: (filters: Filters) => [...orderKeys.all, 'list', filters] as const,
  detail: (id: string) => [...orderKeys.all, 'detail', id] as const,
};

const { data, isPending, error, refetch, isFetching } = useQuery({
  queryKey: orderKeys.list(filters),
  queryFn: ({ signal }) =>
    fetch(`/api/orders?${qs(filters)}`, { signal }).then(checkStatus),
  staleTime: 30_000,        // don't refetch on every focus for 30s
});

// Invalidate by prefix — every list variant refetches.
await queryClient.invalidateQueries({ queryKey: orderKeys.all });
```

Rules that matter:

- **`enabled` beats a mounted flag.** `useQuery({ queryKey, queryFn, enabled: !!id })`
  — no manual `useEffect` to skip the fetch.
- **Mutations invalidate.** `onSuccess: () => queryClient.invalidateQueries(...)`.
  Do not hand-`setState` the response of a mutation into a list; you will miss
  the other three places that show the same data.
- **`staleTime` is per query, not global.** Global `staleTime: Infinity` means
  your app never shows fresh data and you don't notice.
- **Optimistic updates need `onMutate` + `onError` rollback + `onSettled`
  invalidate**, all three. Skipping the rollback leaves a rejected change on
  screen.
- **`queryKey` must contain every value the `queryFn` reads.** A `page` or
  `filter` read from a closure but not in the key means the second page shows
  the first page's data.

## Rung 2 — If the back button should work, it's URL state

Tabs, filters, pagination, sort order, search query, which entity's drawer is
open, wizard step. Anything a user would expect to survive a refresh or be
linkable.

```tsx
// Next.js App Router
const router = useRouter();
const searchParams = useSearchParams();
const page = Number(searchParams.get('page') ?? 1);
const setPage = (n: number) => {
  const next = new URLSearchParams(searchParams);
  next.set('page', String(n));
  router.replace(`?${next}`, { scroll: false });  // replace for filters, push for nav
};
```

Gotchas:

- **`useSearchParams` is a client hook.** A `Suspense` boundary is required
  during static rendering, or Next will bail out of the static shell.
- **Write a search input to the URL on a debounce**, not on every keystroke —
  otherwise every character is a history entry and a re-render storm. Use
  `router.replace`, and `push` only for genuinely navigable steps.
- **The URL is a public contract.** Parse and validate it; a hand-edited
  `?page=-1` or `?filter=<script>` must not crash the page.
- **The URL is not for high-frequency state**: cursor position, hover, an open
  native select. It serialises on every change and it is a string.

## Rung 3 — Forms

Forms are a fourth category, not a `useState` exercise. Use a form library
(React Hook Form; Zod for the schema) when the form has more than a couple of
fields or any async validation.

```tsx
const schema = z.object({
  email: z.string().email(),
  seats: z.coerce.number().int().min(1).max(50),
});
type Form = z.infer<typeof schema>;

const form = useForm<Form>({ resolver: zodResolver(schema), defaultValues: {...} });
const { register, handleSubmit, formState: { errors, isSubmitting } } = form;
```

Do not mirror a form into state field-by-field unless you need live
character-count validation *while typing*; a `watch()` subscription is enough.
And do not hand-roll `onChange` handlers that duplicate what `register` does.

## Rung 4 — Local state, honestly

`useState` for independent values, `useReducer` when transitions must be atomic
or the state machine has more than two or three states:

```tsx
type Status = 'idle' | 'loading' | 'success' | 'error' | 'empty';
// Two useState booleans can render (loading=true, error=Error) — impossible states.
```

If you find yourself writing `isLoading && !error ? 'loading' : ...`, you want
a union type, not the boolean. See `references/state-placement-heuristics.md`.

## Rung 5 — Global client state, sparingly

Rung 5 is for state that is (a) genuinely global, and (b) genuinely not server
state and not URL state. Typically: the auth session's non-fetched parts, a
"has completed onboarding" flag, the active workspace, an entitlement
(`isPro`), the theme, an undo/redo buffer.

Zustand's shape — one store, selectors, no provider nesting:

```tsx
// sessionStore.ts
type Session = { userId: string; entitlements: string[] } | null;
export const useSession = create<{ session: Session; setSession: (s: Session) => void }>(
  (set) => ({ session: null, setSession: (session) => set({ session }) }),
);

// In a component — subscribe to the slice, not the store:
const userId = useSession((s) => s.session?.userId);
```

Rules:

- **Select the narrowest slice.** `useStore()` with no selector re-renders on
  every store change; that is the store equivalent of a context storm.
- **Actions live in the store**, not in components, so components don't need
  effect-based persistence:
  ```ts
  export const useTheme = create<ThemeState>()(
    persist((set) => ({
      theme: 'light',
      toggle: () => set((s) => ({ theme: s.theme === 'light' ? 'dark' : 'light' })),
    }), { name: 'theme' }),   // persist via zustand/middleware, not a useEffect
  );
  ```
- **`persist` middleware, not a `useEffect` that writes to storage.** The
  effect version has a first-render read-after-write flash and a write on every
  render.
- **Redux Toolkit when you need it**: complex write paths, many interacting
  slices, a requirement for a serialisable, auditable action log, a team
  convention. Use RTK's `createSlice` and `createAsyncThunk`; do not hand-roll
  action types, and do not put server data in Redux — use RTK Query, which is
  the Redux answer to rung 1.
- **Zustand vs Redux is not a real technical debate.** Zustand is less ceremony
  and less tooling; RTK is more structure and devtools. Pick one, don't have
  both, and don't migrate for a benchmark.

## Anti-patterns to reject in review

**1. Mirroring props into state.** Two sources of truth, one render behind,
plus a `useEffect` to keep them in sync. Use the prop, or key the component:

```tsx
// Reject
const [name, setName] = useState(props.name);
useEffect(() => setName(props.name), [props.name]);
// Reject harder
useEffect(() => setName(p), [p]);   // p is a new object each render → loop
// Fix: read the prop. Remount with a key when the input resets.
<ProfileForm key={user.id} initial={user} />
```

**2. Context as a state manager.** Context broadcasts to every consumer. A
provider whose `value` is a new object literal re-renders **every consumer** on
every provider render, and React does not bail out on context value identity the
way `memo` does for props.

```tsx
// Storm: value is a new object every render, so all consumers re-render.
<ThemeContext.Provider value={{ theme, setTheme }}>
// Better: split contexts by update frequency, or push state to a store.
const ThemeCtx = createContext<string | null>(null);   // narrow
const ThemeDispatchCtx = createContext<(t: string) => void>(() => {});
// Best for global app state: a Zustand store with a selector — no provider at all.
```

Rules: split dispatch from state, memoise the value, put state in a store if it
is global, and never put fast-changing values (cursor, drag position, keystroke
buffer) in context.

**3. A store for what is really a URL param or server state.** A `currentTab`,
`filters`, `page`, or `activeId` in Zustand is a bug the first time someone hits
the back button or shares a link. Put it in the URL. A `users` array in Zustand
is a query cache that you now have to invalidate by hand.

**4. Global "god store".** One store for the whole app means every feature
touches the same file and every change risks a circular import. Slice by
concern, one file per concern, and keep the session/theme/app-shell store
separate from feature state.

**5. `useReducer` for two booleans, `useState` for a five-state machine.** Pick
the one that matches the shape, not the one that is more impressive.

**6. Optimistic state with no invalidation.** Show the optimistic value, then
invalidate on settle, so any component reading the same key converges.

## Diagnosing re-render storms

- React DevTools Profiler → "Why did this render?" — it names the prop or hook
  change.
- Log a render count per suspect component; if it grows without a state change,
  a context value or store selector is the cause.
- `React.memo` is a last resort, not a fix. A memoised child whose props
  include an inline function or object literal re-renders anyway; stabilise the
  prop first (`useCallback` / hoist it / pass an id).

## Gotchas

- **In React 18 StrictMode, effects run twice on mount in development only** —
  mount, unmount, remount. A `useEffect` that creates a WebSocket, a timer, or a
  subscription without cleaning up in the return leaks a duplicate in dev and
  looks like a production bug that only reproduces in development. Cleanup is
  the only fix; you cannot suppress it.
- **A `useEffect` keyed on an object or array literal loops forever.**
  `useEffect(() => setState(props.options), [props.options])` where
  `props.options` is rebuilt inline each render: setState schedules a render,
  the render builds a new array, the effect fires again, forever in every
  environment. Depend on a primitive — a joined string or a length — or hoist
  the literal.
- **`React.memo` does not stop a context re-render.** `memo` bails out on equal
  props, but a consumer of a changed context value re-renders regardless of
  whether its props moved. Memoising children inside a provider that changes
  every render buys nothing; the provider's value identity is the whole problem.
- **The stale-closure bug is an omitted dependency, not a `useCallback`.** An
  effect reading `filters` from props without listing `filters` captures the
  first render's value forever; the callback identity is irrelevant. List the
  dependency or hold the value in a `useRef` and read the ref inside.
- **A request's late response overwrites newer data.** The `useState`+`useEffect`
  version has no abort, so page 2's slower response lands after page 3's and you
  render page 2 for page 3. Pass the `signal` from the TanStack Query `queryFn`
  and check `signal.aborted`; also key results by generation if you keep the
  hand-rolled version.
- **`useSyncExternalStore` (and therefore every Zustand selector) compares by
  `Object.is` on the selected slice.** A selector returning an object or array
  literal (`s => ({ a: s.a, b: s.b })`) returns a new reference every render, so
  React concludes the store changed and re-renders on every store write. Select
  primitives, or memoise with `useShallow`.
- **`useReducer`'s dispatch is stable for the reducer's whole life** — that is
  why `dispatch` is the one dependency you may omit. The same is true of
  `setState` from `useState`, which is why so many bad examples list it. The
  instability is in the *state* and *callbacks* you read, not the setter.
- **A list keyed by the index reorders content and breaks the DOM node's
  identity**, so focus, selection, scroll position, and uncontrolled input values
  follow the index instead of the item. Key by a stable server id; a `key` on a
  changing value remounts the node and throws away its state.
- **Zustand's `persist` rehydrates asynchronously on the first render**, so the
  store starts at its default (`theme: 'light'`) and flips on the next tick — a
  hydration mismatch and a theme flash. Gate on a rehydrated flag, or use
  `skipHydration` plus an explicit call in an effect.
- **A `useSearchParams` write on every keystroke in Next.js App Router
  transitions the whole route** and pushes a history entry per character, so
  Back steps through the query string character by character. Debounce and use
  `router.replace`; reserve `push` for steps a user would call a destination.
- **Two sources of truth for the same server entity never converge without
  invalidation.** After a mutation, hand-set a component's state and it drifts
  from the query cache until the next refetch; the second render of the page
  shows the stale half. Let the cache own it and invalidate.
- **Derived state in an effect is an extra render and a source of stale frames.**
  Computing a filtered list or a total with `useState` + `useEffect` gives you
  one paint with the unfiltered value. Compute it during render, or
  `useMemo` it.

## Review checklist

- [ ] Does each piece of state have exactly one home?
- [ ] Is any server-cached object also in a component or store?
- [ ] Would a refresh or a shared link preserve the state the user expects?
- [ ] Do context values have stable identities and split state from dispatch?
- [ ] Do store components select the narrowest slice?
- [ ] Does every mutation invalidate the queries it affects?
- [ ] Is any prop mirrored into state "to make it editable"? (use `key`)

## Files

- `references/state-placement-heuristics.md` — the "which rung?" decision
  procedure, with the tell-tale symptoms that a state is in the wrong place.

---
name: react-hooks-deep-dives
description: Fix the React bugs that only show up in a running app - useEffect dependency loops ("Maximum update depth exceeded"), missing cleanup and leaked subscriptions, stale closures over timers and event handlers, wrong useLayoutEffect vs useEffect timing, and effects that should not exist at all. Use when someone says "infinite loop", "effect runs twice", "stale value", "stale closure", "setState in useEffect", "component re-renders forever", "memory leak in React", "exhaustive-deps warning", or when reviewing a custom hook for correctness. Triggers on "useEffect", "dependency array", "cleanup function", "StrictMode double render", "useSyncExternalStore", "useEffectEvent", "derived state".
compatibility: React 18+; React 19 features marked as such. Examples in TypeScript/JSX.
metadata:
  version: "1.0"
---

# React Hooks Deep Dives

`useEffect` is for synchronising with something *outside* React: the network, the
DOM, a subscription, an imperative API. It is not for computing values, resetting
state when props change, or storing data that React already has.

Most "React is broken" bugs are a violation of that sentence.

## Workflow

Progress:

- [ ] 1. Decide whether the effect is needed at all (below)
- [ ] 2. If needed: list the reactive values the effect body actually reads
- [ ] 3. List exactly those in the dependency array — nothing more, nothing less
- [ ] 4. Return a cleanup that makes the effect re-runnable without leaking
- [ ] 5. Check the loop and stale-closure traps in Gotchas
- [ ] 6. Verify in StrictMode (React double-invokes effects in dev on purpose)

## Step 1 — Do you need the effect at all?

Three checks, in order. Most code that needs an effect fails the first one.

**Does it synchronise with something outside React?** Network, WebSocket,
`document`, `window`, a third-party SDK, an interval, a subscription. Yes →
keep the effect.

**Is the value derivable during render?** Then compute it, don't store it:

```tsx
// Wrong: two sources of truth, one render behind, and an extra render.
const [full, setFull] = useState('');
useEffect(() => { setFull(`${first} ${last}`); }, [first, last]);

// Right: computed on every render, always consistent.
const full = `${first} ${last}`;
const isEmpty = items.length === 0 && !loading;
```

**Are you syncing state to a prop?** Reset state when a prop changes by
*keying* the component, not with an effect:

```tsx
// Wrong: extra render, plus a paint where both old and new value are visible.
useEffect(() => { setDraft(props.initial), [props.initial] });

// Right: parent remounts the subtree with a fresh key when the prop changes.
<ProfileForm key={userId} initial={user} />
```

`useMemo` is the escape hatch for an expensive derivation; plain computation is
fine unless it measurably costs you. See `references/effect-decision-table.md`
for the full ladder.

## Step 2 — The dependency array: exact semantics

- React compares each dependency with **`Object.is`**, and runs the effect
  again if *any* differ. `NaN` equals `NaN` under `Object.is`; `0` and `-0` do
  not.
- The array **length must be constant** between renders. Conditional deps are a
  bug, not a lint warning to silence:
  `The final argument passed to useEffect changed size between renders.`
- In React 18+ StrictMode dev, React **mounts, unmounts, and remounts** every
  effect once on first mount. A cleanup that is not idempotent will break here
  and only here — treat this as a free bug report.
- `react-hooks/exhaustive-deps` is a **correctness** check, not a style check.
  Do not disable it. When it complains about a function or object, that
  complaint is usually a real stale value.

### What makes an effect loop

The error in the console is one of:

```
Maximum update depth exceeded. This can happen when a component repeatedly
calls setState inside componentWillUpdate or componentDidUpdate. React limits
the number of nested updates to prevent infinite loops.
```

```
Too many re-renders. React limits the number of renders to prevent an infinite
loop.
```

Causes, most common first:

1. **A new object/array/function every render** in the deps, when the effect
   *writes* state. `useEffect(() => setX(y), [{ ...y }])` loops forever. Fix
   with primitive deps: `[y.id, y.count]`, or `useMemo` the object so the
   identity is stable.
2. **`setState` during render** (not in an effect) — the "Too many re-renders"
   variant. Move it to an effect, derive it, or bail out:
   ```tsx
   if (prev !== next) { setPrev(next); }  // the documented store-previous pattern
   ```
3. **A `getSnapshot` that returns a fresh object** (store subscription):
   `The result of getSnapshot should be cached to avoid an infinite loop.` Return
   primitives, or cache with `useMemo`/`useSyncExternalStore`'s selector, or use
   Zustand's `useShallow`.
4. **A stable-looking dep that isn't.** `Date.now()` in the array, a class
   instance, a JSX element, or `new Date()`.

## Step 3 — Cleanup: make the effect re-runnable

An effect without cleanup leaks the previous run. If the effect can run twice,
cleanup must tear down the first run completely.

```tsx
useEffect(() => {
  const controller = new AbortController();
  let cancelled = false;

  (async () => {
    const res = await fetch(`/api/orders/${id}`, { signal: controller.signal });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    if (!cancelled) setOrders(data);
  })().catch((e) => { if (e.name !== 'AbortError') setError(e); });

  return () => { cancelled = true; controller.abort(); };
}, [id]);
```

Both guards are needed. `controller.abort()` stops the *network*; `cancelled`
stops a response that already resolved from calling `setState` on an unmounted
component. Skipping the second gives you the React 18 warning
`Can't perform a React state update on an unmounted component` (still noisy even
though it is a no-op) and races where a slow first request overwrites a fast
second one.

Cleanup is required for: timers, `addEventListener`, `IntersectionObserver`,
`ResizeObserver`, `setInterval`, `requestAnimationFrame`, WebSocket, any
`navigator.*.lock()`, and every async fetch.

**The one-line trap:** a callback that *sometimes* returns a value is treated
as a cleanup function.

```tsx
useEffect(() => {
  if (props.disabled) return;          // fine — returns undefined
  return props.onSave();                // BUG: whatever onSave returns is the cleanup
  // If onSave returns a Promise, React will try to call it: "cleanup is not a function"
}, [props.disabled]);
```

Use a block body: `return () => { props.onSave(); }`.

## Step 4 — Stale closures

An effect (and every render callback: `onClick`, `setInterval`, `addEventListener`)
closes over the values from the render that created it. If state changes
afterwards, the callback still sees the old value.

Three fixes, in order of preference:

```tsx
// 1. Functional updater — the right answer for "next state depends on prev".
setCount(c => c + 1);

// 2. Put the changing value in the deps, so a fresh closure is made.
//    This is correct for effects, and wrong for stable-callback consumers
//    (see below).
useEffect(() => { const t = setInterval(() => setCount(c + 1), 1000);
  return () => clearInterval(t); }, []);

// 3. A ref, when the identity must stay stable but the value must be current.
const latest = useRef(0);
useEffect(() => { latest.current = value; }, [value]);
// callback: setCount(latest.current + 1)
```

For stable-callback consumers (`onSubmit` passed to a memoised child, a
subscription that must not resubscribe every render), the modern answer is
`useEffectEvent` / `useEvent`, described below.

## Step 5 — `useLayoutEffect` vs `useEffect`

`useLayoutEffect` runs after React mutates the DOM but **before the browser
paints**. `useEffect` is deferred, normally until after paint (React may run it
first if the update came from a discrete interaction like a click, or inside
`act`/tests).

| Situation | Hook |
|---|---|
| Measure a DOM node and set state, avoiding a visible jump | `useLayoutEffect` |
| Read layout and paint the result yourself (`selection`, scroll restore) | `useLayoutEffect` |
| Sync with an external store after commit | `useEffect` (or `useSyncExternalStore` — better) |
| Fetch, analytics, event logging, subscriptions | `useEffect` |
| Anything that can block first paint | `useEffect` |

```tsx
// Measure before paint, so the user never sees the unstyled position.
useLayoutEffect(() => {
  const h = el.current?.offsetHeight ?? 0;
  setHeight(h);
}, [deps]);
```

Gotcha: `useLayoutEffect` in server-rendered React logs
`useLayoutEffect does nothing on the server, because its effect cannot be
encoded into the server renderer's output format` in React 18 and earlier, and
blocks the paint while it runs. Use the isomorphic alias in shared code:

```tsx
const useIsomorphicLayoutEffect =
  typeof window !== 'undefined' ? useLayoutEffect : useEffect;
```

Run `useLayoutEffect` on a list and you can turn a 50ms layout into a
perceived lag. It is for correctness-under-paint, not for "run earlier".

## Step 6 — Effect ordering and the async race

- **Effects run child-first, then parent.** Cleanups run parent-first, then
  child. Do not depend on the order of two effects in *different* components.
- **Async effects race.** Two runs of the same effect can resolve out of order
  and the slower one wins. Guard with the `cancelled`/`ignore` flag above, or
  `AbortController`, or a monotonic request id if the signal is not plumbed.
- **StrictMode's extra run is a feature.** Fix cleanup until the double
  mount/unmount is clean; it is a free test for every leak in the component.

## `useEffectEvent` and experimental patterns

`useEffectEvent` returns a stable-identity function whose body always sees the
latest props and state. You call it from inside an effect or an event handler,
and you **omit it from the dependency array**.

```tsx
const onFetch = useEffectEvent(async (id: string) => {
  const res = await fetch(`/api/orders/${id}`, { userId });   // always current
  setData(await res.json());
});

useEffect(() => { onFetch(orderId); /* [orderId] only */ }, [orderId]);
```

Caveats, stated honestly:

- Availability is version-dependent and has moved through several names:
  `experimental_useEffectEvent` from `react`, the `useEvent` callback in React
  19 canary/RC and third-party shims, and a stable `useEffectEvent` export in
  later 19.x. **Check what your installed React actually exports** before using
  it — this is a one-line check:
  ```bash
  node -e "console.log(require('react').useEffectEvent ?? 'not available')"
  ```
  If it is not available, do not add a shim package to get it; use the `ref`
  or functional-updater patterns above, which work on every version.
- The linter knows about it. If `exhaustive-deps` flags it, the code is wrong.
- It is **not** a state updater: calling `setState` inside an effect event that
  you also render is a "cannot update a component while rendering" error.
- `useSyncExternalStore` (React 18+) is the correct hook for reading *any*
  external store — Redux, a router, `window.matchMedia`, a websocket. It is
  tear-free under concurrent rendering, which a hand-rolled
  `useState` + `subscribe` effect is not.

## Gotchas

- **`useMemo`/`useCallback` are performance hints, not guarantees.** React may
  discard a memoised value (and `useMemo` results are not kept indefinitely for
  offscreen trees). Never put correctness — an identity comparison that gates a
  write — behind them.
- **Do not write `useMemo(() => x, [x])`.** It is a no-op with extra code. Only
  memoise an expensive computation or an identity that must be stable.
- **An empty dep array that reads props is the most common stale closure.** It
  is lint-visible, so fix the deps rather than disabling the rule.
- **`JSON.stringify(obj)` as a dep works but is a lie**: it drops `undefined`
  and function values, and `{a: undefined}` and `{}` compare equal. Depend on
  the primitive fields you actually branch on.
- **Do not put `props` or `props.x` in the array** — depend on the specific
  fields, so a new props object identity does not re-run the effect.
- **`useState` setter functions are stable.** `[setUser]` is a valid, free
  dependency. The same is true of a `useReducer` dispatch.
- **`useState(fn)` vs `useState(fn())`**: the first treats `fn` as a lazy
  initialiser, the second *calls* it and stores the result. A common source of
  "my state is a function".
- **`setState` with the same value bails out** — except when you pass a
  function or a fresh object, which always re-renders.
- **`useReducer` for state that changes together.** Two `useState` calls that
  must move together can render once with an inconsistent pair. One reducer
  makes every transition atomic.
- **Cleanup runs on every dependency change, not just unmount.** If setup is
  expensive, that is your real problem — debounce or restructure the deps
  rather than removing cleanup.
- **Effects do not run during SSR** (`useEffect`, not `useLayoutEffect`). A
  context provider set up in an effect won't exist on the server render.

## Debugging loop

1. Turn on StrictMode: `<StrictMode><App /></StrictMode>`. Anything that breaks
   now is a real bug.
2. Put a `console.count('effect')` in the effect. Count > 1 on mount means a
   dep is unstable.
3. `useEffect(() => { console.log(deps) }, deps)` and watch the array contents
   in the DevTools console.
4. Read `references/effect-decision-table.md` when you have the pattern in
   hand and need to decide the cleanup strategy, or when choosing between
   `useEffect`, `useLayoutEffect`, `useSyncExternalStore`, and `useEffectEvent`.

## Files

- `references/effect-decision-table.md` — hook choice, per-situation dependency
  list, and cleanup strategy tables.

# Effect decision table

Load this when you have a concrete effect and need to pick the hook, the
dependency list, and the cleanup strategy. When you need to justify a removal of
an effect entirely, start from the ladder in Step 1 of `SKILL.md`.

## 1. Which hook

| What the code is doing | Hook | Why |
|---|---|---|
| Subscribe to network / WebSocket / SDK callback | `useEffect` | After commit, cancellable |
| Analytics, logging, imperative side effect | `useEffect` | Must not block paint |
| `fetch`/async read of server data | `useEffect` (or a query library — prefer it) | Retry/cancel/park all in one place |
| Read a DOM node and set state from its size/position | `useLayoutEffect` | Avoids a visible pre-measure frame |
| Restore scroll position, apply a text selection, autofocus | `useLayoutEffect` | Must happen before paint |
| Read an external store (Router, Redux, `matchMedia`, socket state) | `useSyncExternalStore` | Tear-free under concurrent rendering |
| Anything that must also run on the server / during hydration data load | No effect | `useSyncExternalStore` with `getServerSnapshot` |
| Read a stable-identity latest value from inside a stable callback | `useEffectEvent` (React 19.2+) or a ref | Dep array stays minimal |
| Compute a derived value | Nothing | Compute in render, or `useMemo` if expensive |
| Reset state when an id changes | Nothing | `key` prop on the component |

## 2. Dependency list per situation

Dependency list is *the values the effect body reads that can change*, nothing
more. Read the body; don't pattern-match.

| Effect body reads | Deps |
|---|---|
| `props.id` only | `[props.id]` |
| `props.id` and `props.enabled` | `[props.id, props.enabled]` |
| `onSaved` from props (stable, should not retrigger) | `useEffectEvent` for the call, `onSaved` excluded from deps |
| `setUser` (a `useState` setter) | `[setUser]` — free, already stable |
| `dispatch` from `useReducer` | `[dispatch]` — free, already stable |
| `state.count` in a tick | Functional updater: `setCount(c => c + 1)`, no dep at all |
| An object `opts` (16 keys) used branch-free | `useMemo(() => opts, KEYS)` at the call site, then `[opts]` — or split into primitives |
| The store's whole snapshot | `useSyncExternalStore(subscribe, getSnapshot)` — no dep array |
| One field of a store snapshot | `useStore(s => s.count)` (Zustand) — the selector is the dep, done |

### Never do this

```tsx
// Re-runs on every render, because props is a new object each time.
useEffect(() => { doThing(props); }, [props]);
useEffect(() => { doThing(thing); }, [{ a, b }]);   // new literal every render
useEffect(() => { doThing(now); }, [Date.now()]);   // never equal
```

## 3. Cleanup strategy per resource

| Resource | Setup | Cleanup |
|---|---|---|
| `setTimeout` | `const t = setTimeout(fn, ms)` | `clearTimeout(t)` |
| `setInterval` | `const t = setInterval(fn, ms)` | `clearInterval(t)` |
| `requestAnimationFrame` | `const id = requestAnimationFrame(fn)` | `cancelAnimationFrame(id)` |
| `addEventListener` | `el.addEventListener('resize', fn, opts)` | `el.removeEventListener('resize', fn, opts)` — **same function identity and same `capture`** |
| `IntersectionObserver` / `ResizeObserver` | `new IO(cb); io.observe(el)` | `io.disconnect()` (or `.unobserve(el)`) |
| `fetch` | `await fetch(url)` | `controller.abort()` **and** a `cancelled` flag before `setState` |
| WebSocket | `const ws = new WebSocket(url)` | `ws.close()`; also set a `closed` flag so late `onmessage` is dropped |
| `setTimeout` retry loop | `schedule()` | `cancelled = true` in cleanup so the loop stops rescheduling |
| Store subscription | `const un = store.subscribe(fn)` | `un()` |
| `navigator.locks.request` | — | Release the lock; the promise resolves when the work finishes |
| Third-party SDK (`analytics.init`) | `sdk.init(opts)` | `sdk.teardown()` / `destroy()` — check the SDK's docs; this is where leaks hide |

### The `removeEventListener` identity trap

```tsx
// Broken: the cleanup removes a different function than the one added.
el.addEventListener('scroll', () => handler());
el.removeEventListener('scroll', () => handler());
```

Both arrow functions are distinct identities; only the *captured* `handler` is
shared. Keep the handler in a variable:

```tsx
const onScroll = (e: Event) => handler(e);
el.addEventListener('scroll', onScroll, { passive: true });
return () => el.removeEventListener('scroll', onScroll);
```

`passive: true` on scroll/touch/wheel listeners is a correctness improvement,
not just perf: it lets the compositor start scrolling without waiting for your
`preventDefault()`.

## 4. Async effect template

Use this for every effect that awaits something whose result could arrive after
the effect is stale.

```tsx
useEffect(() => {
  const controller = new AbortController();
  let cancelled = false;

  void (async () => {
    try {
      const res = await fetch(`/api/x/${id}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      if (!cancelled) setData(json);
    } catch (err) {
      if (cancelled || (err as Error).name === 'AbortError') return;
      setError(err);
    }
  })();

  return () => { cancelled = true; controller.abort(); };
}, [id]);
```

Checklist before you call an async effect done:

- [ ] Does it have a cleanup that both aborts and stops a late `setState`?
- [ ] Does it distinguish `AbortError` (expected) from a real network error?
- [ ] Is the error state cleared on the next successful run, or does a previous
      error linger after a retry?
- [ ] If the component is on a screen that can be visited twice, does the
      second visit refetch, or does it reuse the first visit's result forever?
- [ ] Does the effect write to a store that outlives the component? If so, the
      `cancelled` flag protects the component, not the store.

## 5. Effect removal ladder

Walk down until the first one applies.

1. **Is there an external system?** If not, you do not need an effect.
2. **Can the value be computed from props/state during render?** Compute it.
   (`useMemo` only if the computation is measurably slow.)
3. **Is this "reset state when a prop changes"?** Use a `key` on the component.
4. **Is this "notify the parent a child state changed"?** Call the callback
   during the event, not from an effect.
5. **Is this "fetch on mount, refetch on id change"?** A query library
   (`react-query`, SWR) does this with cancellation, retry, and caching
   handled — see the `react-state-management` skill.
6. **Is this "animate a value when X changes"?** That's not `useEffect`; use
   `react-spring` / Framer Motion / Reanimated, which read the previous value
   in render rather than in an effect.

Only steps 1–3 are worth the trouble to hand-roll.

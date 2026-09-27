# State placement heuristics

Load this when a specific piece of state is in the wrong place and you need to
name *which* rung it belongs on. When you already know the answer, the ladder in
`SKILL.md` is enough.

## Decision procedure

Answer in order; the first "yes" names the rung.

1. **Does a fetch own this value, and would a second component want the same
   data with its own loading and error state?** → Rung 1, query library.
   Symptom: two components each `useEffect` the same URL, and they disagree.
2. **Would a user expect the back button, a refresh, or a shared link to
   preserve it?** → Rung 2, URL. Symptom: someone reports "the back button is
   broken" or "I can't share this view".
3. **Is it a form the user is actively filling in, with validation and a
   submit lifecycle?** → Rung 3, form library. Symptom: fifteen `useState`
   calls in one component and a submit handler that forgets one field.
4. **Does only this component and its children need it, and it goes away with
   the component?** → Rung 4, `useState`/`useReducer`. Symptom: a prop drilled
   through four layers that only one leaf uses — that prop should have been
   state in the leaf, or lifted less far.
5. **Is it read by unrelated parts of the app, is it not server data, and is
   it not URL-shaped?** → Rung 5, small store.
6. **Are the write paths complex, audited, and numerous, with a team
   convention?** → Rung 5b, Redux Toolkit.

If you answered 5 and 6 both "yes", the honest answer is RTK, because the
tooling (devtools time travel, action log) is what you are paying for.

## Symptom → likely misplacement

| Symptom in review or bug report | Almost always |
|---|---|
| Component re-renders even though nothing it uses changed | Context value is a new object; or a store with no selector |
| Two components show different values for "the same" data | Server state fetched twice, or mirrored into local state |
| Back button loses the user's place | URL state stored in a component/store |
| A form resets when a parent re-renders | `key` on the form, or a remount, or form state in the wrong scope |
| `useEffect` syncing two `useState` values | One of them should be derived or be a URL param |
| "State is stale after I save" | Mutation didn't invalidate; or optimistic update with no rollback |
| A whole page flashes on every keystroke | Search input written to the URL per keystroke, or a full-store subscription |
| State survives a logout you didn't expect | Persisted store not cleared on sign-out |
| DevTools shows a component rendering on *every* keystroke anywhere | Global store with a wide selector, or context above the keystroke |
| Scroll position lost on navigation | Scroll state in a store instead of the browser's own restoration / a `key`-remounted list |
| A list flickers when you check a box | The checkbox wrote to the list's data, instead of to a server mutation + invalidation |

## Prop drilling vs lifting vs store

- **Lifting** is the right fix for a value two siblings need. It is local,
  explicit, and free.
- **Drilling** through four layers to reach one leaf is a sign the intermediate
  layers shouldn't know about it: move the state down to the leaf, or move the
  leaf up as a render prop / children.
- **Context** is for a value that is truly ambient (theme, locale, current user
  for auth checks, i18n t()) — not a convenient way to skip prop typing.
- **A store** is for state with no common parent (route-level siblings, deeply
  nested independent features). It buys you "no providers"; it costs you
  debuggability and one more place a bug can live.

## Context that does not storm

If you must use context for a value that changes often, do all of:

1. Split **state** and **dispatch** into two contexts, so consumers that only
   dispatch don't re-render on state change.
2. Memoise the value with `useMemo` and memoise the dispatch with `useCallback`.
3. Keep the value narrow (a string, a small object) — a `useReducer` state and
   its dispatch as two contexts is the standard pattern.
4. Nest providers so that a fast-changing value is provided *low* in the tree,
   not at the root.
5. For anything read very frequently (every row of a list), a store with a
   per-row selector beats context; context re-renders the whole subtree.

```tsx
// The pattern that actually holds up
const StateCtx = createContext<State | null>(null);
const DispatchCtx = createContext<Dispatch<Action> | null>(null);

function Provider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initial);
  // useMemo matters: without it the value is new each render, defeating memo.
  const s = useMemo(() => state, [state]);
  return (
    <DispatchCtx.Provider value={dispatch}>
      <StateCtx.Provider value={s}>{children}</StateCtx.Provider>
    </DispatchCtx.Provider>
  );
}
```

## Store discipline (Zustand / RTK)

- **One store per concern**, not one app store. `useSession`, `useTheme`,
  `useFilters` in separate files.
- **Components select; they never `getState()` during render.** A
  `getState()` read outside render/selector is a value that won't re-render you
  when it changes.
- **Mutate only through named actions in the store**, not `setState` at call
  sites. That keeps the persistence and invalidation hooks in one place.
- **Store is not the cache for server data.** If a value has a
  "last fetched at" or a loading state, it's rung 1.
- **Zustand outside React** (event handlers, API clients, non-component code)
  can read via `useXStore.getState()` — that's legitimate, but it is a
  non-reactive read; make sure you're not using it where a re-render was
  intended.

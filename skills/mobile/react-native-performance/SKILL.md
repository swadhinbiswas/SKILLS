---
name: react-native-performance
description: Make a React Native app fast by working with the JS/UI thread split rather than around it - list virtualisation (FlatList vs FlashList vs ScrollView), image sizing and caching, Reanimated over Animated, avoiding inline objects and functions, and the New Architecture (JSI/Fabric/TurboModules). Use when the app drops frames, when scrolling stutters, when a screen "feels heavy", when startup is slow, or when profiling with the DevTools/perf monitor finds long JS tasks or a bridge bottleneck. Triggers on "scrolling laggy", "jank", "dropped frames", "useNativeDriver", "FlashList", "re-render loop", "slow list", "bridge", "Fabric", "Hermes", "UI thread", "heavy screen".
compatibility: React Native 0.71+; New Architecture (Hermes + JSI, Fabric renderer, TurboModules) is the default from 0.76. Version-specific notes are marked inline.
metadata:
  version: "1.0"
---

# React Native Performance

A React Native frame has a budget of ~16.7ms (60Hz) or ~8.3ms (120Hz). A frame
that misses it drops. Everything on this page is about keeping both the JS
thread and the UI thread under budget, in the right places.

## The model: two threads

- **JS thread**: runs your React code, reconciler, business logic, and
  (historically) the bridge serialiser. A long JS task blocks *all* new React
  work — including a touch handler, so taps feel laggy.
- **UI thread (native/main)**: draws the view hierarchy and runs native
  animations. With the New Architecture, the **Fabric** renderer and native
  **Reanimated** work run here, largely off the JS thread.
- **Bridge (legacy)**: serialised JSON messages between JS and native. In the
  Old Architecture every `setState` producing a view update, and every
  `Animated` value not using `useNativeDriver`, crosses it. Under the New
  Architecture, JSI and the TurboModule system replaced the async bridge for
  most data, and the Fabric renderer makes view mounting cheaper.

**The core lever**: run animation and gesture work on the UI thread
(`useNativeDriver`, Reanimated) so the JS thread is free to handle input and
state the instant it arrives.

## Workflow

Progress:

- [ ] 1. Enable the New Architecture + Hermes (they are the default in RN
      0.76+; confirm, don't assume)
- [ ] 2. Profile with the right tool — don't guess (below)
- [ ] 3. Find the long JS task or the expensive re-render
- [ ] 4. Fix the render cost (memo, list, image) *or* move it off the JS thread
- [ ] 5. Re-profile on a **release** build on a mid/low device

## Lists: the most common jank source

Never `ScrollView.map()` a long list — it renders everything and holds it all in
memory. Use a virtualised list.

| List | Use when | Notes |
|---|---|---|
| **`FlatList`** | Default. You need basics: `data`, `keyExtractor`, `renderItem`, `getItemLayout`. | Virtualises; tuning it is manual |
| **`FlashList`** (Shopify) | You have real scroll perf problems or very large/complex items | v2 requires New Architecture; auto-size-on-draw (no more "why is my list blank?"); recycle aggressively. The upgrade from `FlatList` is usually worth it |
| **`SectionList`** | Grouped data | Virtualised; often fine on top of the above |
| **`ScrollView`** | A handful of non-virtualised items | Fine for <~10 static children; a trap beyond that |

Tuning that matters:

```tsx
<FlatList
  data={rows}
  keyExtractor={(r) => r.id}                    // stable, from the model
  renderItem={renderRow}                          // hoisted, not an inline arrow
  getItemLayout={(_, index) => ({ length: ROW_H, offset: ROW_H * index, index })}
  initialNumToRender={10}                         // first paint
  maxToRenderPerBatch={10}                        // per JS-thread slice
  windowSize={7}                                  // screens of content to keep mounted
  removeClippedSubviews                          // Android: detach offscreen views (helps huge lists; can blank fast scroll — test)
  updateCellsBatchingPeriod={50}                  // batch prop changes
/>
```

- **`renderItem` must be a stable reference and must not create new objects or
  closures per row.** A new inline arrow each render recreates every row
  component:
  ```tsx
  // Wrong: new component type per render → full remount, no memo help.
  renderItem={({ item }) => <Row item={item} onPress={() => go(item.id)} />}
  // Better: stable component + memo + stable per-row data.
  const Row = memo(function Row({ item, onPress }) {
    return <Pressable onPress={onPress} … />;
  });
  // Pass the id, not a new arrow: the list binds the handler per item.
  renderItem={({ item }) => <Row item={item} onPress={handlePress} />}
  ```
- **Memoise the item component and its props.** If a row's props include
  `{ onPress: () => ... }` or a styled object literal, `memo` never hits.
  Pass primitives/ids and stable handlers (see below).
- **`getItemLayout`** lets the list jump to an offset without measuring —
  a big win for fixed-height rows and for `scrollToIndex`.
- **`keyExtractor` from the data id**, never the array index — index keys
  break memoisation and reorder badly.

## Images

Images are a huge cost (decode memory, network, and layout) and a common cause
of blank-then-pop rows.

- **Always set explicit `width`/`height` (or `aspectRatio`)** on `<Image>`.
  An unsized image reserves no space, so the row relayouts as it loads (jank +
  flicker). With Fast Refresh you can see rows "pop".
- **Size the source to the rendered size × DPR.** A 4000px image in a 200pt row
  wastes memory and decode time. Use `resizeMode="cover"` + correct `width`.
- **Use a caching image component**, not bare `<Image>`, if you're loading
  remote images: `@d11/react-native-fast-image` (or
  `expo-image`) caches to disk, decodes off-thread, and avoids the
  placeholder-flash that bare `Image` gives on scroll. FastImage is archived;
  for new work on Expo, **`expo-image`** (with a memory/disk cache) is the
  current default. On bare RN, `react-native-fast-image` still works but is in
  maintenance — verify maintenance status before adopting.
- **`cache: 'only-from-network' | 'only-from-disk' | 'default'`** on `Image`
  controls caching; set it deliberately for images that change server-side.
- **Decode memory is the real cost**: a 4000×3000 image ≈ 48MB in memory
  (width × height × 4 bytes). Virtualised lists that keep too many large
  bitmaps alive are a common OOM — see the
  `mobile-crash-and-perf-diagnostics` skill.

## Animation: Reanimated over Animated

- **`Animated` with `useNativeDriver: true`** runs the animation on the UI
  thread. **`useNativeDriver: false` (the default for some props) runs it on
  the JS thread** and drops frames whenever the JS thread is busy. If you animate
  `left`/`top`/`width`/`height`/`backgroundColor` with `Animated`, you *cannot*
  use the native driver — that is a reason to use Reanimated.
- **Reanimated** (v2/v3/v4) runs worklets on the UI thread and is the default
  choice for RN animation and gestures in new code. It can animate any
  transform/layout prop, and its `useAnimatedStyle` never touches the JS thread
  per frame.
  ```tsx
  import Animated, { useSharedValue, useAnimatedStyle, withSpring } from 'react-native-reanimated';
  const x = useSharedValue(0);
  const style = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  // onPanResponder / Gesture.Pan().onUpdate(e => { x.value = e.translationX; })
  ```
  - v3 needs the **Babel plugin** (`react-native-reanimated/plugin`, last in
    the plugins list) and works on the UI thread. v4 (Reanimated 4) is New
    Architecture–only. Check your version; on the New Architecture prefer v4,
    on the Old Architecture v3.
- **Animate `transform` (translate/scale/rotate), not `top/left/width`** —
  transforms are cheap on both architectures; layout props are not.
- **Gesture Handler** (`react-native-gesture-handler`) runs gestures on the UI
  thread and pairs with Reanimated; prefer it over PanResponder for anything
  scroll-linked or high-frequency.
- **`LayoutAnimation` is unreliable** across the New Architecture/Fabric for
  list reordering; don't build critical UX on it. Reanimated layout animations
  (`LinearTransition`, `Layout`) are the supported path.

## Render-cost rules

The RN render is synchronous-ish work on the JS thread; make each render cheap
and make renders *not happen*.

- **`React.memo` every list row and any heavy leaf.** Compare the props you
  actually pass; a new object/array/function prop defeats it.
- **No inline objects or functions in props/styles.** A new `style={{…}}`,
  `source={{uri}}`, or `onPress={() => …}` per render is a new reference each
  time. Hoist, memoise, or use `StyleSheet.create` (which returns stable
  objects) and stable `useCallback` handlers.
  ```tsx
  // Wrong: new object each render.
  <View style={{ padding: 8 }} />
  // Right: stable, and flattened for native.
  const s = useMemo(() => ({ padding: 8 }), []);
  // Or stylesheet (stable + flattened):
  const styles = StyleSheet.create({ box: { padding: 8 } });
  ```
- **`useCallback`/`useMemo` only to protect identity, not for micro-gains.**
  They cost a little; they buy referential stability for `memo`/effects. Use
  them for props passed to memoised children and for effect deps, not
  everywhere.
- **Don't compute heavy work in render** (sorting/filtering a big array, JSON
  parsing). Memoise on the inputs, or move it to a memoised selector, or
  precompute in a store.
- **`FlatList` + a state store that re-renders the whole list** on every
  keystroke: keep transient UI state (search text) local to the search bar so
  typing doesn't re-render the results list.

## JS vs UI thread: the decision

| Work | Where it should run | How |
|---|---|---|
| Touch/gesture tracking, drag, swipe | UI thread | Reanimated + Gesture Handler worklets |
| Animations (transform, opacity, progress) | UI thread | Reanimated / `Animated` + native driver |
| List scrolling and windowing | native/UI (RN's list) | `FlatList`/`FlashList` (not a JS `ScrollView`) |
| Data fetch, business logic, state | JS thread | Keep it off the frame budget; don't block |
| Heavy compute (parse, sort, crypto, image) | off main | `InteractionManager.runAfterInteractions`, a worker (`react-native-multithreading`/JSI), or move to native |
| Navigating on a tap | JS (handler) but keep the handler tiny | Don't do heavy work in `onPress` before navigating |

`InteractionManager.runAfterInteractions(() => …)` defers non-visual work until
animations/gestures settle — use it for logging, prefetching, and analytics
rather than blocking a frame.

## The New Architecture (JSI / Fabric / TurboModules)

- **What it is**: React Native's New Architecture replaces the async bridge with
  **JSI** (direct JS↔native object access, sync), a **Fabric** renderer (view
  layer built natively, cheaper mounting, better offscreen/texture support), and
  **TurboModules**/`TurboModuleRegistry` (typed, synchronous-capable native
  module interface).
- **Status**: opt-in from 0.68; **default from RN 0.76** (0.76+ ships New
  Architecture on by default, and the legacy architecture is deprecated/being
  removed). Expo SDK 52 (RN 0.76) and later default to the New Architecture.
  If you're on an older RN, you can opt in via `newArchEnabled` in
  `android/gradle.properties` and `RCT_NEW_ARCH_ENABLED`, but third-party libs
  with native code must be New-Arch-compatible.
- **Why it matters for perf**: less bridge serialisation, cheaper list/view
  mounting, synchronous native calls. It *helps* but it does not rescue bad
  render code — the same `renderItem` and `useEffect` problems still drop
  frames, because the JS thread is still the JS thread.
- **Hermes** is the default JS engine (on both Android and iOS from RN 0.76,
  and recommended on earlier versions too). It precompiles your bundle and
  generally starts faster and uses less memory than JSC. Keep Hermes; don't
  switch to JSC for a perf fix — the fix is almost always elsewhere.
- **Fabric caveats**: not every legacy native module/view is Fabric-ready.
  Interop layers exist but with caveats; a non-Fabric component can be the
  jank source. If a screen is inexplicably slow under Fabric, suspect a
  non-Fabric native dependency.

## Profiling: don't guess

- **React DevTools Profiler** (works in RN): flame chart, commit times,
  "why did this render" (double-tap a component in the flame chart). Look for
  long commits and re-render frequency.
- **Flipper is retired** (Meta sunset it; the successor is the RN DevTools
  frontend). Don't build a workflow around Flipper.
- **RN DevTools** (in newer RN) — the current standalone debugger/performance
  tooling. Check your version's docs for the exact menu.
- **Perf Monitor** (in dev, `dev menu → Performance`): shows FPS and, with the
  New Architecture, JS/UI frame times; the in-app FPS overlay (RedBox FPS
  Monitor) is a fast field signal.
- **On device, use a release build** (`--mode release` in Metro/`react-native
  run-android --mode release`). Dev builds are slower and the StrictMode-like
  dev behaviours mislead. Profile a real mid-range device, not a simulator.
- **Android**: `adb shell dumpsys gfxinfo <pkg> framestats`, or
  Perfetto/`dumpsys gfxinfo`. **iOS**: Instruments (Time Profiler, Allocations,
  Animation Hitches), Xcode's "Slow Animations"/"Debug Color Layer" toggles.
- **The Perf Monitor FPS number** dropping below ~55–60 means frames are being
  missed; profile the trace, don't add `console.log` and guess.

## Gotchas

- **`useNativeDriver` cannot animate `backgroundColor`, `borderColor`,
  `width`, `height`, `top`, `left`, `flex`** — those must stay on the JS
  thread with `Animated`, which is why Reanimated (UI thread) is preferred.
- **A `useEffect` that sets state on every render**, or a store subscription
  that re-renders the whole screen, is the classic "list flickers/scrolls jump"
  cause. Keep the data flowing one way and stable.
- **`console.log` in RN is expensive in release too** on some platforms and
  always costs a bridge/console hop in dev; strip logs before shipping.
- **FlatList `windowSize` too high keeps too many rows mounted** (memory); too
  low and fast scroll shows blanks. Tune with `removeClippedSubviews` off
  first, then adjust.
- **`removeClippedSubviews` can cause blank cells on fast scroll / iOS edge
  cases.** It's Android-oriented; test on iOS before shipping it.
- **The bridge is not your bottleneck under the New Architecture**, but a
  legacy module or a `setNativeProps` in a loop can still block. Look at the
  UI-thread time, not just JS time.
- **`setNativeProps`/`setNativeProps` in a loop** bypasses React and can
  conflict with re-renders; prefer Reanimated shared values.
- **Hermes makes JS faster but the main thread can still be saturated by React
  reconciliation**; faster JS ≠ fewer frames if you're re-rendering everything.
- **Dev-mode double rendering** (StrictMode / the dev bundle) inflates frame
  counts; never tune on the dev FPS number.
- **Sorting/filtering inside `renderItem` or a `useMemo` with wrong deps** is a
  common "why is the list slow" that has nothing to do with the list.
- **A single giant component** (one screen, 2000 lines) is hard to memoise;
  split into memoised leaves so a re-render doesn't re-reconcile the whole tree.

## Review checklist

- [ ] Hermes + New Architecture on (RN 0.76+ default; confirm the app config).
- [ ] Long lists are `FlatList`/`FlashList`, never mapped `ScrollView`s.
- [ ] `renderItem` is a stable, memoised component with primitive/stable props
      and a `keyExtractor` from the model id.
- [ ] Animations/gestures on the UI thread (Reanimated/native driver), on
      transforms.
- [ ] No inline object/array/function props or style objects in hot paths;
      `StyleSheet.create` / `useMemo` where identity matters.
- [ ] Every `Image` has explicit dimensions and is appropriately sized.
- [ ] No heavy compute or `setState` in render; non-visual work deferred with
      `InteractionManager`.
- [ ] Profiled on a release build on a mid-range device, not guessed from dev.

## Files

- `references/perf-recipes.md` — copy-paste patterns for list tuning, memoised
  rows, Reanimated setup, and a profiling decision table.

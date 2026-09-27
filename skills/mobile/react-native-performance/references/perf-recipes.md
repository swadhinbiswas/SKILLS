# RN performance recipes and profiling reference

Load this when you have a jank or slowness symptom and need the concrete fix or
the tool that confirms it.

## Symptom → probable cause → confirm → fix

| Symptom | Probable cause | Confirm with | Fix |
|---|---|---|---|
| List scrolls stutter, rows blank then pop | ScrollView.map, or unsized images, or re-rendering rows | Perf Monitor FPS; flash a `console.log` in `renderItem` | Virtualised list; explicit image sizes; `expo-image`/FastImage cache; stable `renderItem` |
| A row re-renders while scrolling even with `memo` | New object/function prop each parent render | React DevTools "why did this render" | Stabilise props; `useCallback`; pass ids not closures |
| Drag/gesture drops frames | Animation on JS thread | Perf Monitor: JS frame time high | Reanimated + Gesture Handler (UI thread) |
| Colour/layout animation janky | `Animated` without native driver on `width`/`backgroundColor` | Same | Reanimated; animate transform/opacity |
| App feels heavy at startup | Big bundle parsing; Hermes off; too much eager init | Bundle size; Time Profiler | Hermes; lazy requires; defer init; smaller bundle |
| Frame drops while typing in a search bar | Search text state re-renders the whole list | DevTools Profiler: list re-renders on keystroke | Keep search text local to the input; memoise the filtered list |
| Memory climbs, app killed on long scroll | Too many large bitmaps mounted; `windowSize` too high | Instruments (Allocations) / Android `dumpsys meminfo` | Lower `windowSize`; smaller source images; `expo-image` cache policy; recycle |
| One specific screen is slow under Fabric | A non-Fabric native dependency on that screen | Compare that screen with a pure-JS one; check the dep's Fabric support | Upgrade/remove the dep or use interop carefully |

## FlashList notes (Shopify)

- v2 requires the **New Architecture** (and a recent RN); v1 works on both.
- It auto-sizes items, so the classic "my list is empty / wrong height" is
  mostly gone — but it still wants a **stable, unique `keyExtractor`** and
  reasonably sized images.
- It recycles cells aggressively, so **cell state must live outside the cell**
  (a store keyed by id, not `useState` inside a recycled row) or it will show
  the wrong row's state.
- Trade-off vs `FlatList`: FlashList is faster on large/heterogeneous lists but
  the ecosystem (debuggers, test utils) assumes `FlatList`. Migrate the
  measured hot lists, not everything.

## Reanimated setup and versions

- **Reanimated 3**: needs `react-native-reanimated/plugin` as the **last**
  entry in `babel.config.js` `plugins`. Works on both architectures.
- **Reanimated 4**: **New Architecture only** (Fabric). The plugin is still
  required. If you're on the Old Architecture, stay on v3.
- **Reanimated 4 + RN**: confirm the RN version in Reanimated's compatibility
  table for your RN — the supported matrix is explicit and mismatches cause
  worklet build/runtime errors, not warnings.
- **Expo**: `npx expo install react-native-reanimated react-native-gesture-handler`
  picks versions matched to the SDK. Don't `npm i` these by hand — mismatched
  versions are the #1 source of "worklets broken after upgrade".
- **New Architecture became the default in RN 0.76 / Expo SDK 52.** Later
  SDKs continue from there, but check the specific SDK's release notes rather
  than assuming a version number — confirm the RN version your SDK pins before
  you pick a Reanimated major (v4 is New-Architecture-only).

## Profiling decision table

| Question | Tool | What to look for |
|---|---|---|
| Which component re-rendered and why? | React DevTools Profiler | Commit duration; double-tap a component for "why did this render" (which prop/hook) |
| Is the JS thread or the UI thread the bottleneck? | Perf Monitor (dev) / OS profilers | JS frame time vs UI frame time; a high JS bar with a fine UI bar = reduce render work; the reverse = native view/animation cost |
| What is the FPS on device? | In-app FPS overlay (dev menu) / `adb shell dumpsys gfxinfo` framestats | Sustained below ~55–60 = dropped frames |
| Where is JS CPU time going? | Android: Perfetto/`simpleperf`; iOS: Instruments Time Profiler | Hot functions, usually rendering or a large sort/parse |
| What is allocating? | iOS: Instruments Allocations; Android Studio memory profiler | Bitmap buffers, JS object churn |
| Is the native layer the cost? | iOS Instruments; Android `dumpsys gfxinfo`/`atrace` | Fabric view mount, image decode |
| What does a real release build do? | `npx react-native run-android --mode release` (or Expo `eas build --profile`) | Dev numbers are not representative |

## Profiling discipline

- Profile a **release** build. Dev builds and the in-app dev menu overhead
  inflate JS time and the FPS number.
- Test a **mid/low device** (a 2019–2021 Android), not a flagship simulator;
  most users are not on your machine.
- Reproduce the *specific* interaction (a fling, a long list, a drag) while
  recording, not just an idle load.
- One change at a time, re-measure. Bundle-size wins and render wins compound
  but you can't attribute them if you do all at once.

## Anti-patterns to grep for

- `ScrollView` + `.map()` over a collection that can exceed ~10–20 items.
- `renderItem={(…) => (<Row … onPress={() => x(item.id)} />)}` — unstable.
- `style={{ … }}` in a mapped/re-rendering row.
- `useEffect(() => setState(something), [objectOrFunction])`.
- Animating `width`/`height`/`top`/`left` with `Animated` (no native driver).
- `console.log` left in a hot path.
- `setState` in a scroll handler on every event (throttle to
  `scrollEventThrottle` and only for a progress bar).

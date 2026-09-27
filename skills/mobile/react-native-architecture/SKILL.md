---
name: react-native-architecture
description: Structure a real React Native app that survives growth - navigation (native stack vs tabs, deep linking), state at scale, data fetching and caching, the native module boundary, the Expo SDK / RN upgrade treadmill, and a testing strategy that matches the platform. Use when starting or scaling an RN codebase, when choosing a navigator or state library, when deep links or universal links break, when planning an upgrade, or when reviewing app structure. Triggers on "react navigation", "expo-router", "deep link", "universal link", "app architecture", "folder structure", "state management RN", "zustand vs redux", "MMKV", "upgrade to SDK 52", "new architecture", "testing React Native", "detox", "monorepo".
compatibility: React Native 0.73+ / Expo SDK 50+ conventions; Expo SDK 52 = RN 0.76 (New Architecture default). Version caveats inline.
metadata:
  version: "1.0"
---

# React Native Architecture

The decisions that are expensive to reverse: where screens live, how they
navigate, where state lives, where data is cached, and how the native boundary
is managed. Get these right early; the upgrade treadmill is much easier from a
clean structure.

## Project structure

Feature-first, not type-first. A `types/` folder for app-wide TypeScript and
everything else organised by feature:

```
src/
  app/                      # navigation root, providers, deep-link config
    navigation.tsx
    linking.ts
  features/                 # one folder per feature, self-contained
    auth/
      screens/LoginScreen.tsx
      hooks/useAuth.ts
      api.ts
      store.ts              # only if this feature has real client state
    orders/
      screens/OrderList.tsx  OrderDetail.tsx
      components/
      api.ts                 # server calls (react-query hooks)
      model.ts               # types, zod schemas
  shared/                   # cross-feature
    ui/                     # Button, Input, List (design system)
    lib/                    # api client, storage, date, analytics
    store/                  # global client stores (session, theme)
  lib/
assets/
```

Rules:

- **A feature owns its screens, its API calls, and its types.** If editing an
  order screen requires editing three top-level folders, the boundaries are
  wrong.
- **`shared/` is genuinely shared**; a component used by one feature stays in
  that feature until a second feature needs it.
- **Barrel files re-exporting everything** cause slow Metro rebuilds and
  circular-import bugs in RN. Prefer direct file imports within a feature; a
  single feature-level `index.ts` is fine.
- **`api.ts` per feature** keeps the server boundary explicit; a
  `services/` dumping ground does not.

## Navigation

Two current options:

| | React Navigation | Expo Router |
|---|---|---|
| Model | Imperative/ declarative in JS | File-system based on `expo-router` |
| Setup | Manual, explicit | Convention; works out of the box in an Expo template |
| Deep links | Manual `linking` config | Filesystem = URLs |
| Best for | Bare RN, dynamic/multi-stack flows, library-agnostic | Expo apps, web parity, teams that like routing-as-files |
| Caveats | You configure everything | Ties you to Expo; a `router`-heavy codebase is less portable |

Pick one. For **Expo** new apps, `expo-router` is the smooth default; for
**bare RN** or a library you want to keep portable, React Navigation is the
safer base.

**Stack vs tabs is not either/or**: tabs are a navigator *inside* a root stack.
A tab that navigates to a detail screen should push onto that tab's stack, so
the back button stays sane and the tab bar doesn't disappear.

```tsx
// React Navigation shape
<NavigationContainer>
  <RootStack>
    <RootStack.Screen name="Tabs" component={Tabs} />
    <RootStack.Screen name="OrderDetail" component={OrderDetail} />
  </RootStack>
</NavigationContainer>
```

**Native stack** (`@react-navigation/native-stack`) is the default: it uses the
platform's real `UINavigationController`/`Fragment` transitions, so it is
faster and feels native. The JS `stack` is for when you need a custom
transition. Use native stack for normal app navigation.

- **Screen options belong in the navigator**, not sprinkled in each screen
  (`headerShown`, `title`, animations) so behaviour is consistent.
- **Modal presentation** (`presentation: 'modal'`, `'containedModal'`) for
  flows that are visually a modal; gives the platform-correct swipe-to-dismiss.
- **Keep navigation state out of your store.** Use the navigator's
  `navigation.navigate`/`useNavigation`; putting the current route in a global
  store duplicates it and fights transitions.

## Deep links / universal links

A deep link is `<scheme>://path` (custom) or `https://yourdomain/path` (universal
link on iOS, app link on Android). Both need three pieces to align: the
navigator's route names, the OS registration, and the backend/website route.

```tsx
// React Navigation linking config
const linking = {
  prefixes: ['myapp://', 'https://myapp.example.com'],
  config: {
    screens: {
      Tabs: {
        screens: { Orders: 'orders', Settings: 'settings' },
      },
      OrderDetail: 'orders/:orderId',
    },
  },
};
// navigation.navigate('OrderDetail', { orderId: 'abc' })
```

Expo Router handles this from the filesystem, but you still must configure the
**OS side**:

- **iOS Universal Links**: host `apple-app-site-association` on the domain
  (`/.well-known/apple-app-site-association` or at the root) with the app's
  `teamId` + `bundleId`, and add the `associatedDomains` entitlement
  (`applinks:myapp.example.com`) — an Expo config plugin. Expo Go won't test
  this; needs a build.
- **Android App Links**: host `/.well-known/assetlinks.json` with
  `package_name` + SHA-256 signing fingerprints; add `intentFilters` — an Expo
  config plugin. If you use EAS, `expo-credentials` can supply the
  fingerprint.
- **iOS custom scheme**: set `scheme` in app config; a build is needed for it to
  register.

**Test deep links on a build, not Expo Go.** From a device:
```bash
# iOS simulator
xcrun simctl openurl booted "myapp://orders/abc"
# Android emulator (adb shell am start with the VIEW intent)
adb shell am start -a android.intent.action.VIEW -d "myapp://orders/abc" com.me.myapp
# Universal link on a real device / iOS simulator
xcrun simctl openurl booted "https://myapp.example.com/orders/abc"
```

If a deep link opens the app but not the right screen, the OS registration is
fine and the problem is the `linking` config / route name. If it doesn't open the
app at all, it's the OS registration (associated domains, assetlinks, scheme,
or the build).

## State at scale

The same ladder as the web, adapted. See the `react-state-management` skill for
the full decision logic; the RN-specific defaults:

- **Server state**: **TanStack Query** (or RTK Query). Caching, dedupe,
  invalidation, retry, offline-stale. This is the answer for anything a fetch
  owns — do not hand-roll a `useEffect` + `useState` fetch in RN.
- **Persistence**:
  - `AsyncStorage` (async, unencrypted, simple key/value) — fine for tokens and
    small prefs; it's the lowest common denominator.
  - **`react-native-mmkv`** (JSI, synchronous, encrypted) — the current default
    for anything read on first render or often (auth token, feature flags,
    caches). Sync reads let you gate the app on a hydrated value without a
    loading flash.
  - **SQLite** (`expo-sqlite` / `react-native-quick-sqlite`) for structured,
    queryable, or offline-first data. See the `mobile-offline-sync` skill.
  - Never store a secret that matters without assuming the device is
    compromised; a rooted/jailbroken device can read storage. Encrypt
    (MMKV/Keychain) and prefer short-lived tokens.
- **Client/global state**: **Zustand** with `persist` (MMKV storage) for session,
  theme, onboarding. RTK if the write logic is genuinely complex and you want
  the devtools. Do not keep server data in a persisted store by hand.
- **Forms**: React Hook Form + Zod (see the `react-state-management` skill).
- **Keep the auth/session store hydrated before render** with an MMKV-backed
  store or a splash gate; otherwise the app flashes a logged-out state.

## Data fetching and caching

- Query keys include every input (see the `react-state-management` skill).
- **Configure for mobile**: `staleTime` slightly longer than web (users return
  to a screen, not a new page), `retry` with backoff, and **persist the query
  cache** to MMKV/SQLite so the app shows last-known data on a cold start
  offline.
- **`focusManager`/`onlineManager`**: wire `AppState` so queries refetch on
  return-to-foreground (RN has no `visibilitychange`; `onlineManager` +
  `focusManager` from TanStack Query, or `expo-network` for connectivity).
- **Background/foreground**: don't fetch while backgrounded; use
  `AppState` to pause polling and resume+refetch on active.

## The native module boundary

- **Prefer a library over a custom native module**; prefer a **config plugin**
  over hand-edited native. See the `expo-app-lifecycle` skill.
- **A custom native module** (TurboModule) is the escape hatch. Under the New
  Architecture it must be a **TurboModule** (typed spec, `TurboModuleRegistry`),
  not a legacy `NativeModules` bridge module (legacy modules work via interop
  but are the path most likely to be the old bridge). Define the spec with
  `codegenConfig` in `package.json` and implement the native side.
- **A native view** (Fabric component) is a bigger commitment; needs codegen
  and Fabric-native code. Almost always use an existing community component
  first.
- **Boundary rule**: the JS↔native contract is serialised. Don't pass rich JS
  objects/closures to native; pass primitives/JSON. Async, not sync, unless the
  module is designed for it (sync native calls block the JS thread).

## The upgrade treadmill

- **Expo SDKs track React Native.** Each SDK pins an RN version and a set of
  library versions. Staying on an SDK means you get a known-good matrix.
- **`npx expo install <pkg>`** installs the version matched to your SDK (from
  the Expo SDK's bundled native modules). Do **not** `npm i` a library with
  native code and hope — a version off the matrix is the #1 source of native
  build breakage.
- **`npx expo-doctor`** checks version alignment, config validity, and
  deprecated APIs. Run it before and after every upgrade.
- **Upgrade order**: bump Expo SDK (one at a time — SDKs are not meant to be
  skipped across many versions in one go), run `expo-doctor` +
  `npx expo install --fix`, rebuild, fix native breakages, then bump RN if
  you're on bare RN (follow RN's own upgrade helper / the Expo upgrade
  path).
- **New Architecture** (Fabric + TurboModules + JSI): default from RN 0.76
  (Expo SDK 52). Libraries with native code must be Fabric/bridgeless-compatible;
  an old native library can pin you or be the jank source under Fabric. Prefer
  current versions.
- **Hermes** is the default engine (RN 0.76+ on both platforms); don't switch to
  JSC.
- **The breakages to expect on an SDK upgrade**: config-plugin API changes
  (plugins get a new config object), removed/deprecated APIs (old `expo-updates`
  fields, `SafeAreaView` from react-native is deprecated → `react-native-safe-area-context`),
  Android/iOS target SDK bumps (new store requirements), and native module
  interface changes. Read the SDK's release notes; they list the breaking
  changes per SDK.

## Testing strategy for RN

Match the test to the risk, and don't try to unit-test the platform.

| Layer | Tool | What to test | Don't |
|---|---|---|---|
| **Unit** | Jest (`jest-expo` preset) | Pure logic, reducers, zod schemas, hooks with `@testing-library/react-native` and mocked query clients | Platform components |
| **Component** | `@testing-library/react-native` (`render`, `fireEvent`, `screen.getByRole`) | Behaviour: does it render, respond, show the right text for a state | Snapshot-everything (brittle); internals |
| **E2E** | **Detox** (grey-box, native) or **Maestro** (black-box, YAML, less code) | The critical journeys: login, checkout, onboarding, deep links | Everything (slow, flaky) |
| **Native** | Manual on device + a small Maestro/Detox smoke set | Anything Detox can't easily reach (camera, push, permissions) | Trying to automate real device push/perms early |

- **`jest-expo`** is the Jest preset (handles RN transform, mocks). Mock
  native modules and Expo modules (`expo-secure-store`, `expo-notifications`).
  Keep the **majority of logic outside components** (services, reducers,
  selectors) so it's unit-testable without a renderer.
- **Maestro** is a strong "just get E2E running" choice: a YAML flow, real
  device, no native test harness in the app. Detox is better when you need
  white-box control and sync, but adds a native build dependency to CI.
- **E2E flakiness** is the enemy of a useful suite. Keep 3–5 rock-solid
  journeys, make selectors stable (use `testID` sparingly and
  `accessibilityLabel`/text where possible), and never `sleep` — wait for a real
  condition.
- **Mock the network at the boundary**, not the module: stub `fetch`/your API
  client so the whole app above it runs for real. For E2E, a mock server (or
  `maestro` + a staging backend) keeps runs deterministic.

## Gotchas

- **Metro + a monorepo** (yarn/pnpm workspaces) needs `watchFolders` and
  `nodeModulesPaths` in `metro.config.js`; forgetting them is why a local
  library "doesn't update".
- **Native module linking:** with autolinking, a library's native code links if
  it's in `dependencies` (not `devDependencies`) and has a podspec/`build.gradle`.
  A library in `devDependencies` won't link.
- **iOS pods:** `npx pod-install` after adding a native iOS dependency;
  `cd ios && pod install`. Forgetting it is a top "module not found" cause.
- **Babel `module:@react-native/babel-preset`** (or `babel-preset-expo`) must
  be configured; custom babel plugins (Reanimated, etc.) have ordering
  requirements.
- **A JS-only library upgrade can still require a rebuild** if it has any
  native code (even a small one) — check for a `*.podspec`/`android/` folder.
- **Don't fight the navigator**: nested navigators, custom navigators, and
  re-implementing back handling cause subtle bugs. Use the library's
  primitives.
- **`react-native-safe-area-context`** (not the deprecated RN `SafeAreaView`)
  for notches; wrap the app in `SafeAreaProvider`.
- **Status bar / splash / fonts** are app-shell concerns; set them once at the
  root, not per screen.
- **Deep links and auth**: a cold-start deep link arrives before the session is
  hydrated. Queue the intent and replay it after auth resolves, or the app
  lands on a logged-out screen and loses the link.

## Review checklist

- [ ] Feature-first structure; a feature owns its screens/api/types.
- [ ] One navigation system; native stack; modal flows use modal presentation.
- [ ] Deep links work from a **build** (universal links verified on device) and
      survive a cold start.
- [ ] Server data in a query library with persistence; global client state in
      one small persisted store.
- [ ] Native libraries added with `expo install` / within the SDK matrix;
      `expo-doctor` clean.
- [ ] The custom native module (if any) is a TurboModule, not a legacy bridge
      module.
- [ ] Tests: most logic unit-tested, a few E2E journeys, no `sleep`.

## Files

- `references/navigation-and-upgrades.md` — navigator config detail, deep-link
  OS setup, and a pre-upgrade checklist.

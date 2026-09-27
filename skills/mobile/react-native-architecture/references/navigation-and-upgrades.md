# Navigation and upgrades reference

Load this when wiring deep links, when a navigation behaviour is wrong, or when
planning an Expo SDK / React Native upgrade.

## Navigator topology (React Navigation)

```
NavigationContainer (linking, theme)
└── RootStack                       ← modal flows, auth gate, always-mount screens
    ├── (no auth) AuthStack: SignIn, SignUp
    └── (authed) Tabs
        ├── OrdersStack: Orders, OrderDetail   ← per-tab stack
        ├── ProfileStack: Profile, Settings
        └── ...
```

- **A tab navigates to a detail → push on that tab's stack**, not the root.
  Otherwise the tab bar disappears and back goes somewhere unexpected.
- **Auth gate**: render `AuthStack` or the app stack based on session; do it in
  a top-level component that watches the auth store. On logout, reset the
  navigator state (`navigation.reset`) or you'll leave a logged-in screen on the
  back stack.
- **Screen options at the navigator**: `options={{ title, headerShown,
  presentation }}` per `Screen`, with shared defaults via `defaultScreenOptions`.
- **Native stack** (`@react-navigation/native-stack`): platform transitions,
  better perf. Use `presentation: 'modal'` / `'containedModal'` / `'transparentModal'`
  for modal-ish flows; `animation: 'slide_from_bottom'` etc. for a custom push.
- **Deep-link into a tab screen**: the `linking.config` must nest it under the
  tab's screen name (see below), otherwise the URL resolves to the tab but not
  the inner screen.

## Deep-link OS setup (both platforms required)

Universal/App links need: (1) a file on your domain, (2) an OS entitlement /
intent filter, (3) a `linking` route config, (4) a build (not Expo Go).

### iOS Universal Links

1. Host `https://<domain>/.well-known/apple-app-site-association` (no
   extension, `Content-Type: application/json`; the file at the root also
   works and avoids CDN content-type issues):
   ```json
   {
     "applinks": {
       "details": [
         { "appIDs": ["TEAMID.com.me.myapp"], "components": [
             { "/": "/orders/*", "comment": "Orders" } ] }
       ]
     }
   }
   ```
2. Add the entitlement `com.apple.developer.associated-domains` with
   `applinks:<domain>` — an Expo config plugin (e.g. a plugin that sets
   `ios.associatedDomains`).
3. Test on a real device (the simulator is unreliable for AASA caching) or the
   simulator with `xcrun simctl openurl`. Apple caches the AASA per CDN; a
   misconfiguration can take a while to clear.

### Android App Links

1. Host `https://<domain>/.well-known/assetlinks.json`:
   ```json
   [{
     "relation": ["delegate_permission/common.handle_all_urls"],
     "target": {
       "namespace": "android_app",
       "package_name": "com.me.myapp",
       "sha256_cert_fingerprints": ["AA:BB:…"]
     }
   }]
   ```
2. Add an intent filter for the https host (an Expo config plugin; Expo can
   generate the SHA-256 fingerprints via `eas credentials` / `expo-credentials`).
3. Verify with `adb shell pm get-app-links com.me.myapp` (should show the host
   as `verified`).
4. **Multiple signing fingerprints**: include the debug, upload (EAS), and Play
   App Signing fingerprints, or links break for testers vs store builds.

### Custom scheme

Set `scheme` in app config. Works without a domain, but is hijackable by other
apps (any app can claim the scheme) — prefer universal links for anything
sensitive. Requires a build to register with the OS.

## Pre-upgrade checklist (Expo SDK / RN)

Run before every upgrade:

- [ ] `npx expo-doctor` is clean (or its findings are understood).
- [ ] All Expo-managed native deps are on SDK-matched versions:
      `npx expo install --check`.
- [ ] No `package-lock` drift: commit the lockfile; the cloud build uses it.
- [ ] Custom config plugins reviewed against the new SDK's plugin API
      (`@expo/config-plugins`).
- [ ] `eas.json` EAS CLI `version` major supports the new SDK.
- [ ] App Store Connect / Play target-SDK requirements for the new SDK are
      met (SDKs raise target API requirements).
- [ ] You can build and run the **current** version first, so a failure after
      the bump is attributable to the upgrade.

Order of operations (Expo):

```bash
npx expo install expo@^52        # or the target SDK; one SDK at a time
npx expo install --fix           # align all native deps to the new SDK
npx expo-doctor                  # re-check
npx pod-install                  # if ios/ exists
eas build --profile preview --platform ios   # verify on a build before store
```

Bare RN (not Expo): use RN's Upgrade Helper for the native diffs, and update
`react-native`, `react`, and the core libraries in lockstep; a version skew
between `react` and `react-native` (or a library's peer range) breaks the build
subtly.

## Version-relationship cheat sheet (verify against release notes)

- Expo SDK 50 → RN 0.73
- Expo SDK 51 → RN 0.74
- Expo SDK 52 → RN 0.76 (New Architecture **default**, Hermes on both
  platforms)

For any other SDK, **look the pairing up in the Expo SDK release notes rather
than assuming** — each SDK pins a specific RN version, and guessing it is how
you end up with a library on a version your SDK's native matrix never tested.
The Expo docs' "Expo SDK" reference page lists current pairings; older SDKs
have their pairing stated in their own release blog post.

The two facts worth remembering because they change behaviour rather than just
version numbers: **New Architecture (Fabric + TurboModules) and Hermes became
the defaults in SDK 52 / RN 0.76.** A library's Fabric compatibility and the
RN version it supports are what actually determine whether an upgrade is smooth.

## Testability seams

- Keep business logic in plain functions/hooks (`useOrders`, `useCart`) that
  take an injected API client, so unit tests don't need a renderer.
- Wrap network and storage behind interfaces; in tests provide fakes. Don't
  mock `fetch` deep inside components.
- For E2E, a stable `testID` on a few key elements (login button, list, submit)
  and stable `accessibilityLabel`s make selectors reliable across releases.
- Avoid `sleep`/`waitFor` on arbitrary timers; wait for a real UI condition.

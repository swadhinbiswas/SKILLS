# EAS build and update reference

Load this when a build fails, when you are choosing OTA vs a store release, or
when writing a config plugin.

## OTA vs store release — decision checklist

Ask: **would `android/` or `ios/` (or the native binary) change?**

| Change | OTA | Store |
|---|---|---|
| Edit a React component / screen / util | Yes | (or OTA) |
| Add an asset (image/font) not baked into native | Yes | (or OTA) |
| Change a runtime `extra` value pushed with the update | Yes | (or OTA) |
| Change `app.json` values read at runtime only | Yes (if shipped in the update payload) | (or OTA) |
| Add/remove a native module or dependency with native code | **No** | Yes |
| Add a permission / `Info.plist` / `AndroidManifest` entry | **No** | Yes |
| Change icon, splash, adaptive icon | **No** | Yes |
| Change bundle id / package name / scheme | **No** | Yes (and it's effectively permanent) |
| Change a `plugins` config | **No** | Yes |
| Bump Expo SDK / React Native | **No** | Yes |
| Bump the Android `versionCode` / iOS `buildNumber` | **No** | Yes |
| Change `runtimeVersion` policy | **No** | Yes |
| Change `expo-build-properties` (minSdk, compileSdk, iOS flags) | **No** | Yes |
| Add a config plugin | **No** | Yes |
| Add push notifications / entitlements / App Extension | **No** | Yes |

The safe mental model: **the OTA update replaces your JavaScript bundle and
assets. Everything native is frozen at build time.**

## Build failure triage (from real EAS logs)

| Log fragment | Cause | Action |
|---|---|---|
| `Failed to resolve all files for configuration ':app'` / `Could not find com.facebook.react:react-android` | Gradle/React Native version mismatch (often a hoisting or a library expecting a different RN) | `npx expo-doctor`; align versions with `npx expo install --fix`; clear caches (`eas build --clear-cache`) |
| `A problem occurred configuring project ':app'` with an AndroidX / `compileSdk` note | A library needs a newer `compileSdk`/`targetSdk` or AndroidX flags | Add/raise `expo-build-properties`; upgrade the library to a version compatible with your SDK |
| `Installed (X) is lower than the minimum (Y) required` | Library needs a higher `minSdkVersion` (e.g. a recent AGP/AndroidX) | Raise `minSdkVersion` in `expo-build-properties` **and** confirm Play's target requirements, or pin an older library |
| iOS: `No profiles for 'com.me.myapp' were found` / `doesn't include signing certificate` | Missing or mismatched distribution cert/profile | `eas credentials` → manage iOS App Store credentials → reset certificates/profiles; re-run |
| iOS: `Provisioning profile ... does not include the ... entitlement` | Capability/entitlement missing | Add the entitlement via a plugin (`expo-build-properties` / a custom plugin) or in the Apple Developer portal |
| Android: `keystore file not found` / `storeFile ... does not exist` | Keystore credentials lost or misconfigured | `eas credentials` → recreate; if it's a **local** (non-EAS) build, check `credentials.json` and `android/gradle.properties` |
| `PluginError: Package "X" requires a config plugin` | A native library needs a plugin | Install the library's Expo config plugin and add it to `plugins` in app config |
| `Build failed because the bundle is out of date` / metro errors | JS build error in the EAS environment only | Run `npx expo export --platform all` (or a local export) locally to surface it — env-var or undeclared-asset bugs only show in cloud builds |
| `Cannot resolve '../assets/icon.png'` | Asset path wrong relative to config | Fix the path in app config |
| Out of memory during Gradle / Xcode | Low build resources | Raise `eas.json` `build.resources` (e.g. `{ "cpu": 4, "memory": 8GB }`), clean cache |
| `node_modules` differs between local and EAS | EAS installs from lockfile; your local `node_modules` is stale or you have uncommitted lock changes | Commit `package-lock.json`/`yarn.lock`/`bun.lockb`; delete `node_modules` + reinstall |

## Config-plugin patterns

A config plugin is a JS module that, at prebuild, mutates the generated native
project. The common cases:

```js
// plugins/my-plugin.js — a plugin is a function receiving the Expo config.
const { withAndroidManifest, withInfoPlist, withXcodeProject } = require('@expo/config-plugins');

module.exports = function withMyPlugin(config) {
  // Add an Android permission
  config = withAndroidManifest(config, (cfg) => {
    const perms = cfg.modResults.manifest[0]['uses-permission'] ??= [];
    if (!perms.some((p) => p.$['android:name'] === 'android.permission.CAMERA')) {
      perms.push({ $: { 'android:name': 'android.permission.CAMERA' } });
    }
    return cfg;
  });
  // Add an iOS Info.plist key
  config = withInfoPlist(config, (cfg) => {
    cfg.modResults.NSFooUsageDescription = 'Explain why you need Foo.';
    return cfg;
  });
  return config;
};
```

- Plugins run in `app.json` `plugins` (or `app.config.js`). They only take
  effect on a **build/prebuild**, never on an OTA.
- A plugin changing `Info.plist`/manifest requires a new store build.
- If you find yourself writing a lot of a plugin, check whether a community
  plugin already does it (`expo-*` plugins cover most common needs).
- Adding a **local** plugin: reference it by relative path in `plugins`
  (`"./plugins/withFoo"`). Local plugins can also expose the "props" pattern:
  `["expo-build-properties", { … }]` passes config to a plugin.

## Runtime config that can change without a rebuild

- `Constants.expoConfig.extra` — read from the app config. Public (in bundle).
- `expo-constants` `Constants.expoConfig?.updates?.runtimeVersion`.
- `app.config.js` can read `process.env` at **build** time and place the result
  in `extra` — but that value is then fixed at build and requires an
  OTA/rebuild to change.
- For values that must change frequently per environment, use a remote config
  endpoint fetched at runtime (it *is* public, so it holds no secrets), or
  separate builds per environment.

## Debugging "works locally, fails in EAS"

- The cloud build does a fresh `npm install` from your lockfile. Uncommitted
  lockfile changes or a dirty `node_modules` locally = different trees.
- `NODE_ENV=production`, no `.env` (unless set in EAS), minification on, and
  `NODE_ENV`-gated code paths run differently.
- Locally reproduce with `npx expo export --platform all` (a production export)
  or `eas build --local`. If it reproduces, the bug is in your code, not the
  cloud.
- Declared assets: a file referenced only by a dynamic path and not in
  `assetBundlePatterns`/static require will be missing in a release bundle but
  present in dev. Use `require()` for known assets or list the pattern.
- Sourcemaps: upload them for a better production error. `EAS_BUILD` sets
  `sourcemap` output; Crashlytics/`Sentry` can consume them.

## Signing and credentials

- **iOS**: EAS creates an Apple Distribution certificate and provisioning
  profiles. Back them up with `eas credentials` (export the p12 + password).
  The App Store Connect API key is also stored in EAS.
- **Android**: EAS creates an upload keystore. **Back it up.** If you lose it
  and are not on Play App Signing's key-reset programme, you can never publish
  an update to that listing. If you enrolled in **Play App Signing**, your app
  key is separate and recoverable; the *upload* key can then be reset.
- Use `eas secret:create` for build-time secrets (keystore passwords, third-party
  API tokens used during the build), not for runtime client secrets (which
  belong on your server).
- For local/native builds, credentials live in `android/gradle.properties` and
  the Xcode team; keep them out of git (gitignore) and prefer EAS-managed
  credentials for CI.

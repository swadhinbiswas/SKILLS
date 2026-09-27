---
name: expo-app-lifecycle
description: Build, configure, and ship an Expo app - project structure, app.json vs app.config.js, environment variables and why EXPO_PUBLIC_ ends up in the client bundle, EAS Build and EAS Submit, OTA updates versus store updates (and the "only the JS bundle can change" rule), prebuild tradeoffs, and native config. Use when starting an Expo project, when configuring app name/icons/splash/permissions, when setting up EAS, when deciding between an OTA update and a store release, or when a build fails. Triggers on "expo", "eas build", "eas update", "expo prebuild", "app.json", "app.config.js", "EXPO_PUBLIC_", "expo-env", "runtime version", "expo-updates", "over the air update", "TestFlight", "build failed".
compatibility: Expo SDK 52+ (RN 0.76, New Architecture by default) and EAS Build/Submit. Some notes are version-sensitive - verify against your SDK's release notes.
metadata:
  version: "1.0"
---

# Expo App Lifecycle

Expo is a workflow, not just a library: you write JS/TS, and it either runs in
Expo Go, gets bundled into a native binary by EAS Build, or gets a JavaScript
update pushed to an installed app over the air. Most Expo pain is a
misunderstanding of which of those three is happening right now.

## The three run modes

| Mode | What it is | Use for |
|---|---|---|
| **Expo Go** | Prebuilt client that loads your project over the network | Rapid development; **limited native modules**; not for testing your production native config |
| **Development build** (`expo-dev-client`) | Your own app with a dev client; loads your JS; can include custom native modules | Developing with native modules; mirrors your production native side |
| **Production build** (EAS Build → store/Play) | Your app with your JS bundled and your native config baked in | What users install |

**Native config only takes effect in a build.** Changing `app.json`'s
permissions, icons, splash, or plugins does nothing in Expo Go or a plain dev
build until you rebuild the binary. This is the single most common Expo
confusion.

## Project structure

```
myapp/
  app/                    # expo-router (file-based routing) OR a src/ dir with your own setup
    _layout.tsx           # root layout / providers
    index.tsx
  src/
    components/
    lib/                  # api client, storage
    store/
  assets/                 # icon.png, splash.png, adaptive-icon, fonts
  app.json                # static config
  app.config.js           # dynamic config (optional; merges over app.json)
  eas.json                # EAS build/submit profiles
  package.json
```

- **Routing**: `expo-router` (file-based, the current default for new apps) or
  React Navigation. See the `react-native-architecture` skill for routing and
  deep links.
- **Config is layered**: `app.json` (static) merged with `app.config.js`
  (dynamic, can read env at build time). `app.config.js` exports an object; if
  it needs to compute something, it can read `process.env`.
- **`eas.json`** defines build profiles (`development`, `preview`, `production`),
  submit profiles, and update channels. Profiles control things like
  `distribution: internal` vs `store`, `channel`, and Android build type (APK vs
  AAB).

## Configuration: app.json vs app.config.js

`app.json` for anything static; `app.config.js` when the value must be computed
(env, per-profile). Keys you will actually use:

```json
{
  "expo": {
    "name": "MyApp",
    "slug": "myapp",
    "scheme": "myapp",
    "version": "1.0.0",
    "orientation": "portrait",
    "userInterfaceStyle": "automatic",
    "icon": "./assets/icon.png",
    "splash": { "image": "./assets/splash.png", "resizeMode": "contain" },
    "assetBundlePatterns": ["**/*"],
    "ios": { "bundleIdentifier": "com.me.myapp", "supportsTablet": true,
             "infoPlist": { "ITSAppUsesNonExemptEncryption": false } },
    "android": { "package": "com.me.myapp",
                 "adaptiveIcon": { "foregroundImage": "./assets/adaptive-icon.png",
                                   "backgroundColor": "#ffffff" } },
    "plugins": [
      "expo-router",
      ["expo-splash-screen", { "backgroundColor": "#ffffff" }],
      ["expo-build-properties", { "ios": { "usesNonExemptEncryption": false } }]
    ],
    "extra": { "apiUrl": "https://api.example.com" },
    "updates": { "url": "https://u.expo.dev/<projectId>", "runtimeVersion": { "policy": "appVersion" } }
  }
}
```

- **`version` is the user-facing version** (`1.0.0`); **`buildNumber` (iOS) /
  `versionCode` (Android)`** is the build identifier. Both must increase for a
  store release. EAS can auto-increment build numbers.
- **`updates.runtimeVersion`** is what OTA updates are keyed on. The
  `appVersion` policy ties it to `version` — bump `version` to make old OTAs
  stop applying. See the OTA section.
- **`extra`** is a place to stash config values readable at runtime via
  `expo-constants` (`Constants.expoConfig.extra`). Anything in `extra` is
  **in the app bundle** — treat it as public.

## Environment variables and the security boundary

Expo inlines env vars into the JS bundle at build time. Two classes:

| Prefix | Inlined where | Security |
|---|---|---|
| `EXPO_PUBLIC_*` | Any JS/TS file, **client bundle** | **Public.** Anyone with the app can extract it. Never a secret. |
| Everything else | Server-side / EAS build steps only (build hooks, `eas.json`, node scripts) | Keep server-side; not in the app |

```ts
// Works anywhere in client code (bundled):
const url = process.env.EXPO_PUBLIC_API_URL;
// Server/build-only env (NOT available in the client):
const token = process.env.SECRET_API_KEY;   // undefined in the app — good
```

Rules:

- **Never put a secret in an `EXPO_PUBLIC_*` var or in `app.json` `extra`.**
  The bundle is trivially unpacked (`npx react-native bundle`, or Expo's
  published bundle) and these values are readable. A Stripe *secret* key, a
  database password, or a private API key in `EXPO_PUBLIC_` is a breach.
- **Client-side keys that are still sensitive** (Stripe publishable key, a
  Firebase web config) are designed to be public *only if the backend enforces
  authorisation*. Rely on server-side checks, not on hiding a key.
- **`.env` files**: Expo auto-loads `.env` for `EXPO_PUBLIC_*` in dev; on EAS
  set them in the dashboard / `eas.json` `env` or with `eas env:create`. `.env`
  is committed unless you gitignore it — gitignore it.
- **`eas secret:create`** for build secrets (keystore passwords, API tokens);
  they are encrypted at rest in EAS and injected at build time. Use them, don't
  hardcode.

## EAS Build and EAS Submit

```bash
eas login
eas build:configure            # link the project, set up eas.json / credentials
eas build --profile development --platform android
eas build --profile preview --platform all
eas build --profile production --platform all
eas build --profile production --platform ios --auto-submit --latest      # build then submit

eas submit --profile production --platform ios
eas submit --profile production --platform android
```

- **Profiles** (`eas.json`):
  ```json
  {
    "build": {
      "development": { "developmentClient": true, "distribution": "internal" },
      "preview":      { "distribution": "internal", "android": { "buildType": "apk" } },
      "production":   { "autoIncrement": true, "channel": "production" }
    },
    "submit": { "production": {} },
    "cli": { "version": ">= 12.0.0" }   // pin the EAS CLI major
  }
  ```
  - `distribution: "internal"` → an installable artifact (APK / ad-hoc iOS) for
    testers, no store. `production`/`store` → the store build.
  - `developmentClient: true` produces an app that loads your JS from the
    dev server (needs `expo-dev-client`).
  - `autoIncrement: true` bumps `buildNumber`/`versionCode` per build.
- **Credentials**: EAS manages signing. First build prompts to create iOS
  distribution certs and an Android keystore — let it, and back up the
  credentials (`eas credentials`). Losing the Android keystore means you can
  never update that app on Play again (unless you enrolled in Play App Signing
  key reset).
- **Build failures**: read the actual EAS log; common ones are
  - `Failed to resolve package X` → a dependency whose native build needs a
    config plugin (install its Expo plugin) or a version mismatch.
  - iOS: "Provisioning profile ... doesn't include signing certificate" →
    run `eas credentials` to reset the profile/cert.
  - Android: minSdk/targetSdk mismatch with a library → pin the library
    version or add `expo-build-properties`.
  - A JS-only change should never need a native rebuild; if a build fails on
    something you thought was JS-only, you changed a plugin/config or a native
    dependency (see the OTA rule).

## OTA updates vs store updates — the rule that matters

**EAS Update / `expo-updates` can change the JavaScript (and assets and
configuration read at runtime) of an already-installed app, without a store
release. It cannot change anything native.**

What an OTA update can change:
- JS/TS bundle (all your screens, logic, styles).
- Bundled assets (images, fonts) that aren't baked into the native binary.
- `app.json` `extra` / runtime constants, if you push them with the update.
- `runtimeVersion`-matched code.

What **requires a new store build**:
- **Any native code or config**: adding/removing a native module, changing
  permissions, `Info.plist` / `AndroidManifest`, icons, splash, bundle id,
  `runtimeVersion` policy change, Android `minSdkVersion`, iOS deployment
  target, entitlements, push notification setup, `expo-build-properties`,
  any `plugin` config.
- **The dependency's native version changed** (a new version of a library with
  native code, or upgrading Expo SDK / React Native).

Rule of thumb: **if `eas build` would produce a different `android/` or `ios/`
folder, or a different `buildNumber`, it is a store release, not an OTA.**

```bash
# Publish to a branch (preview: the branch's own channel picks it up)
eas update --branch production --message "Fix checkout crash"

# Publish to whatever channel this build/profile is configured for
eas update --auto

# Revert: either re-publish the previous update, or flip --rollback in eas.json
eas update --branch production --message "Revert checkout fix" --rollback

eas channel:add production     # point a channel at a branch
eas channel:view production    # see which branch a channel serves
```

- **`--auto` reads your `eas.json`**, using the channel and rollback settings
  configured for the current profile — so it's a shortcut, not an extra flag to
  stack. Don't combine `--auto --rollback`; set `"rollback": true` in the
  `update` config, or pass `--branch` and `--rollback` explicitly as above.
- **Check what you did**: `eas update:list` shows published updates with their
  runtime version and message. Confirm the new update is live on the channel
  before you call it done.
- **`runtimeVersion` is the safety net.** The `appVersion` policy ties the
  runtime to `version`; an OTA built for runtime `1.0.0` won't apply to a user
  on `1.1.0`. Don't set `policy: "fingerprint"` (single-bundle) casually — it
  ties updates to the exact native fingerprint, so *any* native change
  invalidates OTA (often what you want for safety, but know the trade).
- **Channels and branches**: a branch is where you publish; a channel is what a
  build subscribes to (`"channel": "production"` in `eas.json`). Preview
  builds often use a `preview` channel so testers get OTAs separate from prod.
- **Rollback is a real operation**: `eas update --rollback`, or repoint the
  channel. Test the update on a real device/build before it reaches users.
- **Apple/Google allow OTA** (JavaScript is not a reviewable binary in the
  store's eyes), but do **not** use OTA to change functionality in a way that
  circumvents review, ship a broken build to everyone, or silently add
  features that need consent. A crashing OTA is a bad day; stage rollouts and
  monitor.

## Prebuild and the CNG tradeoffs

**Continuous Native Generation (CNG)**: Expo config (`app.json` + plugins)
*generates* the `android/` and `ios/` native projects. Managed workflow: you do
**not** commit them; you run `npx expo prebuild` to regenerate when native config
changes (and EAS Build runs it in the cloud).

- **Managed (CNG, no native dirs) — the default**: no Xcode/Android Studio, no
  native review in PRs, upgrades are `expo install --fix`. You can only change
  native things through config/plugins. **Use this unless you need custom native
  code.**
- **Bare / prebuild with committed `android/` and `ios/`**: you own the native
  projects. Needed for custom native modules, App Extensions (widgets, share
  extensions), advanced iOS (push entitlements, HealthKit, complex Xcode
  settings), or when you need to read/edit native code directly.

```bash
npx expo prebuild            # generate android/ and ios/ from config
npx expo prebuild --clean    # delete and regenerate (destructive to native edits!)
npx expo run:ios             # build & run locally from the native project
```

- **If you commit native dirs, you own them**: you must merge native changes on
  every SDK upgrade (`npx expo prebuild` will *conflict* with hand edits), keep
  them in sync with plugins, and you lose "just upgrade Expo" simplicity. There
  are config plugins that *do* modify native files during prebuild (that's the
  bridge), so prefer a plugin over hand-editing where one exists.
- **Adopting native dirs is one-way-ish**: once you hand-edit, `prebuild
  --clean` (and every upgrade) will fight you. Commit native changes, and prefer
  plugins + `expo prebuild` for routine config.

## Practical release flow (the default)

1. `npx expo install --fix` (align deps to the SDK) → run tests → a preview
   build → device test.
2. JS-only fix, already-released native build → `eas update --auto` (stage to
   a preview channel, monitor, then prod).
3. Anything native or a new store version → bump `version` (and let
   `autoIncrement` handle the build number) → `eas build --profile production
   --platform all` → `eas submit` → wait for review.
4. Keep secrets in EAS secrets / server; nothing sensitive in the bundle.

## Gotchas

- **Expo Go is not production.** A change that works in Expo Go can fail in a
  real build (missing native module, different SDK). Test in a development
  build before shipping.
- **Config-plugin changes need a rebuild**, never an OTA. If you "OTA'd" a
  permission change and it didn't appear, that's why.
- **`EXPO_PUBLIC_` values are baked in at build time.** Changing an
  `EXPO_PUBLIC_` var has no effect on already-installed apps without a new
  bundle — i.e. it needs an OTA update *or* a new build to reach users.
- **Secrets in `app.json` `extra` are public** (it's in the bundle). Don't.
- **A build-number/`version` collision** (two builds with the same
  `buildNumber`) is rejected by the stores; EAS `autoIncrement` prevents it.
- **EAS CLI version matters** — `eas.json` `cli.version` pins a major; a stale
  EAS CLI against a new project is a common "build config" failure. Update with
  `npm i -g eas-cli@latest` and check the pinned major.
- **`runtimeVersion` and OTA drift**: if users report the wrong JS, check
  `runtimeVersion` policy and the channel/branch a build subscribed to.
- **Renaming the `slug` after the first build** can orphan the project/URLs;
  treat the slug as permanent after the first EAS build.
- **`scheme`** is what custom-scheme deep links use; changing it breaks
  existing deep links (see the `react-native-architecture` skill for the
  linking config).

## Review checklist

- [ ] `version` bumped for a store release; build number auto-increments.
- [ ] No secret in `EXPO_PUBLIC_*` or `extra`; build secrets in `eas secret`.
- [ ] `eas.json` profiles separate dev/preview/production; prod uses
      `distribution: store` + a channel.
- [ ] `runtimeVersion` policy chosen deliberately; OTA tested on a build first.
- [ ] Native config changes go through a rebuild, not an OTA.
- [ ] `android/`/`ios/` are either absent (CNG) or intentionally owned and in
      sync with plugins.
- [ ] Android keystore / iOS distribution certs are backed up (`eas credentials`).

## Files

- `references/build-and-update.md` — EAS troubleshooting, config-plugin
  patterns, and an OTA-vs-store decision checklist.

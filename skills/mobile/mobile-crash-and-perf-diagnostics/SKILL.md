---
name: mobile-crash-and-perf-diagnostics
description: Diagnose crashes, ANRs, and jank on real mobile devices - getting symbolicated stack traces through ProGuard/R8 and dSYM obfuscation mapping files, using Crashlytics/Firebase, reading OOM and memory-pressure signals, and explaining the classic "works in dev, crashes in release". Use when a release build crashes but dev does not, when stack traces are unreadable, when a Crashlytics report needs symbolication, when the app is killed for memory, or when an ANR appears. Triggers on "symbolicated", "obfuscated stack trace", "ProGuard", "R8 mapping.txt", "dSYM", "upload symbols", "works in dev crashes in release", "ANR", "out of memory", "OOM", "Crashlytics", "Firebase Crashlytics", "unhandled exception", "native crash", "SIGABRT", "memory pressure", "jank in production".
compatibility: Firebase Crashlytics (Android + iOS), Android ProGuard/R8, Apple dSYMs, Hermes source maps. Platform notes inline.
metadata:
  version: "1.0"
---

# Mobile Crash and Performance Diagnostics

A crash report is only useful if you can read the stack. Obfuscated
`at a.b.c(SourceFile:12)` in a release build is the difference between a
two-hour fix and a two-week one. Getting symbols right is the first job.

## The mental model: which layer crashed

| Layer | What you see | Where the truth is |
|---|---|---|
| **JavaScript** | A red screen in dev, a JS exception, a Hermes `Error` | Source maps; a JS error reporter (Sentry, Crashlytics JS) |
| **Java/Kotlin (Android)** | `FATAL EXCEPTION` with a stack | ProGuard/R8 `mapping.txt` → symbolicate |
| **Native (C/C++, both)** | `SIGSEGV`/`SIGABRT` in a `.so`, a tombstone | Native symbols (unstripped `.so` or the NDK symbols) |
| **iOS Swift/ObjC** | A crash log with addresses | **dSYM** → symbolicate |
| **System (OOM)** | App killed, no crash log, "low memory" | Memory profiling, not a stack trace |

Most confusing cases are a **native crash inside a library** (image decoder,
a WebView, Fabric view) — the top frames are `libc` and the useful frame is in
a stripped `.so`. That's where correct symbol upload matters.

## "Works in dev, crashes in release" — the checklist

This is the single most common report. Work these in order; the causes are
almost all here:

1. **ProGuard/R8 (Android) shrinking/obfuscation.** Debug builds skip R8;
   release runs it. Reflection, serialization, and class-name-based lookups
   break. Symptoms: `ClassNotFoundException`, `NoSuchMethodError`, a null from
   a serializer, JSON fields silently empty. Fix: `-keep` rules for anything
   reached reflectively, or add the library's consumer rules
   (`-dontwarn`, `-keep class com.x.y.** { *; }`). See
   `references/symbolication.md` for keep-rule examples.
2. **Missing resources stripped by R8.** R8 removes "unused" resources
   referenced only in XML/manifest. `Resources$NotFoundException` at release
   only. Fix: `res/raw/keep.xml` with `tools:keep="@layout/*"` for the layouts
   inflated only by name.
3. **iOS: missing dSYM / wrong dSYM** → unreadable stack. Upload the dSYM at
   build time so the crash is symbolicated. (See below.)
4. **Hermes bytecode + `__DEV__` branches.** Code in `if (__DEV__) { … }` never
   runs in release; the release path is untested. Also: dev Fast Refresh and
   Metro differ from the bundled release JS.
5. **Debug-only code left in.** `if (__DEV__)` guards that hide an
   initialization; `console.log` of a value that's only set in dev; a
   `debugger`; test bundles; a `NODE_ENV` check that changes behaviour.
6. **The dev server vs the bundle.** Release uses a **bundled** JS (no Metro).
   Dynamic `require`, unresolved assets not in `assetBundlePatterns`, and
   circular imports can bundle differently than dev. Reproduce with a
   **production build** (`eas build --profile production` or a local release
   build), not Expo Go.
7. **Different SDK/native version.** Expo Go / a dev client ≠ your production
   build's native side. A library that works on the dev SDK can crash on your
   production SDK version.
8. **Environment/config.** A missing env var or `EXPO_PUBLIC_` value in the
   production build (dev `.env` present locally, absent in EAS) → `undefined`
   deref. Prod API URL wrong.
9. **Timing.** Release is faster but does *not* wait for slow dev-time
   promises; a race that dev's slower timing hides shows up in release.
10. **Permissions on first launch.** Release installs fresh and hits a
    permission/notification prompt the dev install already granted.

## Getting symbolicated stack traces

### Android (ProGuard/R8)

- R8 writes a **`mapping.txt`** next to the release output mapping. Upload it to
  **Firebase Crashlytics** (the Gradle Crashlytics plugin uploads it
  automatically) or Sentry (`sentry-android-gradle-plugin` with
  `mappingFile` auto-upload), and re-symbolication of historical reports works.
- To symbolicate by hand, `retrace` is bundled with the Android SDK build
  tools:
  ```bash
  retrace -s app/build/outputs/mapping/release/mapping.txt obfuscated_trace.txt
  # (or: <build-tools>/retrace; or ProGuard's retrace.jar)
  ```
- **Keep rules** go in `android/app/proguard-rules.pro`:
  ```proguard
  # Serialization/reflection (Gson/Moshi/models)
  -keep class com.me.models.** { *; }
  -keepclassmembers class * { @com.google.gson.annotations.SerializedName <fields>; }
  # React Native bridge (rarely needed with autolinking, but for native modules)
  -keep class com.me.MyNativeModule { *; }
  # Kotlin metadata / coroutines
  -keepattributes *Annotation*, Signature, InnerClasses
  ```
- **Library consumer rules**: most well-maintained Android libraries ship their
  own `consumer-rules.pro`; a *missing* `-dontwarn` for an optional dependency
  is a very common R8 "Missing class" build error.

### iOS (dSYM)

- Xcode (or a CI build) produces a **`.dSYM`** next to the `.app`. Xcode
  uploads it to App Store Connect automatically for App Store/TestFlight
  builds. For **local/ad-hoc/dev builds and third-party crash reporters**, you
  must upload the dSYM (Crashlytics via the `upload-symbols` script, or Sentry via
  `sentry-cli`):
  ```bash
  # Firebase Crashlytics — the script's location depends on how you installed
  # the CLI, so find it rather than assuming a path:
  find "$HOME/Library" "$HOME/.cache" -name upload-symbols 2>/dev/null
  # ./upload-symbols <APP-UUID> path/to/App.app.dSYM
  # Sentry
  sentry-cli debug-files upload --org <org> --project <proj> path/to/App.app.dSYM
  ```
- **Bits must match**: the dSYM is tied to the exact binary. Rebuild → new
  dSYM. A dSYM from a different build symbolicates to garbage/wrong frames.
- `xcrun dwarfdump --uuid <binary>` gives the UUID Crashlytics matches on.
- Swift symbol names are mangled (`$s4Main5VideowC`); symbolication demangles
  them. Very high line numbers in a mangled frame mean the wrong dSYM.

### JavaScript / Hermes (source maps)

- For a **release** JS bundle, ship the **source map** to your JS error
  reporter (Sentry, or Crashlytics' JS support) so JS stack traces map to
  original TS/TSX.
- Metro generates `main.jsbundle.map` with `--sourcemap-output` (Expo: the
  `sourcemap` output; Crashlytics/Sentry can consume it). Upload it with the
  build.
- Hermes has its own bytecode; a Hermes crash needs the source map + the exact
  bundle. Keep the source map artifact per release.

### Native (.so) symbols

- A native crash (image decoder, WebView, a Fabric view) gives you a `.so`
  frame. If the `.so` is **stripped** (typical for release), you need the
  **unstripped symbols** (from the build, matching the NDK version and ABI)
  uploaded, or a local symbol server. Firebase Crashlytics collects native
  symbols; for a stripped 3rd-party `.so` you may only get module+offset —
  symbolicate the offset against the matching build of that library.

## Crashlytics / Firebase (and the alternatives)

- **Firebase Crashlytics** (free, Android + iOS): crash grouping, ANR
  reporting (Android), custom logs/keys, and auto symbol upload via the Gradle
  plugin and the iOS build phase. Good default.
  - **Custom keys** are the highest-value feature for a hard crash: set the
    last screen, user id, feature flags as **keys** (not logs) so every report
    in that group carries them.
  - **Breadcrumbs / logs** around the crash tell you what happened just
    before.
- **Sentry** (free tier): better for **JS** errors and for cross-platform
  correlation; needs `sentry-cli` dSYM upload and Gradle source-map/mapping
  upload configured.
- Use **both** if you can, but you need **both** to have symbols on both
  platforms; symbol upload is the part people forget.

**Workflow when a release crash arrives:** find the crash group → get the
symbolicated stack → identify the top app frame → reproduce on a release build →
fix. If the stack is unsymbolicated, the whole chain stops at step 2.

## ANRs (Android) and hangs (iOS)

- **ANR (Application Not Resolved)**: the main thread is blocked >5s (input
  dispatch) — commonly a slow disk/network *on the main thread*, a big
  synchronous parse, or a deadlock. Crashlytics reports ANRs with the main
  thread's stack. Fix: move I/O off the main thread, break up long work, find
  the lock cycle.
- **iOS**: the watchdog kills apps that hang the main thread; a release
  "termination" with no crash is often a watchdog kill. Instruments /
  `os_signpost` / metric-kit hangs traces help.

## Memory: OOM and pressure

- **Android**: `OutOfMemoryError` (Java heap) or the system killing the app
  (no log — check `adb shell dumpsys meminfo <pkg>`, or `ActivityManager`
  "low memory" in logcat). Common mobile OOM causes: **large images/bitmaps
  retained**, a growing in-memory list, a leak (a static/long-lived reference
  to a destroyed Activity/View, an un-cleared listener), or decoding on the
  main thread. Android's heap is small; image decode is the usual culprit
  (see the `react-native-performance` skill).
- **iOS**: jetsam kill (no crash log — check `log stream` / Xcode memory
  report / metric kit `MXCrashDiagnostic`/`MXMemoryDiagnostic`). Watch for
  large image caches, retained view controllers, and `didReceiveMemoryWarning`
  spikes.
- **RN specifics**: a virtualised list with too high a `windowSize`, large
  base64 images, a JS array growing without bound, or an un-cleared
  subscription. Profile JS heap (Hermes) and native allocations separately.
- **The fix is a bound, not a bigger heap.** Identify what grows unbounded and
  cap/evict it. A memory *pressure* pattern (GC churn) precedes the OOM.

## Debugging steps (the actual procedure)

1. **Get a symbolicated stack.** Upload mapping/dSYM/source-map; re-symbolicate.
   If you can't symbolicate, stop — everything else is guesswork.
2. **Classify the layer** (JS / Java / native / system OOM) — it determines the
   next tool.
3. **Reproduce on a release build** with the same config. If you can't
   reproduce, use the crash reporter's custom keys/logs and the distribution
   (device model, OS version, app version) to find the common factor.
4. **Native crash** → get the unstripped symbols for the `.so`; symbolicate the
   offset; the crashing library is the suspect.
5. **OOM** → find the growing buffer (profile allocations; check image
   caches / retained lists / leaks).
6. **ANR** → read the main-thread stack; find the blocking call or lock.
7. **Add a regression guard**: the repro as a test where possible, and a
   Crashlytics custom key for the state you need next time.

## Gotchas

- **A missing/unmatched mapping file is the #1 reason a report is useless.**
  Store the mapping/dSYM with the release version from the moment you cut it;
  they are irreplaceable later.
- **Dev builds have no R8 and no `__DEV__=false` path**, so dev is structurally
  different from release. Never "it works on my device" as release evidence.
- **Hermes and JSC produce different behaviours** (numeric edge cases,
  `Intl`). Default to Hermes; don't compare across engines.
- **Stack traces can be truncated or async-confused** (promises): a rejection
  handled nowhere surfaces as an "unhandled promise rejection" far from the
  cause. Add global handlers to capture the real stack.
- **Line numbers in a deobfuscated stack must line up with the source you
  push.** If a code change landed after the build, line numbers are off — the
  symbols are still right for that build; match the build, not `main`.
- **ProGuard keeps vs `-dontobfuscate`**: stripping obfuscation everywhere is
  a tempting fix for a keep-rule problem but bloats the APK and hides the real
  issue. Add targeted keeps.
- **Third-party native `.so` crashes** (image codecs, WebView, some analytics
  SDKs) need the *library's* symbols; your app's mapping won't symbolicate
  them. Match the exact library version's symbols.
- **A crash that only happens on a low-RAM device** is usually a memory issue,
  not a logic bug — test on a constrained device/simulator.
- **`if (__DEV__)`-guarded analytics/permissions can make the release build
  take a code path you never tested** — audit those branches.

## Review checklist

- [ ] R8/ProGuard keep rules for reflection/serialization; library consumer
      rules present.
- [ ] dSYM uploaded for iOS; `mapping.txt` uploaded for Android; JS source
      maps uploaded — all matched to each release.
- [ ] A crash reporter is live with custom keys (screen, user, flags) and
      breadcrumbs.
- [ ] Release builds are the ones tested (not Expo Go / dev client) before
      release.
- [ ] `__DEV__` branches audited; no leftover debug code or localhost URLs.
- [ ] Memory growth is bounded (image caches, lists); tested on a low-RAM
      device.

## Files

- `references/symbolication.md` — concrete symbol-upload commands, keep-rule
  examples, source-map config, and a "which symbol for which crash" table.

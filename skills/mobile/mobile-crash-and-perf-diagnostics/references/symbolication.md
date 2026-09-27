# Symbolication reference

Load this when a crash report is unreadable, or when setting up automatic symbol
upload for Android, iOS, JS, and native `.so` crashes.

## Which symbol file for which crash

| Crash layer | Symbol needed | Produced by | Upload to |
|---|---|---|---|
| Java/Kotlin (Android) | `mapping.txt` (R8/ProGuard) | Release build (AGP writes it to `app/build/outputs/mapping/release/`) | Firebase Crashlytics (auto via Gradle plugin) or Sentry (auto via Gradle plugin) |
| iOS Swift/ObjC | `.dSYM` (per-arch) | Xcode build; uploaded to App Store Connect for store/TestFlight builds | App Store Connect (auto for store builds); Crashlytics/Sentry via `upload-symbols`/`sentry-cli` for other builds |
| JavaScript (Hermes/JSC) | JS source map (`*.jsbundle.map`) | Metro `--sourcemap-output`; Expo build `sourcemap` output | Sentry / Crashlytics JS SDK |
| Native `.so` (C/C++ in a lib) | Unstripped symbols for that `.so` (NDK debug symbols) | Your build, or the library's release artifacts | Crashlytics (native symbol upload) / Sentry (debug files) |
| React Native release JS in a native crash | Both the native symbols **and** the JS source map | — | — |

**The version of the symbol must match the build that crashed.** Rebuilding
produces a new mapping/dSYM; a report from the previous build will not
de-obfuscate with the new one. Archive the symbols per release version.

## Android: R8 / ProGuard

### Where the mapping is
`app/build/outputs/mapping/release/mapping.txt`. If your build produces a bundle
(`bundleRelease`), it is next to it. Keep a copy per release.

### Manual retrace (obfuscated stack → real stack)
```bash
# retrace ships with the Android SDK build-tools
retrace -s app/build/outputs/mapping/release/mapping.txt crash-log.txt

# ProGuard's own (older) way
java -jar proguard.jar -retrace crash-log.txt
# line numbers use '?' when inlining/opt obfuscation strips them; retrace still
# gives you the class/method/line it can recover.
```

### Keep rules (`android/app/proguard-rules.pro`)

```proguard
# --- Default Android/RN/AGP keeps the plugin contributes automatically ---

# Gson / Moshi / kotlinx-serialization models (reflection-based)
-keep class com.me.myapp.data.model.** { *; }
-keepclassmembers class * {
  @com.google.gson.annotations.SerializedName <fields>;
  @kotlinx.serialization.Serializable <fields>;
}
-keepattributes Signature, *Annotation*, InnerClasses, EnclosingMethod

# DataBinding / ViewBinding inflate by name
-keep class com.me.myapp.databinding.** { *; }

# Native modules exposed to JS by name
-keep class com.me.myapp.nativemodule.** { *; }

# Optional deps the library guards with reflection: silence, don't crash build
-dontwarn com.some.optional.**
```

- **`consumer-rules.pro`**: a well-maintained library ships its own keeps that
  apply to *your* app automatically. If you hit "Missing class
  `com.x.y`" as an R8 error, it's a missing `-dontwarn` for an optional
  dependency the library only touches on some classpath. Add the
  `-dontwarn` the library's docs specify.
- **Resources stripped by R8** (layouts inflated by name only):
  `res/raw/keep.xml`:
  ```xml
  <resources xmlns:tools="http://schemas.android.com/tools"
      tools:keep="@layout/*,@id/*" />
  ```
  and ensure it's not stripped itself (it's referenced by the build config).
- **The tempting non-fix**: `minifyEnabled false` or `-dontobfuscate` globally.
  It "fixes" the crash, hides the real keep-rule bug, and bloats the APK. Use
  targeted keeps; only disable for a local build while bisecting.

### Auto symbol upload to Firebase Crashlytics
Add the Crashlytics Gradle plugin; the `mapping.txt` is uploaded during the
release build automatically. Verify the plugin is applied to the `app` module,
and that you use a **release** build type (debug builds don't produce a mapping).

## iOS: dSYM

- **Xcode → Product → Build → show Build Settings → "Debug Information
  Format" = `DWARF with dSYM File`** (the default). A build with
  `dSYM` disabled (some CI settings set it to "none" to save space) cannot be
  symbolicated later.
- **App Store / TestFlight**: Xcode uploads the dSYM to App Store Connect with
  the binary. Crashes in App Store Connect / TestFlight are symbolicated
  automatically. Nothing to do for store builds.
- **Ad-hoc / local / enterprise / third-party reporter**: upload manually.
  ```bash
  # Firebase Crashlytics
  PATH="$PATH:~/Library/Android/sdk/../firebase_crashlytics" \
    ~/Library/.../upload-symbols <APP-UUID> path/to/App.app.dSYM
  # Get the app UUID:
  xcrun dwarfdump --uuid path/to/App.app/App
  # Sentry
  sentry-cli debug-files upload --org <org> --project <project> \
    path/to/App.app.dSYM
  ```
- **Bit-match check**: `dwarfdump --uuid` of the dSYM must equal the UUID of
  the crashed binary, or Sentry/Crashlytics will reject or mis-symbolicate.
- **Fastlane** uploads the dSYM as part of `build_app_store` / `upload_to_app_store`
  (it runs `upload_symbols_to_crashlytics` in the crashlytics plugin for the
  Crashlytics backend).

## JavaScript source maps (Hermes)

- **Expo/EAS**: the build produces a source map with the bundle; upload it to
  your JS reporter (Sentry via `@sentry/react-native` `metro.config.js`
  wrapper, or Crashlytics JS). Configure the Sentry Metro plugin:
  ```js
  // metro.config.js
  const { getDefaultConfig } = require('@sentry/react-native/metro-config');
  module.exports = getDefaultConfig(__dirname);
  ```
  This makes Sentry upload source maps on release builds and symbolicate Hermes
  stacks.
- **Bare RN / manual**: build with a source map and upload it:
  ```bash
  npx react-native bundle --platform ios --dev false \
    --entry-file index.js --bundle-output main.jsbundle \
    --sourcemap-output main.jsbundle.map
  # upload main.jsbundle + main.jsbundle.map together
  ```
  The map is only valid for that exact bundle; keep them paired per release.
- **Hermes stack in production** looks like
  `address at /data/user/0/.../index.android.bundle:1:23456` — the source map
  turns it into file/line/column in your source. Without it, it's noise.

## Native `.so` crashes

- Frame: `libfoo.so (foo+1234)` or `#00 pc 000000000001234  /data/app/.../libfoo.so`.
- To symbolicate, you need the **unstripped** build of that exact `.so`
  (same ABI arm64-v8a, same version). Keep the NDK debug symbols from your CI
  for libraries you build.
- For **third-party** native libs (image codecs, WebView, some SDKs), you may
  only have the module+offset. Symbolicate the offset against a matching build,
  or report it to the library; Crashlytics collects native symbols for common
  system libraries.
- **Strip the JS, keep the native**: an app can have BOTH a native crash frame
  *and* a JS bundle referenced (React Native). You need the native symbols to
  read the frame, and the JS source map to read the JS part.

## Setting up Firebase Crashlytics end to end

1. Add a Firebase project, register the Android app (package name) and iOS app
   (bundle id); download `google-services.json` / `GoogleService-Info.plist`
   into the project.
2. Android: apply `com.google.firebase.crashlytics` Gradle plugin; the mapping
   uploads on release builds. Set `firebaseCrashlytics.mappingUploadEnabled`
   for local debugging if you want it on every build.
3. iOS: add the Crashlytics SDK; for non-App-Store builds add the
   **upload-symbols** build phase (or run the script in CI) so dSYMs upload.
4. Add **custom keys** at key decision points (screen name, user id, feature
   flags, last action) so every report in a group carries the state:
   ```ts
   import { setCustomKey } from '@react-native-firebase/crashlytics';
   setCustomKey('screen', 'Checkout');
   setCustomKey('cartSize', String(cart.length));
   ```
   Keys (not logs) are attached to all reports in the group — far more useful
   than a breadcrumb you have to correlate.
5. Verify by forcing a test crash in a **release/internal build** (not dev) and
   confirming it arrives symbolicated. Test the pipeline, not just the SDK
   install.

## Sentry (JS-first alternative)

- `@sentry/react-native` + the Metro config plugin handles source-map upload
  and symbolication for JS; add `sentry-cli debug-files upload` (or the Gradle
  plugin) for iOS dSYMs and Android mappings. Configure `release` and
  `environment` so crashes are attributable to a version and a build type
  (dev vs prod).
- Same hard rule: symbols uploaded, per release, matched to the build.

## A no-symbols escape hatch

If you truly have no symbols for a release crash, you can still act on:
- the **crash group's device/OS/version distribution** (a specific OS or device
  model points at platform code, not your logic);
- **custom keys and breadcrumbs** you set;
- the **native module/offset** (which library's `+offset`), which at least
  names the suspect.

But this is triage, not a fix — treat getting symbols uploaded for the next
release as a required task.

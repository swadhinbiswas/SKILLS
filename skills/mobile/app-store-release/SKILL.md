---
name: app-store-release
description: Ship a mobile app to the App Store and Google Play without getting rejected - the review guidelines that actually cause rejections, privacy declarations, IAP rules, account deletion, signing and provisioning, versioning and build numbers, and staged rollouts. Use when preparing a release, when a build was rejected, when setting up certificates or the Android keystore, or when choosing between build numbers and versions. Triggers on "app store review", "rejected", "TestFlight", "Play Console", "privacy policy", "account deletion", "in-app purchase", "signing", "provisioning profile", "keystore", "build number", "staged rollout", "guideline 2.1", "App Store Connect", "release checklist".
compatibility: App Store Connect and Google Play Console. Guideline numbers cited are the current published ones; re-check before relying on an exact clause.
metadata:
  version: "1.0"
---

# App Store Release

Rejections are almost always boring and fixable: a missing privacy declaration,
a demo account that expired, a broken build, an IAP that bypasses the store.
Work the checklist before you submit, not after the rejection email.

## Workflow

Progress:

- [ ] 1. Bump the version (and the build number) and confirm signing/credentials
- [ ] 2. Work the release checklist below (metadata, privacy, IAP, account
      deletion, screenshots)
- [ ] 3. Build the store artifact and verify it (install the release build)
- [ ] 4. Submit for review with notes explaining anything unusual
- [ ] 5. Roll out (staged/phased first), watch crash rates, expand

## Versioning and build numbers

| | App Store | Google Play |
|---|---|---|
| User-facing version | `CFBundleShortVersionString` (Expo `version`, e.g. `1.2.0`) | `versionName` (Expo `version`) |
| Build identifier | `CFBundleVersion` / `buildNumber` (integer) | `versionCode` (integer) |
| Rules | Both must **increase** for a new upload | Same |

- **A build number must be higher than the last uploaded**, even for the same
  version string. Re-uploading a build with a used number is rejected.
- **EAS**: `"autoIncrement": true` in the `production` profile in `eas.json`
  bumps the build number per build. On Play, use **Play App Signing**; you
  upload with an upload key and Play re-signs with the app key.
- **Version strategy**: a new **user-facing version** only when there are user
  visible changes. **Build numbers** for every upload (hotfixes, OTA-like
  rebuilds, store-required changes). Google's Play Console can reject a
  `versionCode` you already used; iOS similarly per build number.
- **Play requires a recent target API level.** New apps/updates must target a
  recent API level (the requirement rises each year — check the current
  deadline; it's commonly API 34/35 within the year). An old Expo SDK may not
  meet it → an upgrade may be forced on you. Check before planning a release.

## Signing and provisioning

- **iOS**: an **Apple Distribution** certificate, an **App ID** (bundle id),
  and a **provisioning profile** that includes your certificate. Apple
  Developer Program membership is required. TestFlight and the App Store use
  the distribution certificate; development uses a different one.
  - EAS manages these (`eas build` prompts to create/choose credentials; back
    them up with `eas credentials`).
  - Automatic signing (Xcode) regenerates profiles but ties you to a Mac and
    an Xcode account; EAS is the usual CI-friendly route.
  - Capabilities (push, associated domains, HealthKit, iCloud) are **enabled on
    the App ID** *and* in the entitlements. A missing capability produces
    "provisioning profile doesn't include entitlement" at build or a silent
    feature failure at runtime.
- **Android**: an **upload keystore** (signs the AAB you upload). With **Play
  App Signing** (the default for new apps), Play holds the *app signing key*
  and re-signs the distributed app; your upload key is what you keep.
  - **Back up the keystore and its passwords.** If you lose the upload key and
    cannot use Play's key-reset process, you can never update that listing.
  - Never commit the keystore or passwords. Use EAS secrets / CI secrets.
- **Keep signing config out of the repo** (`.gitignore` `*.keystore`,
  `*.jks`, `credentials.json`). CI should pull secrets, not a committed key.

## The metadata and policy checklist (where rejections come from)

Apple and Google both reject for the same core reasons. Work these:

- **Privacy policy**: a publicly reachable URL describing what you collect and
  how. Both stores require one; it must be a real, loadable page (not a PDF
  behind a login, not a 404). Link it in the store listing **and** in the app
  (in-app privacy link or settings).
- **Privacy declarations / data safety**:
  - **Apple — App Privacy** (nutrition label): declare every category of data
    you and your SDKs collect, whether it's linked to identity, and whether it's
    used for tracking. **Inaccurate privacy labels are a rejection** (and now
    also enforceable). Keep it in sync when you add an SDK.
  - **Google Play — Data safety** section: same idea, plus a
    **Privacy policy** URL. Google audits the app against your declarations; a
    mismatch (you declare no collection but an SDK collects an ad ID) is a
    common enforcement action.
- **Account deletion** (a hard requirement on both stores):
  - **Apple 5.1.1(v)**: if you support account creation, you **must** support
    account deletion **in-app** (not just a website/email).
  - **Google Play**: a **Data safety → Account deletion** URL/path; must be
    in-app and effective, deleting the data (or explaining why not).
  - Provide a real in-app path, and it must actually delete/soft-delete the
  **server-side** data, not just log you out.
- **Sign-in / demo account**:
  - Apple: if the app has restricted features (login, sharing, etc.) and is
    behind anything, you must provide **full working demo credentials** in the
    review notes, or reviewers can't get in. Expired demo accounts → automatic
    rejection. Make sure the account is active and the backend is up.
  - Google: same idea for apps needing login.
- **In-app purchase / billing** (Apple 3.1.1, Google Play Billing policy):
  - **Apple**: digital content/features consumed in-app must use **IAP** —
    unlocking, subscriptions, coins, a "pro" tier, even a one-time purchase.
    Physical goods/services and external-purchase exceptions exist but are
    narrow. **Linking a user straight to a web checkout for digital goods is a
    reliable rejection.** Reader apps and multiplatform services have explicit
    exceptions.
  - **Google Play**: use the **Play Billing Library** for in-app digital
    goods. You may (and often should) direct users to an external payment
    option for physical goods/services, or offer an external purchase path
    where policy allows — but the flow must be compliant and disclosed.
  - **Server-side receipt/validation**: validate purchases server-side; don't
    rely on the client. A reviewer buying something that doesn't unlock is an
    immediate rejection.
  - Restore purchases (iOS) must work.
- **Minimum functionality / web wrapper (Apple 4.2)**: a site that repackaged
  in an app with little native value is rejected. Have real native features.
- **Content**: no placeholder content, no broken links, no dead "coming soon"
  screens in a shipping build, no beta/dev menus visible. **Remove debug
  affordances, test data, and "Lorem ipsum".**
- **Permissions**: request in-context (when the feature is used) with a
  pre-prompt explaining why; only the permissions you actually use, with
  `NS…UsageDescription` strings (iOS) that explain purpose. An iOS build that
  crashes or hangs on a permission prompt, or has empty usage descriptions, is
  rejected.
- **Screenshots and metadata**: screenshots must be of the **current** app, from
  a real device, showing real content. Store listing must be complete (title,
  description, support URL, privacy policy, category, contact). Reserve the
  right screen sizes (Apple accepts a limited set; Google wants phone + optional
  7"/10" tablet).
- **Age rating / content descriptors**: accurate. **GDPR / kids**: if kids are
  in scope, stricter data collection, ads, and parental-gate rules apply.
- **Export compliance**: answer the encryption questions. Setting
  `ITSAppUsesNonExemptEncryption: false` (iOS) when you only use standard HTTPS
  avoids an export-compliance prompt (which can hold a build). If you do use
  non-exempt crypto, you'll get a document request in App Store Connect.

## Staged / phased rollouts

Ship progressively and watch before you ship to everyone:

- **App Store**: **Phased Release (7-day)** on the App Store version — Apple
  auto-rolls out to 1% → 2% → 5% → 10% → 20% → 50% → 100% over 7 days if
  crash-free. You can pause anytime. You control the percentage manually too.
- **Google Play**: **Staged rollout** — pick a % (start 5–10%) and a
  **health guard** (e.g. halt if ANR/crash rate exceeds a threshold over a
  window), then promote. Halt thresholds catch problems automatically.
- **Watch**: crash-free users (both consoles), ANRs (Android), and your own
  analytics. Have a **rollback plan**: for iOS you can't un-ship, but you can
  halt the phase and ship a fix fast; on Play you can **halt** the rollout and
  roll back to a previous version. This is why staged rollout is the default for
  non-trivial releases.
- **Keep the previous good build installable** on Play (rollout allows
  rollback); on iOS, TestFlight can hold a "previous" build to promote if the
  App Store build is broken.

## The release checklist

Run before you press submit:

- [ ] Version (user-facing) and build number both increased; correct platform
      targets.
- [ ] Signing works; keystore/cert backed up; secrets not in the repo.
- [ ] Install the **release** build on a real device and click through the
      primary flows — login, the reviewed feature, purchase. (Debug-only bugs
      and dev menus are the classic "crashes for reviewers".)
- [ ] Privacy policy URL live and reachable; app privacy / data safety
      declarations match reality.
- [ ] In-app account deletion present and functional.
- [ ] Demo login credentials in the review notes, active, with the backend up.
- [ ] IAP: tested end-to-end including restore; server validates.
- [ ] Permissions: in-context prompts, usage descriptions, no crashes.
- [ ] No debug/test content, no broken links, no dead screens.
- [ ] Screenshots are current; listing complete; support/contact URLs live.
- [ ] Export-compliance answered.
- [ ] Staged/phased rollout planned with a health guard and a rollback plan.
- [ ] OTA vs store decided correctly (see the `expo-app-lifecycle` skill).

## Review notes that help (and hurt)

- **Provide credentials and a "how to test" path in the notes.** Reviewers
  reject when blocked, not when the app is complex. Say which screen to check.
- **Explain anything unusual** (a demo mode, a review-only flag, a backend that
  must be running) clearly up front.
- **Don't** add a hidden review-mode that behaves differently in a way that
  looks like bait-and-switch; keep it honest and minimal.

## Gotchas

- **Review is not instant and not guaranteed fast.** Apple's review is usually
  hours–days (longer around launches/holidays, or if you reply); Google's can be
  hours, and new accounts/apps can be held. Plan lead time; don't ship on the
  last day.
- **A rejection is often one specific metadata/privacy/IAP item** — the email
  names the guideline (e.g. Guideline 2.1 App Completeness, 5.1.1 Privacy,
  3.1.1 IAP). Fix exactly that and resubmit; don't rebuild unnecessarily.
- **Guideline numbers and requirements change.** Apple/Google update policies
  (and Play's target-API deadline) regularly — re-check the current published
  guidelines rather than relying on a number you remember.
- **Privacy labels drifting from reality** (an added ad SDK) is both a
  rejection risk and, post-release, an enforcement/enforcement-action risk.
  Treat the privacy declarations as code-reviewed config.
- **Play App Signing changes the key you back up**: back up the *upload* key
  (yours) and know that Play holds the app signing key. Don't confuse them.
- **TestFlight / internal testing is not the App Store/Play review** — a build
  can pass TestFlight and fail review (IAP, account deletion, privacy are
  enforced there).
- **Don't ship a build that only works in dev**: Metro dev server, debug flags,
  `NSAllowsArbitraryLoads`, localhost URLs. Reviewers get the release artifact.
- **The privacy "nutrition label" in Apple is per-app and manually declared** —
  it is not auto-derived, so it silently goes stale. Re-review it when you add
  analytics/ads/auth.
- **In-app purchase that unlocks nothing (or unlocks only client-side)** is
  rejected; the entitlement must be enforced by your server.

## Review checklist

- [ ] Version + build number increased; targets current.
- [ ] Release build verified on device (login, feature, purchase, restore).
- [ ] Privacy policy live; privacy/data-safety declarations accurate and
      current.
- [ ] In-app account deletion works.
- [ ] Demo credentials + test path in review notes; backend up.
- [ ] IAP/billing compliant and server-validated.
- [ ] Permissions requested in context, usage descriptions present.
- [ ] Metadata/screenshots current; no debug content.
- [ ] Staged rollout with health guard and rollback plan.

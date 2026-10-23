# Phone apps

Native apps for Android (Kotlin, Jetpack Compose) and iPhone (Swift,
SwiftUI). Every screen is the app's own, with no web view. Other companies' sites (Tailscale, GitHub's forms) open in
the phone's browser, and the sign-in never goes with them.

The apps talk only to the agent on your PC, over Tailscale, with the same
API as the desktop app. There is no server in between and no account.

## What's where

| Folder | What |
|---|---|
| `../shared/` | The words and colors every app uses, edited by hand (see below). Both apps bundle these files. |
| `api-routes.txt` | Every agent route the apps call. `tests/test_phone_app.py` fails if one disappears, so the agent stays compatible with apps already on phones. |
| `android/core/` | Everything the Android app decides that doesn't need Android: reading the pairing code, the certificate check, talking to the agent, the wording, the server colors, the Notifications tab and the widget. Plain Kotlin with JUnit tests. |
| `android/app/` | The Android app. |
| `ios/Core/` | The iPhone app's equivalent of `android/core`, a Swift package with tests. |
| `ios/` | The iPhone app and its widget (`project.yml` is the XcodeGen spec). |

## Words and colors

The top-level `shared/` folder is the one place for words and colors, read
by the desktop app and both phone apps:

- `strings.json`: every word, one line per key, as `[Simple, Technical]`.
  The phone apps' own keys start with `mobile.`, the desktop app's with
  `desktop.`. `tests/test_strings.py` checks both wordings have the same
  `{placeholders}`.
- `feed.json`: which events show in the activity feed, and their words.
- `theme.json`: each theme's colors.
- `color-cases.json`: how each palette color tints each theme. The tests of
  all three apps check their tinting against it.
- `version.json`: the phone apps' version and the agent API versions they
  work with.

## Building

**Android:** Android Studio (or JDK 17 and the Android SDK), then:

```
cd mobile/android
./gradlew :core:test :app:assembleDebug
```

Without the Android SDK, `./gradlew -PcoreOnly=true :core:test` runs only
the core tests.

**iPhone:** a Mac with Xcode 16 and [XcodeGen](https://github.com/yonaskolb/XcodeGen):

```
swift test --package-path mobile/ios/Core
xcodegen generate --spec mobile/ios/project.yml
open mobile/ios/ServerController.xcodeproj
```

CI (`.github/workflows/mobile.yml`) does both on every change under
`mobile/`, unsigned. The first build of each app is in CI.

## Lock-screen alerts (Firebase and Apple)

Android lock-screen alerts use Google's Firebase Cloud Messaging, sent by
the agent itself. Two files come from your Firebase project (see
`docs/notifications.md` for making it):

- `google-services.json` (for building the app): save its contents as the
  repository secret `GOOGLE_SERVICES_JSON` (**Settings → Secrets and
  variables → Actions**). CI writes it to `android/app/` before building.
  To build on your own PC, put the file there; it is in `.gitignore`.
  Without it the app builds and works, with lock-screen alerts off.
- The service-account key (for the PC that sends): only on that PC, put
  there by `python -m installer.setup_phone_alerts`. Never in the
  repository, `.env` or a secret.

iPhone lock-screen alerts use Apple's push service, also sent by the agent.
The app needs the Push Notifications capability on its App ID (Apple
Developer site); its notification extension (`ios/ServerControllerAlerts`)
swaps Apple's plain "New alert" for the alert's own words. The `.p8` key
goes only on the PC that sends, with
`python -m installer.setup_phone_alerts --apple <file> --team <team ID>`
(`docs/notifications.md` has the steps).

## Keys and signing

Signing keys and store credentials never go in the repository or `.env`:
only in CI secrets (in a protected environment) or on the PC that builds a
release. `android/app/build.gradle.kts` signs a release only when
`ANDROID_KEYSTORE_FILE`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`
and `ANDROID_KEY_PASSWORD` are set. Store releases come later.

## House rules, in the apps too

- A value the agent didn't measure shows as Unknown, never 0 or a guess,
  and an old reading is never shown as current (the widget and the server
  list turn Unknown when the PC doesn't answer).
- The apps start, stop and restart servers through the same API routes and
  permissions as the desktop app; they can't run anything else on the PC.
- A backup downloads only to a place the person picks, with the sign-in in
  a header (never in the address). A restore always asks the agent first
  what it would do, and the agent makes a backup of the world as it is now
  before putting another back.
- Certificate checks are never switched off. A pairing code names the PC's
  certificate and only that one is accepted; a typed address needs one the
  phone trusts, or the person compares fingerprints.
- Logs and "Copy details for a problem report" never hold the sign-in, a
  password, the PC's address or the certificate fingerprint.

# Voxos mobile

A deliberately small iPhone and Android client for the existing Voxos text and
voice agents. It does not duplicate the web app.

## Chat behaviour

- Signing in opens a single conversation screen. Typing and talking share the
  same surface, and the composer holds both actions.
- Conversations and transcripts use the authenticated user's durable Voxos
  chat history; the mobile app does not keep a separate local transcript.
- The newest saved chat opens automatically. A new chat is created only when
  the first message is sent.
- The sidebar holds chat history, grouped by when each chat was last updated,
  with a search box for filtering by title. Swipe a history row left, or use
  the row menu, and confirm to delete it.
- Replies stream into the transcript. Stop ends the local stream and reconciles
  the view with the durable server transcript.
- Up to four images can be attached to a message. Drag a thumbnail to reorder
  the attachments before sending. The add button can take a new photo, choose
  images from the photo library, or paste an image from the clipboard. A copied
  image can also be pasted from the composer's long-press menu.
- The composer has one message path for text, image generation, and image
  editing. Ask naturally for an image; Voxos uses the conversation to prepare
  the image tool request and shows the generated attachment in the assistant
  response.
- To edit an image, attach references with the request or follow up on the most
  recently generated image in that chat. Current attachments take precedence.
  Stop cancels image-tool work through the same active response control.
- Text provider credentials and model preferences continue to be configured in
  the Voxos web app and are never stored by the mobile client.
- Authentication supports Firebase Email/Password and Google accounts. Apple
  sign-in remains available on the web app only.

## Voice behaviour

- The voice button in the composer opens the call screen. Voice never starts on
  launch, login, foreground resume, or server refresh.
- Opening the call screen checks that the server has voice configured, asks for
  microphone permission, connects, and then opens the microphone so the call is
  ready to speak into. Mute is one tap away.
- Every call is its own conversation: opening the call screen starts a new
  local conversation identifier and the backend creates a fresh runtime session.
- A connected call keeps the display awake. The normal idle timeout returns as
  soon as the call disconnects.
- End, or leaving the call screen, disconnects immediately so no paid realtime
  session stays open.
- Moving the app out of the foreground disconnects voice. The iOS background
  audio capability is intentionally not enabled.
- The backend's inactivity timeout remains a second safety net.

## Server setup

The app keeps a unique on-device list of HTTPS Voxos deployment roots. Choosing
or adding a server fetches its public client configuration from:

```text
GET /api/client-config
```

Set the corresponding deployment variables shown in the repository
[`.env.example`](../.env.example). On Kubernetes, create the referenced
application Secret from a private environment file as described in the
[Helm guide](../helm/README.md).

Configure the native IDs before creating matching Firebase registrations:

- Android: copy `android/installation.properties.example` to the ignored
  `android/installation.properties` and set `VOXOS_ANDROID_APPLICATION_ID`.
  A Gradle `-PVOXOS_ANDROID_APPLICATION_ID=...` argument takes precedence over
  the environment variable, which takes precedence over the installation file.
- iOS: copy `ios/Flutter/Installation.xcconfig.example` to the ignored
  `ios/Flutter/Installation.xcconfig`. Set `VOXOS_IOS_BUNDLE_ID` and
  `VOXOS_APPLE_TEAM_ID` for device builds and App Store archives.

For signed Android release builds, set all four `VOXOS_ANDROID_KEYSTORE_PATH`,
`VOXOS_ANDROID_KEYSTORE_PASSWORD`, `VOXOS_ANDROID_KEY_ALIAS` and
`VOXOS_ANDROID_KEY_PASSWORD` fields in the same ignored installation file.
The path is relative to `android/` unless absolute. Without these settings,
release APKs are unsigned; debug builds retain the usual local debug signing.

The public default ID is `org.example.voxos`. Unsigned simulator builds can
use this default with no Apple team configured. Register your own IDs for
signed distribution. The Kotlin namespace and clipboard channel are internal
protocol names and do not change when the application ID changes.

Enable Firebase Email/Password and Google authentication for the same project
used by the Voxos backend. Set `FIREBASE_GOOGLE_IOS_CLIENT_ID` to the iOS OAuth
client ID and `FIREBASE_GOOGLE_SERVER_CLIENT_ID` to the web OAuth client ID.
In `ios/Flutter/Installation.xcconfig`, set `GOOGLE_REVERSED_CLIENT_ID` to the
`REVERSED_CLIENT_ID` from your Firebase iOS configuration. Debug and release
builds use this value for the Google sign-in callback scheme.
First launch and server selection do not require a bundled Firebase project.
For push notifications tied to your own project, place its native configuration
in ignored `android/app/google-services.json` or
`ios/Runner/GoogleService-Info.plist`. Android applies Google Services generation
only when that file exists; iOS copies its configuration only when supplied.
Push uses the native default project only when it matches the selected server.
Provider keys, LiveKit credentials and signing secrets remain private. The fetched public
config and server addresses are stored with `SharedPreferences`; passwords are
never stored by Voxos. Changing servers does not sign out the current Firebase
session. A server with an existing session opens it, while a server without one
shows its sign-in screen. Signing out only ends the session for the selected
server.

## Error monitoring

Copy `sentry.config.example.json` to the ignored `sentry.config.json` and fill
`SENTRY_DSN` with the installation's mobile Sentry project DSN. The root mobile
Make targets and TestFlight script pass this configuration into the build.
Direct Flutter builds use `--dart-define-from-file=sentry.config.json`.

The app captures errors and samples 1% of performance traces, with tenant
payloads removed before transmission. Release builds use the `production`
environment and other builds use `development`; `SENTRY_ENVIRONMENT` can
override it. See [Sentry setup](../docs/wiki/Developer-Setup.md#sentry-error-monitoring)
for privacy defaults and quota controls.

## Local device configuration

Copy the example and add local device identifiers:

```bash
cp app/.env.example app/.env
```

`app/.env` is ignored. From the repository root:

```bash
make app-devices
make run-ios
make run-ios-device
make deploy-ios-device
make build-prod-ipa
make deploy-testflight
make app-run-android
make app-build-ios
make app-build-android
make app-analyze
make app-test
```

`make run-ios` opens and runs on an iPhone simulator. The two device commands
use `APP_IOS_DEVICE_ID` from the ignored `app/.env`: `run-ios-device` runs a
debug build, while `deploy-ios-device` installs a release-mode build.
`build-prod-ipa` creates a local App Store archive. For `deploy-testflight`,
copy `ios/ExportOptions-TestFlight.plist.example` to the ignored
`ios/ExportOptions-TestFlight.plist` and set `teamID` to the same Apple team
configured in `Installation.xcconfig`. The command uploads using that team's
App Store Connect account. Both commands require signing access in Xcode.
If command-line account access is unavailable, `deploy-testflight` exits with
an error after leaving the signed archive in `app/build/ios/archive/`; open that
archive in Xcode Organizer to complete the upload.

There are no mobile dev/prod flavours. The same app connects to whichever
backend deployment the user enters during initial setup.

Open the iOS workspace with `make app-open-ios` to configure the Apple account
and provisioning for the team selected by your private installation settings.

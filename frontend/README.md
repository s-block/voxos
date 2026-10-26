# Voxos Test Frontend

Lightweight Vite + React app for exercising the Voxos LiveKit voice worker.
For the full signed-in console guide, see
[`../docs/wiki/Frontend-Guide.md`](../docs/wiki/Frontend-Guide.md).

## Why this stack?

LiveKit's current React voice-agent guidance uses Vite, `livekit-client`, and
`@livekit/components-react`. The browser talks to the LiveKit room over WebRTC,
while the Voxos worker bridges the agent to OpenAI Realtime.

## Run

From the repository root:

```bash
make frontend
```

Or from this directory:

```bash
npm install
npm run dev
```

## Firebase Auth

The browser follows the same auth contract as the future Flutter client:

1. Sign in with Firebase on the client.
2. Call `currentUser.getIdToken()`.
3. Send that token to Voxos as `Authorization: Bearer <firebase-id-token>`.

The backend verifies the Firebase ID token, maps the Firebase UID to a durable
local Voxos user id, and mints the LiveKit room token for that local id.

After signing in, save your OpenAI key in the Voice setup panel. The browser
sends it once to the authenticated backend, where it is encrypted and stored for
that signed-in user. The voice worker reads the encrypted credential from
Postgres; it does not read `OPENAI_API_KEY` or a user id from environment
variables.

## Server switching

The browser stores a unique list of Voxos deployment roots in local storage.
Use **Account → Change server** while signed in, or **Change** on the sign-in
screen. Each selection refreshes `GET /api/client-config` and routes API,
streaming, upload, LiveKit token, and MCP OAuth callback traffic through that
server. Changing servers does not sign out another server's Firebase session.

Set `FIREBASE_WEB_APP_ID` and the remaining Firebase client fields on each
backend. The frontend reads these fields from `/api/client-config`; production
builds use the frontend's own origin as their initial server address. Every
selectable backend must allow the hosted frontend origin in its CORS configuration.

For local development, set `VITE_API_BASE_URL` in `frontend/.env.local` when the
API is at a different address. Portable image builds use runtime backend settings.
The optional public Logo.dev key can be supplied by mounting `/runtime-config.js`
with `window.__VOXOS_CONFIG__ = { logoDevPublishableKey: "your-publishable-key" };`.

Sentry error reporting uses `sentryDsn` in the same runtime configuration object,
with optional `sentryEnvironment`, `sentryRelease`, and `sentryTracesSampleRate`
fields. Development can use `VITE_SENTRY_DSN`. Errors are captured at 100% and
performance traces at 1% by default, with tenant payloads removed before
transmission. See [Sentry setup](../docs/wiki/Developer-Setup.md#sentry-error-monitoring)
for deployment configuration and quota controls.
That file contains browser-visible configuration, so keep provider credentials
and application secrets on the backend.

## Connect Modes

- `Token endpoint`: calls a backend endpoint that returns LiveKit credentials.
- `Sandbox server`: uses a LiveKit Cloud sandbox token server ID.
- `Manual token`: uses a LiveKit server URL and a pre-generated participant JWT.

The worker expects room metadata containing the Voxos user key:

```json
{"user_id":"user-123"}
```

The UI sends this as LiveKit agent metadata when using the session API. If you
use manual tokens, generate the token with equivalent room/agent dispatch
metadata for the worker.

## Portable image

Build the web image with `docker build -f Dockerfile.production .` from this
directory. It serves static assets on port `8080` as a non-root user.
The [Helm chart](../helm/README.md) adds the API and Firebase helper proxy routes
and optional runtime public configuration.

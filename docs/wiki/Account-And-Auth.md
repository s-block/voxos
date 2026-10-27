# Account And Auth

Last updated: 2026-10-01

Voxos uses Firebase bearer authentication for browser and API users. The backend
maps each verified Firebase identity to a durable local Voxos user id and uses
that local id for ownership, tenancy, preferences, credentials, LiveKit metadata,
MCP context, and agent data.

## Frontend Sign-In

The selected API supplies Firebase web app configuration through
`/api/client-config`. After sign-in, the frontend calls
`currentUser.getIdToken()` and sends the token as:

```http
Authorization: Bearer <firebase-id-token>
```

The Account page shows the signed-in Firebase email and Firebase user id. It also
provides the location-sharing preference and sign-out action.

## Location Sharing

Location sharing is off by default. Enabling it from Account or the native app
Settings screen immediately asks the browser or operating system for foreground
location permission. The preference is scoped to the signed-in account and
selected Voxos server on that device. Only the enabled preference is stored;
coordinates remain in memory until they are attached to a message or voice
session.

When enabled, web and Flutter request a current location before each text send
and once when a voice session starts. A denied permission, disabled Location
Services, timeout, or unavailable fix does not block the message or call. Voxos
does not request background or always-on location access.

## Backend Auth Contract

Authenticated API routes depend on a verified Firebase bearer token. `/health`
and `/api/client-config` are public. Interactive API documentation and the
OpenAPI schema are available when `API_DOCS_ENABLED=true`, the local default.
Set it to `false` for a public deployment. MCP OAuth callback routes are also
not Firebase-authenticated because external providers redirect browser users
back to Voxos without a Voxos bearer token.

The backend must use `AuthenticatedUser.user_id` as the canonical local user id.
Request bodies, frontend-supplied ids, LiveKit metadata, model output, and tool
arguments are not trusted for user authority.

Firebase authentication records the installed application-default version on
the local user. When that version is missing or outdated, the API enters the
verified user's tenant scope and idempotently provisions the keyless default
Exa MCP record before marking the version complete. A failed or interrupted
initialization remains unmarked and is retried on the next authenticated
request; completed versions add no provisioning check to later requests.
Existing accounts receive the equivalent default and completed version through
database migrations, which preserve an existing Exa record instead of
replacing it.

## Session Revocation

Enable `FIREBASE_AUTH_CHECK_REVOKED=true` after configuring Firebase Admin
Application Default Credentials with the `firebaseauth.users.get` permission
for the selected project. Outside Google Cloud, mount the protected credential
file from a deployment Secret and set `GOOGLE_APPLICATION_CREDENTIALS` to its
container path. The Helm chart supports this through
`application.firebaseAdminCredentialsSecret`, whose Secret contains a
`credentials.json` key. Validate the credentials and permissions before
enabling the setting.

The checker rejects revoked sessions and disabled or deleted accounts with
401. Credential or Firebase service failures reject authentication with 503
and `Retry-After`. Local development defaults to signature and expiry
verification with revocation checks disabled.

## Request Admission

The API limits authenticated write requests and stream requests by the verified
local Voxos user ID. Defaults allow 60 requests per minute with a burst of 20
and at most four concurrent admitted requests per user. An active stream holds
its admission until it ends. Exceeded user limits return 429 with `Retry-After`.

A separate limit admits at most 64 simultaneous HTTP requests per API process,
excluding `/health`; exhausted capacity returns 503 with `Retry-After`.
These budgets are local to each API process. See
[Developer Setup](Developer-Setup.md#api-security-settings) for configuration.

## Local Test Login

The repository includes a local Codex login workflow for development. It reads
the shared local test account from `.env` and `frontend/.env.local`. Do not copy
the test account password, Firebase ID token, refresh token, or authorization
header into docs, logs, screenshots, or commits.

## Tenant Isolation

User-owned data is scoped by the local Voxos user id. API route handlers should
wrap the base session factory with `tenant_scoped_session_factory(current_user.user_id)`.
Workers should derive authority from a persisted RLS-protected run, schedule, or
voice-demand record and use the tenant helpers in `src/voxos/db/tenant.py`. A
LiveKit job may enter tenant scope only after verifying the server-minted
`voxos_route`; plain room, participant, or job `user_id` metadata is not
authority.

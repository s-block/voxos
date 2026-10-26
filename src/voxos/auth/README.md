# Firebase Auth

The auth layer expects Firebase client ID tokens in:

```http
Authorization: Bearer <firebase-id-token>
```

`FirebaseTokenVerifier` validates the token header and payload using cached
signing keys:

- `alg` must be `RS256`.
- `kid` must match a cached Firebase public key.
- The RS256 signature must validate against that key.
- `aud` must equal `FIREBASE_PROJECT_ID`.
- `iss` must equal `https://securetoken.google.com/<project-id>`.
- `exp`, `iat`, `auth_time`, and non-empty `sub` are required.

Firebase public certs are fetched from `FIREBASE_CERTS_URL` and cached in process
using the endpoint's `Cache-Control: max-age` value, capped by
`FIREBASE_PUBLIC_KEY_CACHE_MAX_TTL_SECONDS`. A missing or unknown `kid` triggers
one refresh for key rotation; certs are not fetched on every request.

## User Identity Flow

`FirebaseTokenVerifier` only validates the Firebase ID token. It returns a
`VerifiedFirebaseIdentity` with the Firebase UID and safe profile fields from the
token.

Application code should use `FirebaseAuthDependency`, which maps that verified
Firebase identity to a durable local user record in `auth_firebase_users` and
returns `AuthenticatedUser`:

- `AuthenticatedUser.user_id` is the canonical Voxos app user ID
  (`auth_firebase_users.id`).
- `AuthenticatedUser.firebase_user_id` is the external Firebase UID.
- Voice preferences, encrypted provider credentials, LiveKit metadata, and MCP
  request context should use `AuthenticatedUser.user_id`.

The separate `StatelessFirebaseAuthDependency` only validates Firebase and does
not create or load a local app user. Do not use it for preferences, credentials,
LiveKit token creation, or MCP tools.

## Local Settings

Set at least:

```dotenv
FIREBASE_PROJECT_ID=your-firebase-project-id
```

Build the main dependency with an async DB session factory. The dependency
creates or updates `auth_firebase_users` by unique `firebase_user_id`, then
returns the stable local `id` as `AuthenticatedUser.user_id`.

## MCP

`voxos.mcp.auth.McpAuthContextFactory` accepts the same Firebase bearer token and
resolves the same local `AuthenticatedUser`. This is only authentication and
user matching; authorization policy is deferred.

HTTP MCP servers should treat the bearer token as transport auth, not as a tool
argument. Persona MCP tools derive `AuthenticatedUser.user_id` from the verified
token and must not accept user IDs in tool input. This keeps tool access scoped
to the authenticated user and avoids accidental cross-user updates.

Exposed FastMCP server factories must be built with
`voxos.mcp.auth.McpTransportAuthConfig`. Do not expose a server built with a
static test resolver or without a token verifier. Unit tests may instantiate the
lower-level tool classes with a fake resolver, but HTTP MCP transports must use
transport auth so unauthenticated calls are rejected before tool execution.

For a dedicated remote MCP deployment, use OAuth protected-resource metadata and
resource-bound access tokens for the MCP server URL. The current Firebase bridge
is a reusable verifier adapter for the app's existing user identity; it should
not forward the received token to upstream services.

## Data Migration Note

The schema already has `auth_firebase_users.id`, so no schema change is needed
for the local app user ID. If existing preference or credential rows were seeded
with a Firebase UID in `user_id`, backfill them before deployment by joining
`auth_firebase_users.firebase_user_id` to the old value and replacing it with
`auth_firebase_users.id`.

## Revocation

Set `FIREBASE_AUTH_CHECK_REVOKED=true` in deployed environments to reject tokens
from revoked sessions and disabled or deleted accounts. The checker initializes
a separate Firebase Admin app with `FIREBASE_PROJECT_ID` and runs its synchronous
SDK work in a thread. It checks each authenticated request without caching the
revocation result.

Provide Application Default Credentials with permission to read Firebase Auth
users in that project. On hosts outside Google Cloud, mount a protected
service-account credential file and set `GOOGLE_APPLICATION_CREDENTIALS` to its
container path. Keep that file in a deployment Secret. Credentials and project
permissions must be validated before enabling revocation checks. Backend
credential or service failures return 503 and reject the request; revoked or
disabled identities return 401.

Local development defaults to signature and expiry verification with revocation
checking disabled. See [Firebase session management](https://firebase.google.com/docs/auth/admin/manage-sessions)
and [Admin SDK setup](https://firebase.google.com/docs/admin/setup).

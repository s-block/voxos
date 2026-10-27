# Voxos LiveKit Voice Agent

This package contains the LiveKit voice worker foundation. It loads durable
user voice preferences from Postgres, decrypts the selected user-owned provider
credential through the internal credentials vault, then starts a
provider-backed LiveKit realtime session.

Redis is not used for voice preferences or user provider credentials.

## Local Setup

Install dependencies:

```bash
uv sync --all-extras --dev
```

Start the RLS-safe local Postgres/pgvector service and LiveKit from the root
Compose stack:

```bash
docker compose up -d pgvector livekit
```

The local LiveKit dev server uses `devkey` / `secret`. Do not use
`docker-compose.livekit.yml` for tenant-isolation testing: its plain Postgres
container does not bootstrap the non-superuser application role required for
meaningful RLS checks, and it does not provide the pgvector extension required
by current migrations.

Generate a local AES-GCM key and set it in `.env`:

```bash
python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'
```

Required local values:

```bash
DATABASE_URL=postgresql+asyncpg://voxos:voxos@localhost:5432/voxos
VOICE_ROUTE_SIGNING_SECRET=<long-random-route-signing-secret>
VOICE_AGENT_NAME=voxos-voice-agent
VOICE_WORKER_POLL_INTERVAL_SECONDS=0.25
VOICE_WORKER_START_TIMEOUT_SECONDS=15
VOICE_AGENT_READY_TIMEOUT_SECONDS=20
VOICE_OUTBOUND_ANSWER_TIMEOUT_SECONDS=45
VOICE_OUTBOUND_OPENING_TIMEOUT_SECONDS=20
VOICE_OUTBOUND_OPENING_RETRY_ATTEMPTS=2
VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS=10
VOICE_WORKER_START_CONCURRENCY=8
VOICE_WORKER_LEASE_RENEWAL_CONCURRENCY=16
VOICE_WORKER_DEMAND_TTL_SECONDS=60
VOICE_WORKER_IDLE_TTL_SECONDS=60
VOICE_WORKER_LEASE_TTL_SECONDS=15
VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS=10
VOICE_WORKER_MAX_ACTIVE_PROJECTS=100
VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_SUPERVISOR=16
VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_TENANT=2
VOICE_WORKER_MAX_CALLS_PER_PROJECT=32
VOICE_WORKER_DEMAND_RETENTION_SECONDS=86400
VOICE_WORKER_MAINTENANCE_INTERVAL_SECONDS=60
VOICE_LIVEKIT_ENDPOINT_POLICY=trusted_private
VOICE_USER_METADATA_KEY=user_id
VOICE_TOKEN_TTL_SECONDS=900
VOICE_INACTIVITY_TIMEOUT_SECONDS=30
VOICE_DELETE_ROOM_ON_CLOSE=true
SECRET_ENCRYPTION_BACKEND=local_aes_gcm
LOCAL_SECRET_ENCRYPTION_KEY=<base64-32-byte-key>
```

`trusted_private` is required only for controlled local/self-hosted endpoints
such as the bundled plain-WS LiveKit server. Deployed supervisors should keep
the default `public_wss` policy. The application rejects non-global DNS results,
but DNS can change before the SDK connects; production still needs egress rules
that prevent access to metadata and internal network ranges.

`LOCAL_SECRET_ENCRYPTION_KEY` must be a persistent base64-encoded 32-byte key.
Generate one with `python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"`.
If you delete or rotate it, existing local ciphertext cannot be decrypted.

Run migrations:

```bash
uv run alembic upgrade head
```

For the normal app flow, sign in through the frontend and save your OpenAI key
in the Voice setup panel. The backend encrypts the key into Postgres for that
authenticated user, then the worker reads the encrypted credential at session
start. Do not set `OPENAI_API_KEY` or a user id in runtime env for this path.

Seed one local Firebase user mapping, credential, and preference through the
typed services only if you need a script-driven setup without the frontend:

```bash
uv run python - <<'PY'
import asyncio
import os

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from voxos.auth.models import FirebaseTokenClaims, VerifiedFirebaseIdentity
from voxos.auth.services import FirebaseUserService
from voxos.credentials.services import CredentialsServiceFactory
from voxos.providers.models import RealtimeVoiceProvider
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.user_settings.services import UserCredentialService, UserVoicePreferenceService
from voxos.voice.models import UserVoicePreferences


async def main() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    async with session_factory() as session:
        identity = VerifiedFirebaseIdentity.from_claims(
            FirebaseTokenClaims.model_validate(
                {
                    "sub": os.environ.get("LOCAL_FIREBASE_USER_ID", "local-firebase-user"),
                    "aud": os.environ.get("FIREBASE_PROJECT_ID", "local-firebase-project"),
                    "iss": "local-dev",
                    "auth_time": 1,
                    "iat": 1,
                    "exp": 2,
                },
            ),
        )
        user_record = await FirebaseUserService(session).upsert_from_verified_identity(
            identity=identity,
            last_seen_update_interval_seconds=0,
        )
        credential = await UserCredentialService(session, secret_vault).create_credential(
            user_id=user_record.id,
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI local dev",
            secret=SecretStr(os.environ["USER_OPENAI_API_KEY"]),
        )
        await UserVoicePreferenceService(session).set_preferences(
            UserVoicePreferences(
                user_id=user_record.id,
                provider=RealtimeVoiceProvider.OPENAI_REALTIME,
                model="gpt-realtime",
                voice="marin",
                credential_ref=credential.id,
            ),
        )
        print(f"Seeded local Voxos user_id={user_record.id}")
    await engine.dispose()


asyncio.run(main())
PY
```

`USER_OPENAI_API_KEY` is only used by the seed process. The voice worker reads
the encrypted credential record from Postgres.

LiveKit API keys and secrets are saved per user through the Connection page or
`/api/livekit/settings` so the API can prepare caller/frontend room tokens. Call
setup writes a tenant-owned demand row. The supervisor resolves only that
demand's tenant settings, starts or reuses an isolated project child, and waits
for LiveKit registration before the token can dispatch an agent.

The LiveKit job or room metadata consumed by the worker must include a
server-minted `voxos_route` token. `LiveKitCallRouter` and
`LiveKitVoiceTokenService` create this signed token after Firebase
authentication or verified phone-webhook routing; it binds the local Voxos
user, agent, runtime session, and room:

```json
{"voxos_route":"<server-signed-route-token>"}
```

Plain `user_id` metadata may still identify a participant, but it is not
authority and is never sufficient to start a tenant-scoped worker session.
Application routes must not accept a client-supplied route token or allow
client metadata to override the server-minted route.

The frontend leaves LiveKit streaming off by default. For direct LiveKit API
sessions, LiveKit Agents marks the user as away after
`VOICE_INACTIVITY_TIMEOUT_SECONDS` seconds of voice inactivity and Voxos shuts
down that agent session. Telnyx and Twilio jobs are classified only from the
activated, tenant-scoped demand row and disable this speech-idle timer. A phone
session remains active through conversational silence until its verified media
bridge participant disconnects. `VOICE_DELETE_ROOM_ON_CLOSE=true` asks LiveKit
Agents to delete the room after that participant-driven close or another real
session termination.

Goal-directed outbound phone calls are pre-dispatched. The API persists the
caller-safe objective and worker demand, waits for project registration,
dispatches `VOICE_AGENT_NAME` with only the signed `voxos_route`, and waits for
the worker to start the realtime provider session and write `agent_ready_at`
before carrier dialing. The objective is loaded from the tenant call record;
it is never copied into LiveKit metadata or logs.

The call session supplies a stable expected bridge participant identity.
Outbound Twilio and Telnyx bridge tokens omit `agent_name` so they cannot
dispatch a second job. Provider adapters start silently with audio input
disabled. The worker generates exactly one objective-led opening only after
carrier answer and RoomIO readiness, then enables normal input and barge-in.
The agent introduction belongs only to that first spoken turn; subsequent
replies continue the objective without reintroducing the agent unless the
callee asks or appears confused.
Telnyx `streaming.started` marks media readiness only. Ringing, readiness,
answer/media, and opening waits are bounded by the settings above; terminal
provider state and exhausted opening attempts shut down the prewarmed job.

Run the voice worker with:

```bash
make worker
```

The supervisor does not enumerate configured tenants at startup. Identical
credential sets share an active project child, distinct projects are isolated,
and an unused child remains briefly for burst reuse before draining after
`VOICE_WORKER_IDLE_TTL_SECONDS`. The database call cap is authoritative and the
SDK load threshold is disabled. LiveKit project credentials cross the child
boundary through an anonymous pipe, not argv or the supervisor deployment
environment. The outer child clears SDK environment copies after registration;
each isolated job process receives only its project's values so `JobContext.api`
and RoomIO target the correct project. See
[`docs/livekit-multi-tenant-worker-architecture.md`](../../../docs/livekit-multi-tenant-worker-architecture.md).

Project settings are resolved concurrently up to
`VOICE_WORKER_START_CONCURRENCY`; one slow or invalid tenant is bounded by
`VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS`. Healthy project workers
publish readiness as soon as they register.

Every API and supervisor replica in one environment must use the same
`VOICE_ROUTE_SIGNING_SECRET` and `VOICE_AGENT_NAME`. A mismatched signing secret
changes project fingerprints and invalidates signed call routes; a mismatched
agent name registers capacity under a different dispatch name. The supervisor
also rejects startup unless `VOICE_WORKER_LEASE_TTL_SECONDS` exceeds
`VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS` plus twice
`VOICE_WORKER_POLL_INTERVAL_SECONDS`.

Compose exposes an opt-in `voice-worker` service through the `voice` profile;
`make worker` remains the host-process entrypoint. The tracked deployment
workflow does not provision a production supervisor, so deployed voice requires
a separately managed `python -m voxos.voice.worker` process until that external
deployment wiring is added.

## Deployed Encryption

For AWS KMS envelope encryption, set:

```bash
SECRET_ENCRYPTION_BACKEND=aws_kms_envelope
AWS_KMS_KEY_ID=alias/voxos-user-credentials
AWS_REGION=eu-west-2
```

This mode stores each user API key, OAuth token, private key, or LiveKit
credential in Postgres as ciphertext plus encryption metadata. It does not
create one AWS Secrets Manager secret per user.

The API workload role needs `kms:GenerateDataKey` and `kms:Decrypt` on the
configured KMS key. A separate voice-supervisor workload only resolves existing
secrets and needs `kms:Decrypt`. You can scope policy conditions to non-sensitive
encryption context fields such as purpose/provider/user id shape, but per-user
authorization is enforced by Voxos ownership checks plus authenticated
encryption context, not by one IAM principal or KMS key per end user.

For KMS-compatible local integration testing, keep
`SECRET_ENCRYPTION_BACKEND=aws_kms_envelope` and set
`AWS_KMS_ENDPOINT_URL=http://localhost:4566` for a local emulator such as
LocalStack. Ordinary local development should use `local_aes_gcm`.

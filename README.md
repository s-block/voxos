<p>
  <a href="https://github.com/s-block/voxos/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/s-block/voxos/ci.yml?branch=main&label=ci&style=for-the-badge&logo=githubactions&logoColor=white"></a>
  <a href="https://codecov.io/gh/s-block/voxos"><img alt="Coverage" src="https://img.shields.io/codecov/c/github/s-block/voxos/main?style=for-the-badge&logo=codecov&logoColor=white"></a>
  <a href="https://github.com/s-block/voxos/releases"><img alt="Release" src="https://img.shields.io/github/v/release/s-block/voxos?include_prereleases&sort=semver&style=for-the-badge"></a>
  <a href="https://github.com/s-block/voxos/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/s-block/voxos?style=for-the-badge"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <a href="https://github.com/s-block/voxos/actions/workflows/ci.yml"><img alt="Security checks" src="https://img.shields.io/badge/security-bandit%20%7C%20safety%20%7C%20semgrep%20%7C%20trivy-2ea44f?style=for-the-badge"></a>
  <a href="https://github.com/s-block/voxos/pkgs/container/voxos-oss-api"><img alt="GHCR image" src="https://img.shields.io/badge/image-ghcr.io-24292f?style=for-the-badge&logo=github&logoColor=white"></a>
</p>

# Voxos

Voxos is an open-source backend for user-owned AI agents with voice, text chat,
durable runs, schedules, memory, skills, and tool connectors.

It combines FastAPI, Postgres/pgvector, Firebase bearer auth, encrypted
per-user credentials, LiveKit realtime voice sessions, OpenAI-compatible text
chat streaming, and remote MCP servers. The repository also includes a small
React/Vite console for local testing.

Full user and developer documentation now lives in the GitHub Wiki-compatible
Markdown set under [`docs/wiki/Home.md`](docs/wiki/Home.md). Start there for the
frontend guide, provider keys, LiveKit voice, phone integrations, MCP setup,
agent persistence concepts, and product-doc indexing.

## What Is Included

- FastAPI API for agents, chats, runs, schedules, LiveKit tokens, voice/text
  provider settings, and user MCP servers.
- Demand-driven, tenant-scoped LiveKit voice runtime that starts isolated
  project workers when calls create demand, retains them briefly for burst
  reuse, and resolves encrypted user settings.
- Text chat runtime with server-sent streaming, provider usage records, tool
  calls, transcripts, and durable run history.
- Agent data model for identities, memory, skills, task plans, runtime events,
  schedules, subagents, tool grants, and audits.
- User-owned remote MCP server support over Streamable HTTP and SSE.
- Secret vault backed by Postgres ciphertext, with local AES-GCM for
  development and AWS or Google Cloud KMS envelope encryption for deployments.
- Postgres row-level security helpers and migrations for tenant isolation.
- Docker Compose stack for the API, migration job, LiveKit, pgvector, durable
  agent-run worker, and an opt-in demand-driven voice supervisor.

## Provider Support

| Area | Supported now | Notes |
| --- | --- | --- |
| Voice runtime | OpenAI Realtime, Google Gemini Live, xAI/Grok Voice Agent | Runs through LiveKit Agents realtime plugins. |
| Voice credential storage | OpenAI, Google, xAI/Grok, Hume, ElevenLabs, Mistral | Hume, ElevenLabs, and Mistral storage exists, but worker adapters are not implemented yet. |
| Text runtime | OpenAI, xAI | OpenAI uses streamed `/responses`; xAI uses its compatible `/chat/completions` API. |
| Tools | Internal agent tools and remote MCP tools | Tool access is gated by per-agent grants and effective tool policies. |

## Repository Layout

```text
src/voxos/
  api/              FastAPI app composition and routers
  auth/             Firebase bearer auth and durable local user mapping
  credentials/      User secret vault service
  db/               Async database engine, sessions, tenant/RLS helpers
  voice/            LiveKit token service, settings, worker, voice tools
  providers/        Realtime voice provider adapters
  text/             Text preferences, providers, chat service, storage
  mcp/              User-owned remote MCP server models, auth, clients
  agent_core/       Shared agent tool and instruction contracts
  agent_runtime/    Runs, schedules, sessions, events, tool authorization
  agent_identity/   User agent identity records and services
  agent_memory/     Durable memory models and tools
  agent_skills/     Developer/user skill catalog and rendering
  agent_planning/   Durable task plans and task steps
  persona/          Prompt/persona rendering from agent data
  security/         Secret encryption backends

alembic/            Database migrations
docker/             Local pgvector bootstrap scripts
docs/               Focused operational docs
frontend/           React/Vite local test console
scripts/            Deployment and repo utility scripts
tests/              Python unit and API tests
```

## Prerequisites

- Python 3.12.4 or newer.
- `uv` for Python dependency management.
- Docker and Docker Compose for local Postgres/pgvector, LiveKit, and containers.
- Node.js and npm for the frontend console.
- A Firebase project for authenticated API calls.
- Provider API keys for whichever text or voice providers you want to use.

Install `uv` if needed:

```bash
python -m pip install --upgrade uv
```

## Quick Start With Docker Compose

This is the easiest way to run the backend stack locally. It starts:

- `api`: FastAPI on `http://localhost:7100`
- `migrate`: Alembic migration job
- `livekit`: LiveKit dev server on `ws://localhost:7880`
- `pgvector`: Postgres 16 with pgvector and the non-superuser app role
- `agent-run-worker`: durable runs, including inbound SMS agent replies

1. Create local environment settings:

```bash
cp .env.example .env
```

2. Generate a persistent local encryption key:

```bash
python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'
```

Put the printed value in `.env` as `LOCAL_SECRET_ENCRYPTION_KEY`. Keep this key
stable for your local database; rotating it makes existing local ciphertext
undecryptable.

Generate a separate persistent secret for browser-based MCP OAuth:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Store it in `.env` as `MCP_OAUTH_STATE_SIGNING_SECRET`. Keep it stable across
API restarts and identical across replicas; do not reuse a provider API key or
the local vault-encryption key.

For authenticated API calls, set `FIREBASE_PROJECT_ID` to your real Firebase
project id. The placeholder value is enough to start the app, but real Firebase
ID tokens will not validate against it.

3. Build, reconcile, and verify the complete live-development stack:

```bash
make up
```

`make up` enables the voice and WhatsApp profiles, rebuilds changed images,
runs migrations, recreates services whose image changed, removes obsolete
containers, and waits for health checks. Docker's layer cache keeps unchanged
builds fast. Python source, the frontend, and the WhatsApp bridge source are
bind-mounted. The API reloads, Vite applies HMR, and the bridge recompiles
automatically when their source files change.
The Compose-owned Postgres database is published on host port `55432` by
default, while services use `pgvector:5432` on the Compose network.

4. Check the API:

```bash
curl http://localhost:7100/health
```

Useful Docker commands:

```bash
make up               # build, start, and verify the complete stack
make restart          # rebuild and reconcile the complete stack
docker compose logs -f api agent-run-worker scheduler whatsapp-worker voice-worker frontend ngrok
make db-enable-vector  # enable vector in the configured local DB
docker compose down
docker compose down -v  # destructive: also removes the local Postgres database
```

`make compose-up` and `make compose-restart` remain aliases for the complete
stack commands.

The root `docker-compose.yml` is the preferred local stack. The
`docker-compose.livekit.yml` file is only a minimal helper for LiveKit and a
plain Postgres container; it does not bootstrap the app role used by the root
pgvector setup.

The demand-driven supervisor is available through the opt-in `voice` profile:

```bash
docker compose --profile voice up --build
```

You can instead run `make worker` on the host. In either mode, the saved tenant
LiveKit URL must resolve from both the browser/caller and the supervisor.
`ws://livekit:7880` is container-only, while `ws://localhost:7880` points back
at the worker from inside its container, so use a shared hostname or public
endpoint when running the Compose worker.

### Optional local MCP services

`docker-compose.mcp.yml` runs the official Flightradar24 npm package.
Flightradar24 is stdio-only, so its container uses a pinned Supergateway image
to expose a local HTTP MCP endpoint. This stack joins the root Voxos Compose
network; start the root stack before starting the MCP container.

Set these values in the ignored root `.env` file:

```dotenv
FR24_API_KEY=replace-with-your-flightradar24-key
```

Then start and inspect the stack:

```bash
make mcp-stack-config
make mcp-stack-up
make mcp-stack-logs
```

In the web app, open **MCP**, refresh the catalog, search for
**Flightradar24**, and choose **Local Compose**. Do not add it as a custom
server. The API uses the Compose-only endpoint
`http://flightradar24-mcp:8000/mcp`; the same container is exposed to the host
at `http://127.0.0.1:8181/mcp` for inspection. Override only the host port with
`FR24_MCP_PORT`. Stop the container with `make mcp-stack-down`.

For host-mode development or an additional local model/MCP container, add its
complete URL to `OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON` in `.env`. Use
`localhost` only when the target shares the process network namespace; from a
Compose container, use the target service name or `host.docker.internal`.

To use a separately deployed instance, choose **Deployed HTTPS endpoint** and
enter its complete public HTTPS MCP URL. Any public hostname and path are
accepted when DNS resolves only to global addresses and the URL uses HTTPS on
port 443, so the deployment does not have to use the catalog placeholder or
the default `/mcp` path. Redirects are rejected. The upstream package does not
provide shared-server authentication, so protect a deployed endpoint with
network controls and do not expose it as an unrestricted public service.

The same Compose file builds the stateless Monzo MCP from `../monzo-mcp`.
Voxos owns each user's Monzo OAuth client information, access token, rotating
refresh token, and refresh lock. The MCP container stores none of them. Create
only its separate endpoint bearer outside this repository:

```bash
install -d -m 700 "$HOME/.config/monzo-mcp"
umask 077
openssl rand -hex 32 \
  > "$HOME/.config/monzo-mcp/monzo-mcp-endpoint-token"
```

Set `MONZO_MCP_HOST_ENDPOINT_TOKEN_FILE` only to override that default path.
Configure `MCP_CREDENTIAL_DELEGATION_SIGNING_SECRET` with a long random value;
when omitted locally, Voxos falls back to its OAuth-state signing secret. Start
the root and MCP stacks:

```bash
make up
make mcp-stack-up
```

In **MCP**, choose **Monzo** and **Local Compose**, then enter the contents of
the endpoint-bearer file plus the Monzo OAuth client ID and secret. Register the
displayed Voxos callback URI in the Monzo developer portal, complete browser
sign-in, and approve the connection in the Monzo mobile app. The fixed endpoint
is `http://monzo-mcp:8000/mcp` and is not published to the host.

For each MCP request Voxos sends the endpoint bearer plus a short-lived signed
delegation. The container presents that assertion to the private Voxos broker;
Voxos resolves or refreshes the tenant-owned token under a database row lock and
returns only the usable access token. No tools are granted automatically.
After changing `../monzo-mcp`, run `make monzo-mcp-restart`.

## Local Python Development

Use this mode when you want hot reload, local tests, or direct worker processes.

1. Install dependencies:

```bash
uv sync --all-extras --dev
```

2. Start local dependencies:

```bash
docker compose up -d pgvector livekit
```

3. If the target database does not have the pgvector extension enabled, create it
   once with the configured local Postgres admin role:

```bash
make db-enable-vector
```

4. Run database migrations:

```bash
uv run alembic upgrade head
```

5. Start the API:

```bash
make run
```

The API reads `.env`, disables New Relic locally, and listens on `PORT` from
`.env`, defaulting to `7100`.

To expose the local API to external webhook providers, start an ngrok tunnel in
another terminal:

```bash
make ngrok
```

The target reads `PORT` and `NGROK_URL` from `.env`. Set both `NGROK_URL` and
`API_PUBLIC_BASE_URL` to the same reserved `https://` ngrok URL before
configuring external webhooks. Websocket media stream routes use the same
public host with `wss://`, for example
`wss://<ngrok-host>/api/integrations/streams/communications/twilio/media/<route-key>`.
Pass non-URL ngrok flags when needed with `NGROK_ARGS`.

Agent webhook definitions are created under **Operations → Webhooks**. They use
a per-definition Voxos bearer token and require `make worker-runs`; provider
callbacks that use vendor-specific signatures need a trusted verifying adapter.

5. Start optional workers in separate terminals:

```bash
make worker       # demand-driven multi-tenant LiveKit voice supervisor
make worker-runs  # durable runs, agent webhooks, and inbound message replies
make scheduler    # enqueues due schedules
```

For Telnyx testing, all four directions depend on a stable
`API_PUBLIC_BASE_URL`. Inbound SMS also requires `make worker-runs` plus an
active text provider; calls require `make worker` plus saved LiveKit settings.
The Phone page can Verify number/profile/webhook readiness without changes and
Repair existing Voxos-owned Telnyx routing only after explicit confirmation.
Its recent activity lists expose full phone numbers and safe failure states rather
than message bodies or raw provider payloads.

6. Start the frontend console:

```bash
make frontend
```

The frontend runs at `http://localhost:5173`.

## Frontend Configuration

The frontend uses Firebase client auth and calls the FastAPI backend with
Firebase ID tokens. The selected API supplies Firebase settings through
`/api/client-config`. Add `frontend/.env.local` for a local API address:

```dotenv
VITE_API_BASE_URL=http://localhost:7100
```

After signing in, use the console to save provider keys, choose voice/text
providers, manage tools, open text chats, and test LiveKit voice sessions.
Provider keys are sent once to the backend, encrypted, and stored for the
authenticated user.

The [operator examples](helm/examples/) provide private values and environment
templates for your domain, Firebase project, image registry, storage and ingress
controller. Copy them into the ignored `private/` directory and follow the
[installation guide](helm/README.md#configure-an-installation).

The [Helm chart](helm/README.md) deploys the database and application as separate
Kubernetes releases. Production frontend images use their current origin and
runtime client configuration; private values supply ingress, TLS and Secret
references.

## Environment Variables

Start from `.env.example`. The most important settings are:

| Variable | Purpose |
| --- | --- |
| `PORT` | Local FastAPI port used by `make run` and Compose host mapping. |
| `API_CORS_ALLOW_ORIGINS` | Comma-separated or JSON list of frontend origins. |
| `DATABASE_URL` | Async SQLAlchemy URL. Overrides individual `DB_*` settings. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Database connection pieces when `DATABASE_URL` is not set. |
| `DATABASE_TLS_MODE` | Database transport policy. Local development defaults to `driver-default`; deployed installations should use `verify-full`. |
| `DATABASE_TLS_CA_FILE` | Optional CA bundle used by `verify-full`. |
| `VOXOS_DB_HOST_PORT` | Host port for the Compose-owned Postgres database. Defaults to `55432`; containers always use `pgvector:5432`. |
| `POSTGRES_ADMIN_USER`, `POSTGRES_ADMIN_PASSWORD` | Local Postgres admin credentials used by `make db-enable-vector` to create the pgvector extension in `DB_NAME`. |
| `FIREBASE_PROJECT_ID` | Required audience/issuer for Firebase ID token verification. |
| `VOICE_AGENT_NAME` | LiveKit agent dispatch name. Defaults to `voxos-voice-agent`. |
| `VOICE_WORKER_POLL_INTERVAL_SECONDS` | Demand/worker lifecycle poll interval. Defaults to `0.25`. |
| `VOICE_WORKER_START_TIMEOUT_SECONDS` | Maximum wait for a project worker registration before call setup fails. Defaults to `15`. |
| `VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS` | Per-project bound for loading and decrypting saved tenant LiveKit settings. Must be shorter than the worker start timeout. Defaults to `10`. |
| `VOICE_WORKER_START_CONCURRENCY` | Maximum number of project settings/start workflows resolved concurrently by one supervisor. Defaults to `8`. |
| `VOICE_WORKER_LEASE_RENEWAL_CONCURRENCY` | Maximum concurrent project-lease renewals per supervisor. Defaults to `16`; each renewal has a deadline so one contended project cannot stall unrelated projects. |
| `VOICE_WORKER_DEMAND_TTL_SECONDS` | Crash-safe lease window for a pending or active call demand. Defaults to `60`. |
| `VOICE_WORKER_IDLE_TTL_SECONDS` | Time to retain an unused project registration for burst reuse. Defaults to `60`. |
| `VOICE_WORKER_LEASE_TTL_SECONDS` | Cross-supervisor project ownership lease. Defaults to `15`. |
| `VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | Grace period for project-worker drain and shutdown. Defaults to `10`. |
| `VOICE_WORKER_MAX_ACTIVE_PROJECTS` | Cluster-wide ceiling for live project leases, including starting and idle-retained registrations. Defaults to `100`. |
| `VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_SUPERVISOR` | Per-process ceiling for project children so replicas share cluster demand. Defaults to `16`. |
| `VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_TENANT` | Per-tenant ceiling for distinct live or idle-retained credential fingerprints. Defaults to `2`. |
| `VOICE_WORKER_MAX_CALLS_PER_PROJECT` | Maximum unexpired ready or active calls admitted to one shared project registration. Defaults to `32`. |
| `VOICE_WORKER_DEMAND_RETENTION_SECONDS` | Retention for completed, failed, and expired call-demand audit rows. Defaults to `86400`. |
| `VOICE_WORKER_MAINTENANCE_INTERVAL_SECONDS` | Interval between terminal-demand cleanup passes. Defaults to `60`. |
| `VOICE_LIVEKIT_ENDPOINT_POLICY` | `public_wss` (default) requires WSS and globally routable DNS results. `trusted_private` explicitly permits local/private or plain-WS endpoints for controlled development networks. |
| `VOICE_USER_METADATA_KEY` | Participant metadata key for the durable Voxos user id; it is descriptive, not tenant authority. |
| `VOICE_ROUTE_SIGNING_SECRET` | Route-signing secret for worker dispatch metadata. |
| `MCP_OAUTH_STATE_SIGNING_SECRET` | Persistent deployment secret that signs browser OAuth state for remote MCP servers. |
| `PLATFORM_OPENAI_API_KEY` | Platform OpenAI key for product-doc embeddings and RAG query vectors. |
| `SECRET_ENCRYPTION_BACKEND` | `local_aes_gcm` for local dev, `aws_kms_envelope` for AWS KMS, or `gcp_kms_envelope` for Google Cloud KMS. |
| `LOCAL_SECRET_ENCRYPTION_KEY` | Base64-encoded 32-byte AES key for local secret encryption. |
| `GCP_KMS_KEY_ID` | Full Google Cloud KMS CryptoKey resource ID for `gcp_kms_envelope`. |
| `AWS_KMS_KEY_ID`, `AWS_REGION`, `AWS_KMS_ENDPOINT_URL` | KMS settings when using AWS KMS envelope encryption. |

All API and voice-supervisor replicas in one environment must share the same
`VOICE_ROUTE_SIGNING_SECRET` and `VOICE_AGENT_NAME`. The signing secret protects
both call-route tokens and opaque LiveKit project fingerprints, so rotating it
invalidates outstanding route tokens and pending demands. The configured lease
TTL must exceed the shutdown timeout plus twice the poll interval; the
settings-resolution timeout plus one poll interval must be shorter than the
worker start timeout. Demand TTL must exceed the start timeout plus one poll
interval, and terminal-demand retention must be at least the idle TTL. The
settings model rejects any other combination at startup. Keep worker limits
and lifecycle settings consistent across API and supervisor replicas.

The default endpoint policy rejects plain `ws://` and any hostname resolving to
a loopback, private, link-local, or otherwise non-global address. Local Compose
sets `trusted_private` explicitly for the bundled LiveKit server. This DNS check
is defense in depth, not a complete network boundary: DNS can change before the
SDK connects, so production deployments must also block metadata, VPC, and other
internal ranges with controlled egress.

Use real Firebase and provider credentials for end-to-end flows. Do not commit
`.env`, provider keys, LiveKit secrets, OAuth tokens, private keys, or generated
local encryption keys.

## API Usage

FastAPI docs are available locally at:

```text
http://localhost:7100/docs
```

Authenticated API routes expect:

```http
Authorization: Bearer <firebase-id-token>
```

`/health`, `/docs`, and `/openapi.json` are public. The MCP OAuth callback is
also intentionally unauthenticated because external providers redirect browsers
back without a Voxos bearer token.

Set a shell token for examples. The chat example also uses `jq`.

```bash
export VOXOS_API=http://localhost:7100
export FIREBASE_ID_TOKEN="paste-a-fresh-firebase-id-token"
```

Get or create your default agent:

```bash
curl -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  "$VOXOS_API/api/agents/default"
```

Save and activate an OpenAI text provider key:

```bash
curl -X PUT "$VOXOS_API/api/text/settings/provider-credential" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-...","activate":true}' # pragma: allowlist secret
```

Create a chat and stream a message:

```bash
CHAT_ID=$(
  curl -s -X POST "$VOXOS_API/api/chats" \
    -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"title":"Local test"}' | jq -r .id
)

curl -N -X POST "$VOXOS_API/api/chats/$CHAT_ID/messages/stream" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"What can you do?"}'
```

Save and activate an OpenAI Realtime voice key:

```bash
curl -X PUT "$VOXOS_API/api/voice/settings/provider-credential" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai_realtime","api_key":"sk-...","activate":true}' # pragma: allowlist secret
```

Save LiveKit settings for the authenticated user:

```bash
curl -X PUT "$VOXOS_API/api/livekit/settings" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"livekit_url":"ws://localhost:7880","livekit_api_key":"devkey","livekit_api_secret":"secret"}' # pragma: allowlist secret
```

Mint a LiveKit participant token:

```bash
curl -X POST "$VOXOS_API/api/livekit/token" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"room_name":"voxos-dev","participant_name":"tester","room_config":{"agents":[{"agent_name":"voxos-voice-agent"}]}}'
```

Create a remote MCP server entry:

```bash
curl -X POST "$VOXOS_API/api/mcp/servers" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "label":"Example MCP",
    "url":"https://example.com/mcp",
    "transport":"streamable_http",
    "auth_type":"none",
    "enabled":true,
    "allowed_tools":["example_tool"]
  }'
```

Authenticate the server if its `auth_type` requires it, then call
`POST /api/mcp/servers/{server_id}/test`. Only enabled, connected, successfully
tested MCP servers with explicit `allowed_tools` are exposed to agent sessions.

## Database And Migrations

Voxos uses SQLModel, async SQLAlchemy, Alembic, Postgres, and pgvector. Migrations
live in `alembic/versions`. The app database role is intentionally not a
superuser, so pgvector must be enabled in the target database by bootstrap or an
administrator before migrations that create `vector` columns.

Run migrations:

```bash
uv run alembic upgrade head
```

Create a migration after model changes:

```bash
uv run alembic revision --autogenerate -m "describe change"
```

Review autogenerated migrations before using them. User-owned tables should
include tenant keys, indexes for user access patterns, RLS policies, forced RLS,
and tests proving wrong-user access fails.

When using your own Postgres instance, use a non-superuser app role. Superuser
or bypass-RLS roles invalidate the tenant isolation model.

## Development Commands

```bash
make help              # list available commands
make install-dev       # uv sync plus pre-commit install
make format            # Ruff format and fixes
make lint              # Ruff checks
make mypy              # mypy over src/voxos
make frontend-lint     # ESLint for frontend
make docs-validate     # validate wiki docs and manifest
make docs-index        # index wiki docs into local product-doc search
make secrets           # scan staged files for secrets
make secrets-all       # scan tracked files for secrets
```

Run focused tests while developing:

```bash
uv run pytest tests/api/test_chats.py
uv run pytest tests/voice/test_worker.py
uv run pytest tests/mcp/test_services.py
```

CI runs formatting, linting, mypy, tests with coverage, Bandit, Safety when
configured, Semgrep, Trivy filesystem scans, container builds, and image scans.

## Security Model

- Firebase ID tokens are verified server-side and mapped to durable local Voxos
  user ids in `auth_firebase_users`.
- `AuthenticatedUser.user_id` is the canonical user id for ownership checks,
  database access, LiveKit metadata, MCP context, preferences, and credentials.
- LiveKit jobs derive tenant authority from the server-signed `voxos_route`,
  not from plain `user_id` room, participant, or job metadata.
- User secrets are encrypted through `UserSecretVaultService` and scoped by
  owner, provider, credential id, and purpose.
- LLM/tool calls must derive authority from `AgentRunContext` and
  `ToolExecutionContext`; user ids, tokens, headers, credentials, URLs, and
  other authority-bearing fields are not accepted from model-supplied tool args.
- Postgres RLS uses transaction-local `app.user_id` plus narrow service scopes:
  `agent_worker` for durable runs and `voice_worker` for call demand/leases.
- Remote MCP servers are user-owned and remote-only. Browser users cannot
  register local stdio commands for the worker host.

## Deployment Notes

Voxos has no platform-specific deployment launcher. The API persists voice
demand and durable agent runs in Postgres; ordinary long-lived worker processes
claim that work. A deployment must run the API, `make worker-runs`, `make
worker`, and `make scheduler`, plus `make worker-whatsapp` when WhatsApp is
enabled. All processes must share the same Postgres, vault-encryption, and
signing configuration.

Use `make up` for the complete Docker Compose stack. On a single VM, the same
containers can be kept running with Docker Compose or an equivalent process
supervisor. Run Alembic migrations before starting schema-dependent revisions,
use a least-privileged runtime database role, and enable verified database TLS
for remote production databases.

## Troubleshooting

`LOCAL_SECRET_ENCRYPTION_KEY` is missing or invalid:

Generate a base64-encoded 32-byte key and update `.env`:

```bash
python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'
```

Authenticated API calls return `401`:

Check that the frontend Firebase project and backend `FIREBASE_PROJECT_ID` are
the same project, then request a fresh Firebase ID token.

`LiveKit is not configured`:

Save per-user LiveKit settings through `/api/livekit/settings` or the frontend.
The LiveKit URL, API key, and API secret are stored in the user-scoped database
and secret vault, not in `.env`.

Voice sessions connect but no provider session starts:

Save an active voice provider key and LiveKit settings for the authenticated
user. Make sure `make worker` or the Compose `voice-worker` service is running.
Call setup writes tenant-scoped demand;
the supervisor decrypts only that call tenant's saved project settings, waits
for registration, and then allows dispatch.

Check the API and supervisor logs for the sequence `Requested LiveKit project
worker`, `Assigned LiveKit project worker`, `LiveKit project worker registered`,
and `Prepared LiveKit call route`. The structured `failure_code` field
distinguishes startup timeout, invalid or changed settings, registration
failure, and child-process failure; the wiki troubleshooting page maps the
common codes to operator actions.

The detailed design and LiveKit SDK constraints are in
[Demand-Driven Multi-Tenant LiveKit Workers](docs/livekit-multi-tenant-worker-architecture.md).

MCP OAuth callback fails:

Generate and set a persistent `MCP_OAUTH_STATE_SIGNING_SECRET`, then recreate
the Compose API with
`docker compose up -d --no-deps --force-recreate api` or restart `make run`.
Start the OAuth flow again so a fresh signed state is generated. Hosted OpenBnB
uses dynamic client registration and does not require an OpenBnB client ID or
client secret.

Database access works locally but fails in another Postgres:

Confirm migrations ran, the app role is not a superuser, RLS policies are in
place, and the app connects with the same role that owns runtime traffic.

## Contributing

1. Fork and clone the repository.
2. Create a focused branch.
3. Run `make install-dev`.
4. Make typed, scoped changes with tests close to the behavior you changed.
5. Run `make format`, `make mypy`, relevant `uv run pytest ...` targets, and
   `make frontend-lint` when touching the frontend.
6. Do not commit secrets, local `.env` files, generated encryption keys, or
   provider credentials.

## License

Voxos is licensed under the MIT License. See `LICENSE`.

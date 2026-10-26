# Quick Start

Last updated: 2026-10-26

Use this page to run Voxos locally and reach the frontend console.

## Prerequisites

- Python 3.12.4 or newer.
- `uv` for Python dependency management.
- Docker and Docker Compose for Postgres/pgvector, LiveKit, and containerized
  services.
- Node.js and npm for the React frontend.
- A Firebase project and web app configuration for browser sign-in.
- Provider API keys only for the providers you want to test.

Install `uv` if it is missing:

```bash
python -m pip install --upgrade uv
```

## Docker Compose Stack

The root `docker-compose.yml` is the preferred local stack. It starts:

- FastAPI on `http://localhost:7100`.
- An Alembic migration job.
- LiveKit dev server on `ws://localhost:7880`.
- Postgres 16 with pgvector and a non-superuser Voxos app role.
- The durable `agent-run-worker` used by queued runs and inbound SMS/WhatsApp
  replies.
- A demand-driven voice supervisor under the `voice` profile.
- A multi-tenant WhatsApp linked-device worker under the `whatsapp` profile.

Raw Compose commands keep those profiles opt-in. The canonical `make up`
command enables both so local development has one complete stack.

Create local settings:

```bash
cp .env.example .env
```

Generate a stable local encryption key and store it as
`LOCAL_SECRET_ENCRYPTION_KEY` in `.env`:

```bash
python -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())'
```

Generate a separate persistent signing secret for browser-based MCP OAuth and
store it as `MCP_OAUTH_STATE_SIGNING_SECRET` in `.env`:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Keep this value stable and private. It signs short-lived OAuth state so the
callback can be returned to the correct Voxos user and MCP server. Do not reuse
a provider API key or the local vault-encryption key. All API replicas in one
environment must use the same value.

Generate two separate local browser service credentials and append them to the
ignored `.env` file:

```bash
printf 'BROWSER_AGENT_SERVICE_TOKEN=%s\n' "$(openssl rand -hex 32)" >> .env
printf 'STEEL_SERVICE_TOKEN=%s\n' "$(openssl rand -hex 32)" >> .env
```

Keep these values stable and private. Compose shares the browser-agent token
with its API and worker callers and uses the Steel token for the private
browser proxy. Host-mode API or workers also need
`BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN` set to the browser-agent token.
See [Developer Setup](Developer-Setup.md#browser-runtime-and-account-settings)
for sandbox and queue configuration.

Start the stack:

```bash
make up
```

This rebuilds changed images, recreates services when their image changes,
runs migrations, removes obsolete containers, and waits for health checks.
`make restart` performs the same full reconciliation.

Check the API:

```bash
curl http://localhost:7100/health
```

Useful local commands:

```bash
docker compose logs -f api agent-run-worker scheduler whatsapp-worker voice-worker frontend
make db-enable-vector
docker compose down
docker compose down -v
```

`make db-enable-vector` creates the pgvector extension in the configured local
database with `POSTGRES_ADMIN_USER` and `POSTGRES_ADMIN_PASSWORD` from `.env`.
Compose explicitly keeps `DATABASE_TLS_MODE=driver-default`; remote production
databases should use certificate and hostname verification.

`make up` starts the containerized voice supervisor. You can instead install
the Python dependencies and run `make worker` on the host. A saved LiveKit URL
must resolve from both the caller and supervisor; container-only `livekit` and
container-local `localhost` hostnames are not interchangeable.
The minimal `docker-compose.livekit.yml` helper is not RLS-safe and does not
support current pgvector migrations.

The WhatsApp service started by `make up` contains the pinned Node/Baileys
bridge and uses the same database and secret-encryption configuration as the
API. The Phone page can temporarily
capture a registration SMS received by the selected Twilio/Telnyx resource or
answer a Telnyx verification call, respond to WhatsApp's varying press-a-digit
prompt, and show the spoken code. Arm the selected capture method in Voxos
before requesting the code in WhatsApp Business. For a Telnyx UK mobile, use
the call fallback because ordinary SMS capability does not imply international
OTP reception. Enter the displayed code in WhatsApp Business manually, then
link Voxos from **Linked devices** with the QR or pairing code.

## Local Python Development

Use this mode for hot reload, direct tests, and local worker processes.

```bash
uv sync --all-extras --dev
docker compose up -d pgvector livekit
uv run alembic upgrade head
make run
```

If Alembic reports that the `vector` extension is missing in the target database, run
`make db-enable-vector` once and retry `uv run alembic upgrade head`.
The helper reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `POSTGRES_ADMIN_USER`, and
`POSTGRES_ADMIN_PASSWORD` from `.env` or the shell environment.

Run the host-mode workers you need in separate terminals:

```bash
make worker
make worker-runs
make worker-whatsapp
make scheduler
```

`make worker` runs the demand-driven LiveKit supervisor. It stays unregistered
until a browser or phone call creates tenant-scoped demand, then starts or reuses
an isolated child for that exact LiveKit project. Project credentials are saved
through Connection and stored in Postgres plus the user secret vault, not in
`.env`.

`make worker-runs` is required for durable runs, including an agent reply to an
inbound SMS or WhatsApp message. It is already a default service when using the
root Compose stack. `make worker-whatsapp` builds the locked Node bridge and
runs the shared linked-device worker. `make scheduler` is required only when
due schedules should enqueue runs.

`make run` reads `.env`, disables New Relic locally, and serves the API on
`PORT`, defaulting to `7100`.

## Frontend Console

The selected API provides Firebase client settings at `/api/client-config`,
including `FIREBASE_WEB_APP_ID`. Create `frontend/.env.local` for local API access:

```dotenv
VITE_API_BASE_URL=http://localhost:7100
```

Start the frontend:

```bash
make frontend
```

The console runs at `http://localhost:5173`. Sign in with Firebase, then use the
console to save provider keys, approve tools, open text chats, test voice
sessions, add phone integrations, link WhatsApp Business, and register MCP
servers.

For Kubernetes, copy the values and environment templates from `helm/examples/`
into the ignored `private/` directory, then use the root `helm/` chart. Install its database
release before the application release and supply settings through private
Secrets, including the browser service credentials. Configure network policies
for the cluster and host sandbox profiles for Steel. The production web
frontend calls its current origin. See the repository's `helm/README.md`
deployment guide.

## MCP OAuth

Hosted MCP services such as OpenBnB use the callback derived from
`VITE_API_BASE_URL`, which defaults locally to:

```text
http://localhost:7100/api/mcp/auth/callback
```

OpenBnB does not require an OpenBnB OAuth client ID or client secret. Choose it
from the MCP catalog and select **Start sign-in**. Voxos discovers the provider
OAuth metadata, registers a client dynamically, and uses PKCE. Complete the
provider page, return to Voxos, and let the page discover the tools. If the
browser does not finalize automatically after focus returns, select
**Finalize connection** once.

After adding or changing `MCP_OAUTH_STATE_SIGNING_SECRET`, recreate the Compose
API container so it receives the new environment:

```bash
docker compose up -d --no-deps --force-recreate api
```

Restart `make run` instead when the API is running directly on the host. Start
a fresh provider sign-in after any signing-secret change because previously
issued OAuth state will no longer verify.

## Local Flightradar24 MCP

The Flightradar24 MCP requires a provider API key. Add it to the ignored root
`.env` file:

```dotenv
FR24_API_KEY=replace-with-your-flightradar24-key
```

Start the root Voxos Compose stack first, then start the separate MCP Compose
file:

```bash
make mcp-stack-config
make mcp-stack-up
```

Open **MCP**, refresh the catalog, search for **Flightradar24**, and choose
**Local Compose**. The catalog choice uses the internal endpoint
`http://flightradar24-mcp:8000/mcp`; do not add the host port as a custom
server. Use `make mcp-stack-logs` to inspect it and `make mcp-stack-down` to
stop it.

For an independently hosted instance, choose **Deployed HTTPS endpoint** and
enter its complete public HTTPS MCP URL. Custom public hostnames and paths are
supported. Private, loopback, credential-bearing, and non-HTTPS deployment
URLs are rejected.

## Local Monzo MCP

The Monzo integration builds the stateless `../monzo-mcp` container. Create one
separate local MCP endpoint bearer:

```bash
install -d -m 700 "$HOME/.config/monzo-mcp"
umask 077
openssl rand -hex 32 \
  > "$HOME/.config/monzo-mcp/monzo-mcp-endpoint-token"
```

Set `MONZO_MCP_HOST_ENDPOINT_TOKEN_FILE` only when overriding that path. Set a
long random `MCP_CREDENTIAL_DELEGATION_SIGNING_SECRET` in `.env`; local setups
fall back to `MCP_OAUTH_STATE_SIGNING_SECRET` when it is absent. Then run:

```bash
make up
make mcp-stack-up
```

Open **MCP**, choose **Monzo** and **Local Compose**, then enter the contents of
the endpoint-bearer file and your Monzo OAuth client ID and secret. Register the
displayed callback URI in the Monzo developer portal, open the authorization
URL, and approve the connection in the Monzo mobile app.

Voxos stores and refreshes the Monzo tokens. The container validates its own
bearer and exchanges a short-lived `X-MCP-Credential-Delegation` assertion at
the private Voxos broker for a request-lived access token. Test discovery and
grant only required read tools. The preset grants nothing automatically and
the Compose service disables financial writes.

## Public Webhooks

Phone webhooks, agent webhooks called from outside the local machine, and OAuth
providers that reject loopback callbacks need a public HTTPS API base URL. For
local development, use:

```bash
make ngrok
```

Set `API_PUBLIC_BASE_URL` to the same `https://` ngrok URL before configuring
Twilio, Telnyx, or an external agent-webhook caller. The Operations **Webhooks**
tab otherwise shows a relative delivery path that can still be used by callers
on the same origin. When an MCP OAuth provider requires a public callback, also
set the frontend's `VITE_API_BASE_URL` to that public API origin because the
frontend constructs the MCP redirect URI from that value. Websocket media
stream routes use the same public host with `wss://`.

Before a Telnyx smoke test, open Phone, expand the connected number, and use
**Verify**. It checks provider number/profile assignments, current public
webhooks, outbound destination configuration, text-run prerequisites, and
LiveKit routing without changing Telnyx. Use **Repair** only after reviewing the
failed checks and confirming a provider-side update. Test inbound SMS, outbound
SMS, inbound calls, and outbound calls separately with approved endpoints; the
recent activity panel shows full numbers and safe final/failure states.

# Developer Setup

Last updated: 2026-10-29

This page collects development commands and repo conventions for contributors.

## Install

```bash
uv sync --all-extras --dev
```

Frontend dependencies:

```bash
cd frontend
npm install
```

The WhatsApp bridge requires Node.js 20 or newer and uses its own locked
dependency tree:

```bash
make whatsapp-bridge-install
```

## Local Services

```bash
docker compose up -d pgvector livekit
uv run alembic upgrade head
make run
make frontend
```

Root Compose lets the API and durable run worker reach a model server on the
host as `host.docker.internal`. Host-mode development can use `localhost` or
another local address. Add the complete base URL to
`OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON`; trust is exact by endpoint purpose,
host, port, and path. See [Self-Hosted Models](Self-Hosted-Models.md).

Host-mode chat images default to `.voxos/attachments`, which is gitignored and
persistent across API restarts. Root Compose sets
`ATTACHMENT_LOCAL_PATH=/var/lib/voxos/attachments` and mounts the same named
volume into the API and durable run worker. Do not use `/tmp` when separate
processes must read the same staged image.

The main attachment settings are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ATTACHMENT_STORAGE_BACKEND` | `local` | `local` for host/Compose development or `s3` for deployed tasks. |
| `CHAT_IMAGE_INPUT_ENABLED` | `true` | Shared web/Flutter image-input rollout switch; model capability and attachment authorization still apply. |
| `ATTACHMENT_LOCAL_PATH` | `.voxos/attachments` | Persistent private local object root. |
| `ATTACHMENT_S3_BUCKET` | unset | Required private bucket when the backend is `s3`. |
| `ATTACHMENT_S3_PREFIX` | `chat-images` | Object-key prefix covered by task-role IAM. |
| `ATTACHMENT_MAX_SOURCE_BYTES` | `26214400` | Maximum streamed source upload. |
| `ATTACHMENT_MAX_IMAGE_BYTES` | `5242880` | Maximum normalized full image. |
| `ATTACHMENT_MAX_TURN_BYTES` | `20971520` | Maximum normalized bytes in one turn. |
| `ATTACHMENT_MAX_IMAGES_PER_TURN` | `4` | Maximum ordered images in one user turn. |
| `ATTACHMENT_STAGING_TTL_SECONDS` | `86400` | Cleanup age for unclaimed staging records. |
| `ATTACHMENT_MAX_DELETION_ATTEMPTS` | `5` | Maximum physical-deletion attempts before operator review. |
| `ATTACHMENT_DELETION_RETRY_INTERVAL_SECONDS` | `60` | Minimum interval between durable-run worker scans for incomplete object deletion. |
| `ATTACHMENT_MAX_HISTORY_IMAGES` | `12` | Image count retained in provider history. |
| `ATTACHMENT_MAX_HISTORY_BYTES` | `41943040` | Normalized media-byte history budget. |

When deployed containers do not share durable storage, configure the supported
S3 backend with a private bucket and grant the API/run-worker identity only
ListBucket on the configured prefix plus GetObject, PutObject, and DeleteObject
below it.

When New Relic is active, image ingestion emits privacy-safe custom metrics and
events for success count, client source, normalized-size bucket, validation
failure code, object-store latency, and provider image count. These signals do
not include tenant IDs, filenames, hashes, captions, object keys, or image
content.

Staged rows older than the configured TTL are processed when that tenant next
uploads an image. Logical deletion records physical-deletion completion
separately, and the durable run worker scans incomplete object deletions at the
configured interval across tenants through an attachment-only RLS service
scope. API access is revoked as soon as the attachment or its parent chat is
marked deleted. Failed object deletion is bounded by
`ATTACHMENT_MAX_DELETION_ATTEMPTS`; records left incomplete after that threshold
require operator review.

## API Security Settings

Configure these settings in the API environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_DOCS_ENABLED` | `true` | Serves interactive documentation and the OpenAPI schema. Set to `false` for a public deployment. |
| `API_RATE_LIMIT_PER_MINUTE` | `60` | Per-user refill rate for authenticated write and stream requests. |
| `API_RATE_LIMIT_BURST` | `20` | Maximum immediately available write/stream admissions per user. |
| `API_MAX_IN_FLIGHT` | `64` | Maximum simultaneous HTTP requests per API process, excluding `/health`. |
| `API_MAX_USER_IN_FLIGHT` | `4` | Maximum simultaneous admitted write/stream requests for one verified local user. |
| `FIREBASE_AUTH_CHECK_REVOKED` | `false` | Adds Firebase Admin checks for revoked sessions and disabled or deleted accounts. |

User admission failures return 429; exhausted process capacity returns 503.
Both include `Retry-After`. Streaming responses hold their admission for the
response lifetime. Budgets are per process, so capacity planning must account
for the number of API replicas and worker processes.

Enable revocation checks only after validating Application Default Credentials
with the project's `firebaseauth.users.get` permission. A mounted credential
file uses `GOOGLE_APPLICATION_CREDENTIALS`; the Helm chart can mount a Secret
through `application.firebaseAdminCredentialsSecret`. Keep this credential
outside source control. See [Account And Auth](Account-And-Auth.md).

## Browser Runtime And Account Settings

Root Compose starts a private Stagehand task service and Steel Chromium.
Configure separate random `BROWSER_AGENT_SERVICE_TOKEN` and
`STEEL_SERVICE_TOKEN` values in the ignored `.env` file. Compose supplies the
browser-agent token to API and worker callers as
`BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN`; host-mode callers must set that
variable to the same token themselves. The task service requires both
credentials at startup. Steel's proxy uses its separate credential for HTTP
and DevTools access.

The services have no published ports. A task creates an ephemeral browser,
and execution is serialized to match Steel's single-session self-hosted
runtime. The queue accepts at most four waiting tasks by default, expires a
queued request after 30 seconds, and includes queue time in the 570-second
service task deadline. Excess or expired admissions return 429 with
`Retry-After`. `MAX_QUEUED_TASKS`, `QUEUE_WAIT_TIMEOUT_MS`, and `TASK_TIMEOUT_MS`
override those service settings. A canceled queued request is removed before
a browser starts.

Voxos passes the agent's instruction, public start boundary, action policy,
and the requesting user's currently selected text model transport for each
task. Do not add a separate model key or website workflow to the service
environment.

Public browsing uses this runtime without a saved profile. A Browser Account is
an optional server-side profile that contributes authorized origins and either
vault credentials or a hosted managed-profile reference to the same general
task contract.

Browserless and Browser Use Cloud remain optional hosted providers. They require
an enabled, trusted, connected MCP connection with static bearer or API-key
header authentication respectively. The Browser Account service reuses that
encrypted MCP provider token rather than storing a second copy. Define the
variables below only when overriding an application default. Browser Use
accounts have a two-letter browser country that defaults to `GB`; this is
application data, not an environment setting. Browserless hosted-agent accounts
use the configured fleet endpoint and do not expose a per-account country.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BROWSER_ACCOUNTS_ENABLED` | `true` | Optional saved-profile rollout or incident kill switch. Set to `false` to disable Browser Account metadata, setup, authorization, reconciliation, and signed-in execution. |
| `BROWSER_ACCOUNTS_SETUP_TTL_SECONDS` | `900` | Maximum manual sign-in/setup lease and live-session duration. |
| `BROWSER_ACCOUNTS_AUTHORIZATION_TTL_SECONDS` | `600` | Lifetime of one unconsumed chat authorization. |
| `BROWSER_ACCOUNTS_TASK_TIMEOUT_SECONDS` | `600` | Overall provider task wait limit enforced by Voxos. |
| `BROWSER_ACCOUNTS_CLEANUP_TIMEOUT_SECONDS` | `15` | Bounded provider-session stop window used in cleanup. |
| `BROWSER_ACCOUNTS_TASK_LEASE_SECONDS` | `720` | Cross-replica exclusive task lease. Validation requires it to exceed the task timeout, cleanup timeout, and two polling intervals; execution heartbeats extend it. |
| `BROWSER_ACCOUNTS_MAX_TASK_STEPS` | `40` | Provider step and cost budget for one browser task. |
| `BROWSER_ACCOUNTS_MAX_OUTPUT_CHARACTERS` | `12000` | Maximum provider text copied into the tool result. |
| `BROWSER_ACCOUNTS_POLL_INTERVAL_SECONDS` | `1.0` | Provider task-status polling interval. |
| `BROWSER_ACCOUNTS_BROWSERLESS_BASE_URL` | `https://production-lon.browserless.io` | Optional Browserless hosted-agent fleet origin override. Voxos connects to its `/chromium/cli` endpoint using only the configured API token. |
| `BROWSER_ACCOUNTS_STAGEHAND_BASE_URL` | `http://127.0.0.1:3100` | Private local Stagehand adapter URL for host-mode development. Root Compose supplies `http://browser-agent:3100` internally. |
| `BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN` | unset | Required bearer credential matching the private task service's `BROWSER_AGENT_SERVICE_TOKEN`. |

Provider execution heartbeats its exact task lease and observes durable account
revocation. Browserless revocation waits for the executor that owns the
WebSocket to stop it and acknowledge lease release before credentials are
deleted. Expired leases are fenced and reconciled before another task can take
ownership. The API also performs best-effort reconciliation during Browser
Account list operations, marks stale setups, retries remote deletion, and
records privacy-safe New Relic lifecycle signals and deletion-backlog counts.
At larger volume the list-triggered maintenance should move to a dedicated
bounded scheduler job without adding a cross-tenant vault bypass.

The browser runtimes cannot enforce an approval immediately before an agent
submits a consequential external action. Browserless receives reviewed origins
as instructions rather than a provider-enforced network policy. Browser tasks
default to `read_only`; `state_changes_allowed` is reserved for actions the user
explicitly requested, but it is not a secure transaction approval mechanism.
Follow the rollout and incident requirements in
[Browser Accounts](Browser-Accounts.md).

The local adapter pins Stagehand 3.7 because it is the Stagehand release line
that exposes the autonomous `agent()` API used by this workflow. Build Steel
from `helm/images/steel` and pin deployed images by digest. Its entrypoint
requires a non-root user and the Chromium sandbox enabled. On Linux, configure
host seccomp and AppArmor profiles that permit Chromium's user-namespace
sandbox operations; Kubernetes references them through
`browser.seccompProfile` and `browser.appArmorProfile`. See the repository's
`helm/README.md` deployment guide for host profile configuration.

Update either component deliberately, review its browser/session and agent API
contracts, then rebuild and run the Browser Account integration tests. Local
account isolation depends on ephemeral sessions and vault-backed credential
injection. The adapter's CDP guard constrains
top-level page and popup navigation to the account's reviewed origins while
allowing the page's required cross-origin subresources. Steel logging is kept
at warning level because its browser instrumentation can otherwise include
full page and request URLs.

If the target local database reports that the `vector` extension is missing
during migration, run this once with the configured Postgres admin role before
retrying Alembic:

```bash
make db-enable-vector
```

The helper reads `DB_HOST`, `DB_PORT`, `DB_NAME`, `POSTGRES_ADMIN_USER`, and
`POSTGRES_ADMIN_PASSWORD` from `.env` or the shell environment.

Optional workers:

```bash
make worker
make worker-runs
make worker-whatsapp
make scheduler
```

`make worker` runs the demand-driven LiveKit supervisor against the RLS-safe
root Postgres/pgvector service. The minimal `docker-compose.livekit.yml` helper
uses a plain Postgres bootstrap user and is not suitable for tenant-isolation or
current migration testing.

Alternatively, `docker compose --profile voice up --build` starts the opt-in
containerized supervisor. Use a saved LiveKit hostname reachable from both the
caller and worker container.

`make worker-whatsapp` runs the multi-tenant WhatsApp supervisor. It first
builds `whatsapp-bridge/dist/index.js`, then points the Python worker at the
host's `node` executable and that compiled script. The worker must share the
API's Postgres and secret-encryption configuration. The bridge is a private
stdio child; do not run it as a public service or add a filesystem auth store.
For host mode, the Make target defaults its health file to
`${TMPDIR:-/tmp}/voxos-whatsapp-worker-<uid>.json` unless
`WHATSAPP_HEALTH_PATH` is already set.

The container image includes a pinned Node runtime plus production-only bridge
dependencies. Start and verify the complete Compose stack with:

```bash
make up
```

This enables the voice and WhatsApp profiles, rebuilds changed images, runs
migrations, recreates changed services, and waits for health checks. The
WhatsApp container exits after a sustained unhealthy polling window so its
Compose restart policy can replace the process. For a complete host-mode
WhatsApp test, run the API, `make worker-runs`, `make worker-whatsapp`, and the
frontend.

Local MCP services use the separate `docker-compose.mcp.yml` file and the root
`voxos_default` network:

```bash
make mcp-stack-config
make mcp-stack-up
make mcp-stack-logs
```

The Flightradar24 service wraps its stdio package. The Monzo service builds
`../monzo-mcp`; that image exposes only authenticated native Streamable HTTP.
It mounts only an owner-only MCP endpoint bearer. Provider OAuth, encrypted
token storage, refresh, and refresh locking stay in Voxos. The MCP calls the
private Voxos credential-broker route with a short-lived signed delegation.
The MCP port remains private to the Compose network.

WhatsApp setup has four operational stages with different dependencies:

| Stage | Required runtime |
| --- | --- |
| Register the Voxos number by SMS | API, Postgres/vault, public signed Twilio or Telnyx SMS webhook. |
| Register the Voxos number by call | API, Postgres/vault, public signed Telnyx voice webhook and Telnyx transcription events. This call bypasses LiveKit and the normal voice agent. |
| Link and restore the companion device | API plus `whatsapp-worker` and its private Node bridge. |
| Receive a message and generate a reply | Connected `whatsapp-worker`, `agent-run-worker`, and an active tenant text provider. |

The API must be running when the SMS or call arrives. Start the registration
window in Voxos before requesting the code in WhatsApp Business so the signed
provider event is correlated to the exact tenant integration and resource. A
Telnyx verification call can contain a preliminary “press digit” prompt. The
webhook handler derives the requested digit from each final transcript instead
of configuring a fixed DTMF value, then captures the later variable-format
spoken code. Keep Telnyx `call.transcription` events enabled on the Voxos-owned
voice route.

## WhatsApp Worker Settings

Defaults are suitable for local development. Override only when deployment
capacity or lifecycle timing requires it.

| Variable | Default | Purpose |
| --- | --- | --- |
| `WHATSAPP_ENABLED` | `true` | Enables new link, challenge, and relink operations and worker processing. |
| `WHATSAPP_BRIDGE_EXECUTABLE` | `node` | Node executable used for the private bridge child. Compose sets `/usr/local/bin/node`. |
| `WHATSAPP_BRIDGE_SCRIPT` | `/app/whatsapp-bridge/dist/index.js` | Compiled bridge entrypoint. `make worker-whatsapp` overrides this with the repository path. |
| `WHATSAPP_WORKER_POLL_INTERVAL_SECONDS` | `0.5` | Demand and outbox reconciliation interval and independent lease-renewal cadence. |
| `WHATSAPP_WORKER_LEASE_TTL_SECONDS` | `30` | Database ownership lease for a linked session. |
| `WHATSAPP_WORKER_SHUTDOWN_TIMEOUT_SECONDS` | `10` | Graceful bridge/session shutdown deadline. |
| `WHATSAPP_BRIDGE_START_TIMEOUT_SECONDS` | `15` | Bridge process and protocol-handshake startup deadline. |
| `WHATSAPP_BRIDGE_REQUEST_TIMEOUT_SECONDS` | `20` | Command and durable event RPC deadline. |
| `WHATSAPP_BRIDGE_RESTART_GRACE_SECONDS` | `5` | Delay between bridge/session recovery attempts. |
| `WHATSAPP_MAX_SESSIONS_PER_WORKER` | `25` | Maximum concurrent tenant sockets multiplexed by one worker. |
| `WHATSAPP_CHALLENGE_TTL_SECONDS` | `120` | Maximum QR or pairing-code lifetime accepted from the bridge. |
| `WHATSAPP_API_CHALLENGE_WAIT_SECONDS` | `10` | How long a challenge request waits for the worker before returning `challenge_not_ready`. |
| `WHATSAPP_IPC_MAX_FRAME_BYTES` | `1048576` | Exact maximum newline-delimited bridge frame size; must match the bridge handshake. |
| `WHATSAPP_HEALTH_PATH` | `/var/run/voxos/whatsapp-worker-health.json` | Mode-`0600` worker health file used by the container healthcheck; the host Make target chooses a writable temp path by default. |
| `WHATSAPP_HEALTH_FRESHNESS_SECONDS` | `10` | Maximum age of worker health and successful poll timestamps. |
| `WHATSAPP_WORKER_RESTART_AFTER_SECONDS` | `60` | Sustained failed or stalled polling window before the worker exits for orchestration restart. |

Settings validation requires the lease TTL to exceed the larger of the bridge
request and shutdown timeouts plus two poll intervals. Restart grace must be
shorter than the lease TTL, health freshness must exceed the poll interval, and
the worker restart window must exceed both health freshness and the bridge
request timeout. The configured session cap must also fit the bridge's
handshake capacity.

## Deployment Processes

The root `helm/` chart installs PostgreSQL/pgvector and the
application as separate releases. Copy the operator values and environment
templates from `helm/examples/` into the ignored `private/` directory; the
repository's `helm/README.md` installation guide describes
domains, Firebase registrations, database roles, TLS, storage and ingress settings. Its migration hook runs after the database
and referenced Secrets are ready. Configure private ingress/TLS values and
encrypted application settings outside the chart. A Flux installation can load
private chart values with `HelmRelease.valuesFrom` and SOPS decryption.

Keep production promotion workflows in the private GitOps repository and use
GitHub Actions environment secrets for private URLs and credentials. Promote
application images by digest after validation. Set
`application.env.DEPLOYMENT_REVISION` to the source commit to verify the API and
frontend release through their `X-Voxos-Revision` response headers.

Configure `networkPolicy.enabled` for both releases with the ingress
controller and DNS pod/namespace selectors for the cluster. Set matching
application and database release names. Public egress is an explicit setting;
exclude node public addresses and other installation-specific infrastructure
through private `networkPolicy.publicEgress.additionalDeniedCIDRs` values.
Kubernetes NetworkPolicy enforcement must be available in the cluster.

Create the browser service Secret before installing the application. Its
`service-token` authenticates API/worker callers to the task service and its
separate `steel-api-key` authenticates the task service to Steel. Application
containers use non-root users, read-only root filesystems, and ephemeral
writable temporary directories. Database data and attachments use their
configured persistent volumes.

For macOS Kubernetes tests, k3d runs K3s inside a Docker-compatible runtime.
Choose the same K3s version as the deployment, keep kubeconfigs separate, and
use localhost port forwarding. Rehearse restorations with workers at zero
replicas and an egress policy before enabling live integrations.

Portable frontend builds obtain Firebase configuration from the selected API
and use their current origin. Configure the backend's `FIREBASE_WEB_APP_ID`;
build-time Firebase variables are unnecessary. For iOS push notifications, download the
installation's `GoogleService-Info.plist` from Firebase and place it at
`app/ios/Runner/GoogleService-Info.plist`, which is ignored by Git. Configure
`VOXOS_IOS_BUNDLE_ID` and `VOXOS_APPLE_TEAM_ID` through the ignored
`app/ios/Flutter/Installation.xcconfig`. Android uses
`VOXOS_ANDROID_APPLICATION_ID` in ignored `app/android/installation.properties`.
See the mobile guide in `app/README.md` for native registration
and signing setup.

The API only persists durable agent runs and voice demand. It does not start or
manage deployment infrastructure. Keep the agent-run worker, voice supervisor,
and scheduler running alongside the API using Docker Compose, systemd, or the
deployment platform's ordinary process supervision. Add the WhatsApp worker
when linked-device sessions are enabled and the Codex worker when containerized
Codex tasks are enabled. All processes must share the same database,
vault-encryption, and signing configuration.

Production application services should receive a pooled database URL for a
least-privileged runtime login. The migration process alone should receive the
direct migration/owner URL. Do not use the database project owner, a table
owner, or a `BYPASSRLS` role as the runtime login. Local and Compose development
leave `DATABASE_TLS_MODE=driver-default` so local Postgres remains usable;
remote production databases should use `verify-full` with an appropriate CA
bundle. Run `python -m voxos.db.runtime_readiness` with the proposed runtime URL
before deployment; it exits nonzero when the role crosses the supported
ownership, DDL, Alembic, or RLS privilege boundary.

## Sentry Error Monitoring

Configure separate Sentry projects for the Python API/workers, React frontend,
and Flutter app. SDK reporting starts when that component has a DSN.
Errors use a 100% sample rate; performance traces default to 1%. Backend health
checks are excluded from tracing. The Python API, agent-run worker, scheduler,
WhatsApp worker, Codex worker, voice supervisor, registration children, and
voice job processes share the backend project and carry a service tag.

For the API and workers, set these installation-owned environment variables:

```dotenv
SENTRY_DSN=<backend-project-dsn>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.01
SENTRY_RELEASE=<release-or-commit>
```

`SENTRY_RELEASE` defaults to `DEPLOYMENT_REVISION` when configured. Host-mode
development reads `.env`; Compose passes that file to application processes.
Kubernetes operators can supply the same settings through `application.env`
or the existing application Secret.

For the production frontend, include the public web-project DSN in the
installation's `runtime-config.js` referenced by `frontend.publicConfigSecret`:

```javascript
window.__VOXOS_CONFIG__ = Object.freeze({
  sentryDsn: "<web-project-dsn>",
  sentryEnvironment: "production",
  sentryRelease: "<release-or-commit>",
  sentryTracesSampleRate: 0.01,
});
```

The same object can contain `logoDevPublishableKey`. Development can use
`VITE_SENTRY_DSN` in `frontend/.env.local`. Production assets use runtime
configuration, so one image can serve installations with different projects.

For Flutter, copy `app/sentry.config.example.json` to the ignored
`app/sentry.config.json` and set `SENTRY_DSN` to the mobile-project DSN. Mobile
Make targets and the TestFlight build script pass this file as Dart defines.
Direct Flutter commands use `--dart-define-from-file=sentry.config.json` from
`app/`. The environment defaults to `production` for release builds and
`development` otherwise; add `SENTRY_ENVIRONMENT` to override it. Flutter uses
the package version and build number as its release identifier.

Reporting retains exception types, stack locations and sampled operation
timings. Privacy filters remove exception messages, request data, user data,
custom extras, breadcrumbs and span payloads before transmission. Bulk logs,
session recordings, screenshots and profiling are disabled. Python SDK
integrations are explicit so installed AI-provider SDKs do not enable prompt
or response collection automatically.

Set error rate limits under each project's **Client Keys (DSN)** settings and
keep **Spike Protection** enabled. A budget of 100 backend errors and 25 errors
for each client per day totals at most 4,650 accepted errors in a 31-day month.
Events above each daily cap are dropped; trace sampling is controlled
separately by the SDK. Choose limits that fit the organization's current
allowance. Verify delivery with one synthetic exception in a development
environment and check the matching project's issue feed.

For readable release stack traces, upload web source maps and mobile debug
symbols using [Sentry's source-map tools](https://docs.sentry.io/platforms/javascript/sourcemaps/)
and [Flutter debug-symbol guidance](https://docs.sentry.io/platforms/dart/guides/flutter/debug-symbols/).
Keep upload authentication tokens in private build configuration.

## Quality Checks

Run focused tests while changing behavior:

```bash
uv run pytest tests/product_docs
uv run pytest tests/agent_core/test_internal_tools.py
uv run pytest tests/agent_runtime/test_tool_authorization.py
```

Required final checks after code changes:

```bash
make format
make lint
make mypy
make pre-commit
```

Frontend checks:

```bash
cd frontend && npm run test
make frontend-lint
```

WhatsApp bridge checks:

```bash
make whatsapp-bridge-check
```

That target installs from `package-lock.json`, then runs ESLint, TypeScript
typechecking, the bridge build, and Node tests.

Docs checks:

```bash
make docs-manifest
make docs-validate
make docs-refresh-check
make docs-index
```

`make docs-index` writes the product documentation index to the configured local
database. It requires migrations, database connectivity, and an embedding API
key unless run through a dry-run path. Use `PLATFORM_OPENAI_API_KEY` for
product-doc embeddings. `make docs-refresh-check` performs the corresponding
dry run without writing the live index.

## Migrations

Use Alembic autogenerate after model changes:

```bash
uv run alembic revision --autogenerate -m "describe change"
```

Review generated migrations before committing them. User-owned tables need RLS
policies and wrong-user tests. Global product-doc tables do not store tenant data
and do not need tenant RLS.

## Deployment Safety

Review migrations, configuration, runtime database privileges, and worker
health before routing production traffic to a new application revision.

## Public source checks

`make publication-check` rejects installation files even when force-added to
Git, source symlinks, and Python lockfiles using a private package index.
`make pre-commit` runs this check together with formatting, typing, chart,
frontend and secret checks. Keep environment files, Terraform state, keys,
local attachments, database dumps and recovery archives in ignored storage.
Sanitized `.example` files remain publishable. Application dependencies resolve
from public PyPI with `uv sync --frozen`; image builds need no private index.

CI audits the locked Python and JavaScript runtime and development dependencies
using `pip-audit` and `npm audit`. The dependency audit requires no maintainer
credentials and gates API image publication. Published API, frontend, browser
service and Steel images also receive Trivy runtime vulnerability checks,
including packaged Java libraries and operating-system packages. Review
security updates with the SDK integration tests and rebuild the affected
images before release.

The `Secret scan` CI check fetches complete branch, tag and historical
pull-request heads, then runs pinned Gitleaks with archive decoding and
redacted output. Its exemptions identify exact synthetic values or hash
formats and include regression tests for detecting new credentials in the
same files. Report vulnerabilities through the private reporting channel in
the repository's `SECURITY.md`.

Published API and Steel images include source archives and license notices
under `/usr/share/voxos/source`. Steel also exposes its source directory at
`/source/` on the service HTTP port. The API's WhatsApp program is distributed
under GPLv3 terms and the Steel/MuPDF service under AGPLv3 terms; applicable
MIT, Apache and LGPL notices remain with their components. See
[Third-party software and source](https://github.com/s-block/voxos/blob/main/THIRD_PARTY_NOTICES.md) for access and
redistribution instructions. Docker builds verify upstream source checksums;
dependency-source versions are checked against the lockfiles and installed
native bundle metadata. The API source directory includes libvips component
archives, upstream build patches and locked librsvg Rust dependency sources.

The Python integrations use LiveKit Agents 1.8, MCP SDK 2 and HTTPX 2 for SDK
transports. Exposed MCP servers are built with `mcp.server.MCPServer` and the
Firebase token verifier; configure Streamable HTTP options on
`streamable_http_app()`. The application's other HTTP integrations use HTTPX.

# Architecture And Security

Last updated: 2026-10-29

Voxos is organized by product capability under `src/voxos/`. FastAPI app
composition lives in `src/voxos/api/main.py`, and module routers call typed
services rather than embedding business logic in route handlers.

## Main Packages

| Package | Purpose |
| --- | --- |
| `api` | FastAPI routers and app composition. |
| `auth` | Firebase bearer verification and durable local user mapping. |
| `credentials` | User secret vault service. |
| `db` | Async engine/session setup and tenant/RLS helpers. |
| `voice` | LiveKit tokens, settings, worker, and voice tools. |
| `providers` | Realtime voice provider adapters. |
| `text` | Text preferences, providers, chat service, and storage. |
| `attachments` | Tenant-owned chat image metadata, normalization, private object storage, and lifecycle. |
| `browser_runtime` | General browser-task arguments and profile-free runtime execution. |
| `browser_accounts` | Optional saved-profile metadata, encrypted credentials/opaque handles, provider lifecycle, request binding, schedule binding, leases, audit, and reconciliation. |
| `mcp` | User-owned remote MCP server models, auth, clients, and services. |
| `integrations` | Twilio, Telnyx, and WhatsApp communication records, resources, webhooks, conversations, replies, and tools. |
| `location` | Typed request-scoped location validation, freshness checks, metadata sanitization, and instruction rendering. |
| `whatsapp` | Linked-device setup API, encrypted session storage, runtime validation, fenced outbox, and multi-session worker. |
| `contacts` | Tenant-owned people, phone matching, and contact CRUD. |
| `agent_core` | Shared agent contracts, instructions, and internal tools. |
| `agent_runtime` | Runs, schedules, inbound agent webhooks, sessions, events, subagents, and authorization. |
| `agent_identity` | User agent records and service. |
| `agent_memory` | Durable memories and memory tools. |
| `agent_skills` | Developer/user skill catalog and rendering. |
| `agent_planning` | Durable task plans and task steps. |
| `persona` | Prompt/persona rendering from agent data. |
| `security` | Secret encryption backends. |

## Auth And Tenancy

Firebase identities map to local Voxos user ids in `auth_firebase_users`.
`AuthenticatedUser.user_id` is the only canonical user authority. User-owned
database access should use tenant session helpers that set transaction-local
`app.user_id` for Postgres RLS policies.

When adding a user-owned table, include a user owner column, tenant indexes,
RLS/forced RLS migration coverage, and tests proving wrong-user access fails.

Public deployments can enable Firebase Admin session-revocation checks after
providing project-scoped user-read credentials. Verification fails closed when
the revocation service is unavailable. API admission uses the verified local
user ID for write/stream rate and concurrency budgets; a separate per-process
cap bounds HTTP work. See [Account And Auth](Account-And-Auth.md).

`agent_webhooks` is a forced-RLS tenant table. Authenticated management uses the
Firebase-derived local user id. Public ingress receives only an opaque webhook
id and token; a dedicated `agent_webhook` service scope can select definitions
to verify a one-way SHA-256 token hash but cannot insert, update, or delete them.
After lookup, the persisted owner is used to enter tenant scope before loading
the agent, resolving tool grants, or enqueuing the run. Request JSON never
supplies tenant or tool authority.

Production database access should use two separate principals. Application
services receive a pooled URL for a least-privileged runtime login that is not
an object owner and has `NOBYPASSRLS`; only the migration process receives the
direct URL for the migration/owner login. Remote production connections should
use `verify-full` TLS with an appropriate CA bundle. The Alembic connection
creates the required `vector` extension before applying schema migrations.
Local Docker retains the database driver's local transport behavior.

`agent_tool_route_preferences` is a forced-RLS tenant table keyed by user,
agent, and semantic action. A stored pin contains only an exact tool source and
server/integration identifier; it does not contain credentials and does not
grant authority. API writes derive the tenant from Firebase authentication and
validate the pin against the agent's current allowed route candidates before
persisting it.
The `user_contacts` table follows this model and enforces unique E.164 phone
numbers per user.

Device location is optional request data, never identity or tenant authority.
Clients send a typed WGS84 snapshot only after a local foreground opt-in. The
server validates ranges, rejects malformed text payloads, silently removes an
invalid optional voice snapshot, and ignores stale or implausibly future data.
Text location stays out of the durable chat prompt and transcript. Voice
location is stripped from persisted room metadata and appended only after the
durable base prompt snapshot is created. A subagent may receive a run-scoped
copy from its parent, but freshness is checked again at child execution; no
global location profile or background tracking record is created.

`browser_accounts`, `agent_browser_account_grants`,
`browser_account_run_authorizations`, `browser_account_audit_events`, and
`browser_account_leases` are tenant-owned forced-RLS tables. Browser provider
profiles and sessions are not database authority: the service must first bind
the authenticated tenant, exact agent/account assignment, trusted MCP
connection and tool grant, one single-use run authorization, and an exclusive
account lease.

`message_attachments` follows the same forced-RLS model. The authenticated user
and persisted chat are the only ownership authority. Client attachment IDs are
looked up inside that scope; clients never provide an object key or user ID.
The service atomically claims an ordered ready set into one transcript turn and
uses a client turn key to prevent duplicate submission. A dedicated
`attachment_worker` service scope can select and update only deleted attachment
rows and is used by the durable run worker to finish object deletion after a
process interruption; normal worker access does not bypass tenant policies.

Image bytes are not stored in Postgres. Sources are decoded off the async event
loop, normalized with metadata removed, and stored behind an object-store
protocol. Local development uses a gitignored persistent directory or the
shared Compose volume. A deployed installation can use a private S3 bucket with
public access blocked and prefix-limited runtime permissions. Authenticated API
routes proxy full or thumbnail bytes while
holding read locks on the active parent chat and attachment; logs and API
summaries omit filenames, hashes, object keys, and image content.

`integration_conversations` and `integration_replies` are also forced-RLS
tenant tables. They bind a verified provider/resource/remote peer to an active
runtime session and one inbound message to one durable reply outcome. The
dispatcher derives the reply destination from these records; message text or
LLM arguments cannot supply tenant authority or replace that destination.

Service-wide work uses narrow RLS scopes. `agent_worker` is limited to durable
run tables. `voice_worker` can select/update tenant call-demand rows and own
opaque project leases, but it cannot read LiveKit settings or vault plaintext
cross-tenant. After a demand is selected, the supervisor uses its persisted user
ID to enter a tenant-scoped session before resolving credentials.

The WhatsApp worker follows the same boundary with a distinct
`whatsapp_worker` service scope. That scope can discover and update
`whatsapp_sessions` and `whatsapp_outbox`, and exclusively owns
`whatsapp_session_leases`; it does not receive a cross-tenant policy for
`whatsapp_session_keys` or the user secret vault. After claiming a persisted
session demand, the supervisor enters that exact user's RLS context and verifies
the current owner/generation fence before decrypting auth, accepting an event,
or completing an outbox item. The `whatsapp_mobile_registrations`,
`whatsapp_sessions`, `whatsapp_session_keys`, and `whatsapp_outbox` tables also
have forced tenant RLS. The worker has no service-scope policy for mobile
registrations. A unique tenant constraint currently permits one
mobile-registration attempt and one WhatsApp session per Voxos user while the
deployment can serve many users.

## Secret Vault

Provider keys, LiveKit credentials, MCP tokens, OAuth secrets, private keys, and
integration credentials flow through `UserSecretVaultService`. The vault stores
ciphertext and encryption metadata in Postgres. Local development uses AES-GCM;
deployed environments can use AWS or Google Cloud KMS envelope encryption. In
the Google backend, KMS wraps one random AES-256 data key per stored secret and
the authenticated tenant/provider/credential/purpose context is supplied to
both KMS and AES-GCM.

Plaintext secrets must not be returned from APIs, logged, written to docs, or
stored in frontend config.

Browser Accounts reuse this vault rather than adding a password table. Typed,
versioned local or Browserless website credentials, opaque Browser Use profiles, and
temporary provider session handles use separate authenticated-encryption
purposes and can be unsealed only with the same user, provider, credential ID,
and purpose. Plaintext website credentials, TOTP seeds, cookies, storage-state
JSON, live URLs, and provider handles do not enter Browser Account metadata or
API responses. The private Stagehand adapter or Browserless receives the stored
username and password only in the backend provider-execution path. Browser
Account validation responses strip submitted values before returning field
errors. The local browser runtime, Browserless, and Browser Use remain trusted
with the active browser content or authenticated state; vault encryption is not
end-to-end secrecy from the browser runtime or selected hosted provider.
Browserless uses the hosted-agent `/chromium/cli` contract with a token-only
connection URL and the configured fleet endpoint. Browser Use receives its
account's validated two-letter country during setup and retrieval and requires
profile setup again when the country changes.

The Monzo MCP has two deliberately separate authentication boundaries. Voxos
stores the long-lived MCP endpoint bearer and tenant-owned Monzo OAuth client
information and tokens in the user vault. It sends the endpoint bearer to
`http://monzo-mcp:8000/mcp` plus a short-lived HMAC-signed delegation bound to
the user, MCP server, provider, audience, and expiry.

The container stores no Monzo credential. During a tool call it presents the
delegation to `/api/internal/mcp/credential-broker/v1/access-token`. The route
verifies the signature before entering tenant scope, locks the OAuth token row,
refreshes and atomically stores a rotated refresh token when needed, and returns
only the usable access token with `Cache-Control: no-store`. A rejected access
token is represented only by its SHA-256 fingerprint, preventing redundant
refresh after another request has already rotated it.

The endpoint and broker are private-network interfaces. One container can serve
multiple Voxos users statelessly, but neither route should be exposed through
untrusted ingress.

## Outbound Endpoint Boundary

Tenant-configured MCP and OpenAI-compatible destinations share one outbound
policy. Unregistered destinations require HTTPS on port 443, reject URL
credentials and fragments, and must resolve exclusively to global addresses.
Text-provider base URLs also reject queries. MCP and text transports do not
follow redirects.

Local Docker services are deployment-owned exact exceptions rather than tenant
authority. Each registration binds a purpose, complete normalized URL, and the
`local_container` kind. Tenant-configurable MCP API-key headers cannot claim
transport, metadata, or proxy authority.

Financial tool results are still application data after authorized execution.
Balances and transactions can enter model context and durable chat, run,
transcript, or tool-event storage. The preset therefore grants no tools
automatically and the supported Compose service disables writes.

WhatsApp uses two encryption layers. A random per-integration data key is sealed
through the normal tenant vault. The Baileys credential object, Signal keys,
and temporary link challenge are then AES-GCM encrypted in their tenant tables
with the user, integration, logical key, and revision bound as authenticated
context. A keyed fingerprint records successful number validation without
persisting a second plaintext number.

The mobile-registration precursor uses a separate vault purpose. After a
Twilio or Telnyx SMS webhook passes its signature, account, route, and exact
resource checks, a code-bearing normalized envelope can be encrypted into the
owning user's bounded registration inbox for ten minutes. The ordinary
message/event/agent-reply path is skipped for a captured candidate.
Authenticated no-store endpoints expose only the code, full sender number, and
timestamps; the frontend keeps them in component memory. Completing setup
discards the selected WhatsApp code and deletes the temporary record and
secret. Rejecting a candidate replays it through the provider's idempotent
normal SMS ingestion path.

## WhatsApp Worker Boundary

The Python supervisor owns one private Node.js child over bounded,
version-checked stdio frames. The child runs the exactly pinned Baileys package,
has no HTTP listener, database or vault credentials, filesystem auth store, or
tenant API, and receives only an allowlisted process environment. Stdout is
reserved for protocol frames; diagnostics must not contain JIDs, numbers,
challenges, message bodies, upstream response bodies, or auth values.

One bridge process multiplexes several tenant sockets instead of spawning one
process per tenant. Lease generations fence ownership across worker replicas
and are carried on every command, response, event, auth mutation, and outbox
claim. Auth writes block until their encrypted tenant transaction is
acknowledged. Control and inbound events are independently ordered and are
acknowledged only after durable processing. Stale-generation events fail
closed. The supervisor renews active database leases in an independent loop,
so a slow challenge, reset, or session-start request for one tenant cannot
delay ownership renewal for the other active sessions.

A connected event is not enough to authorize traffic. The runtime requires a
PN-bearing identity reported by the linked session and compares it with the
selected tenant-owned E.164 resource. Only the exact match enables the
integration and resource. LID-only, unresolved, or mismatched identities remain
disabled and trigger confirmed logout/cleanup or a visible relink state.

Inbound WhatsApp contact identities are stored separately from encrypted
session credentials in `whatsapp_contact_identities`. The table is tenant
owned, uses forced RLS, and scopes each PN, LID, or username mapping to the
specific connected integration. Only the backend may link one of these records
to a saved contact, and only after an authoritative PN maps to the contact's
exact current E.164 number. LID-only observations and alias conflicts remain
unlinked, so a bridge payload, display name, stale contact edit, or another
tenant's observation cannot grant contact-derived permissions.

Direct senders are selected from Baileys' remote JID fields; group senders are
selected from its participant fields. The bridge classifies both primary and
alternate identifiers with Baileys' PN/LID predicates, then resolves LID-only
identities through contact data, group participant metadata, and the encrypted
Signal LID mapping store. It never derives a phone number from LID digits.
Group conversations use the group JID for routing and context, while each
message retains its sender identity and contact snapshot. Contact-derived admin
permissions are disabled for group messages because replies and tool-derived
information are visible to the group.

Disconnect and relink also fail closed. Remote logout must be confirmed before
Voxos deletes encrypted auth. An uncertain logout retains the state for a safe
retry. A completed relink advances the lease generation before starting a new
controller, preventing delayed events from the previous linked device from
reopening the integration.

## Tool Authorization

Tool execution derives authority from `AgentRunContext` and
`ToolExecutionContext`. Generic tool arguments are validated so model-supplied
fields cannot smuggle user ids, headers, credentials, server ids, tokens,
browser-account IDs, provider profile IDs, or provider session authority. The
purpose-built `use_browser` contract deliberately accepts a validated public
HTTPS start URL and exact public origins for profile-free browsing; local,
private, credential-bearing, and non-standard targets are rejected.

Web chat and authenticated in-app voice expose one high-risk `use_browser`
capability. It accepts a complete browser instruction, action mode, and either a
public start URL or an opaque handle for one of the saved profiles assigned to
the exact agent. The description contains only safe profile labels, primary
origins, and handles. The backend reloads assignment and hosted provider-tool
authority at execution, so an invented handle cannot select an account and no
model argument can provide stored credentials, provider state, or a raw account
ID. Saved-profile authority is excluded from subagents and phone callers.

`BrowserRuntimeService` owns profile-free task execution, model transport, and
session cleanup. `BrowserAccountService` is the optional profile broker: it
adds server-owned origins and named secret variables to the same provider-neutral
task request. The private Stagehand service receives the agent instruction
unchanged and contains no website or objective-specific workflow. The built-in
`browser-use` skill guides instruction composition and profile selection at the
agent layer.

The private task service requires a deployment-owned bearer credential from
each API or worker caller. A separate credential protects Steel's HTTP and
DevTools interfaces. These credentials authorize service access; the caller
must still establish tenant, agent, tool, and account authority before sending
a task. Health endpoints remain available for process readiness checks.

Steel sessions are ephemeral and force-closed after each task. Saved Browser
Accounts supply vault-backed credentials or hosted profile references rather
than a shared Chromium profile directory. One local browser task runs at a
time, with at most four waiting tasks and a 30-second queue deadline by
default. The 570-second service task budget includes queue time, and rejected
or expired queue admissions return 429 with `Retry-After`.

The Steel image runs as a non-root user with the Chromium sandbox enabled.
Container hosts must provide seccomp and AppArmor profiles that permit the
sandbox's user-namespace operations. Configure the chart's
`browser.seccompProfile` and `browser.appArmorProfile` for the host. Network
policies should admit only the required service connections and restrict browser
egress from private and infrastructure addresses. See the repository's
`helm/README.md` deployment guide for the workload and network settings.

Scheduled use remains an explicit stored-credential local or Browserless
account binding snapshotted onto the run at enqueue time. The first provider
round is narrowed to that browser
capability and requires its execution; missing authority fails the run.
Provider and website credentials, profile, origins, persisted scheduled task,
and budgets are supplied by the server. The generic MCP schema sanitizer removes authority
fields recursively; if a provider requires one from the model, the tool fails
closed. Browser Use profile execution remains on the provider task API that
accepts an allowed-domain list so it preserves the server-owned navigation
boundary.

The authorization service checks persisted grants or a run/session effective
tool policy snapshot, then writes audit events for allow and deny decisions.
Provider phone calls derive caller access from the RLS-protected call-session
snapshot. Unknown and non-admin callers fail closed to a narrow MCP intersection
that accepts `web_search_exa` only from the tenant's enabled, trusted, connected
server whose persisted preset metadata and URL match the canonical Exa catalog
identity and whose exact server/tool pair is already allowed and granted. Tool
name equality alone is not authority. Their prompt also omits owner persona
details, memories, skills, plans, and custom voice instructions.

Subagent model routing follows the same explicit-authority design. The LLM may
request a model name but cannot supply user, parent-run, target-agent, provider,
credential, or session authority. `SubAgentService` receives those values from
the persisted parent and trusted run context, validates the model against the
user-owned allowlist, and intersects child tool permissions before enqueueing.

The contact admin flag is a convenience trust decision based on caller ID, not
strong authentication. Provider routing authenticates the integration path,
not the human caller. Inbound SMS and WhatsApp share one tenant-scoped contact
admission check. Unknown numbers, unresolved WhatsApp identities, stale contact
snapshots, and alias conflicts are recorded but receive a durable `suppressed`
reply disposition without an agent run, transcript, or outbound delivery. An
exact saved Admin contact may retain the selected agent's existing
`search_contacts` grant, while non-admin senders cannot access contacts. Group
messages never receive contact-derived Admin elevation because the response is
visible to the group. Admin status does not add mutating, task-creation,
integration-delivery, credential, or arbitrary outbound tools.

## Product Docs

Product docs are global application docs. They are safe for all users to read
and must not contain private user data. Docs retrieval tools are read-only
internal tools and still flow through the same tool policy and audit path.

## Tenant History And Learning

History tools derive account authority from `ToolExecutionContext`, retained
sessions, and persisted root-run policy. Owner voice proof is persisted from the
verified LiveKit route. Current grant revocations and cancellation are checked
again before learning commits. Source identifiers select evidence within the
verified tenant; they cannot supply account identity or permission.

`agent_learning_settings` and `agent_improvement_evidence` use forced RLS.
Composite evidence foreign keys bind source sessions and proposals to the same
user. Candidate fingerprints deduplicate within an account. Account row locks
serialize knowledge changes, consent, grant updates, source withdrawal, and
application. Independent assessment runs outside the transaction; final checks
compare complete knowledge versions and freshly resolved evidence. Target
changes and proposal application commit together, and versioned snapshots
protect review and undo from overwriting later edits.

Retrieved history is untrusted data. Secret assignments, quoted credential
fields, bearer tokens, and sensitive URL components are sanitized. Retrieval
outputs and arguments are withheld from durable tool events so deleting an
original source cannot leave a second cached history copy. Learning retains
source references rather than complete copied transcripts. Model calls use the
existing credential vault and provider client with no tools.

## Installation configuration boundary

Public source and chart packages contain reusable application code and sanitized
examples. Operators provide hostnames, certificate references, Firebase project
settings, image registries, storage and network selectors through private Helm
values and referenced Secrets. Vault encryption keys and database credentials
must survive redeployments and be included in encrypted off-cluster recovery
procedures. The frontend obtains its server's public client configuration at
runtime; credentials remain in the backend vault or deployment Secrets.

## Source and redistribution

Published images retain component license notices and source archives under
`/usr/share/voxos/source`. Steel's `/source/` route serves these static build
inputs; session, browser and DevTools operations require bearer authentication.
The runtime contains compiled service assets and production dependencies,
with test captures and dependency test directories excluded from the service
tree. See [Developer Setup](Developer-Setup.md) for source access and the
component-specific redistribution terms.

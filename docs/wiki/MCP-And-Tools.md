# MCP And Tools

Last updated: 2026-10-27

Voxos exposes two broad tool families to agents: built-in internal tools and
user-owned remote MCP tools. Tool access is policy-driven and audited.

Voxos uses MCP Python SDK 2 for remote transports and exposed internal servers.
Streamable HTTP clients use an explicit HTTPX 2 client with bounded timeouts;
redirect responses are rejected before another request is sent. Broker
assertions are minted for each outbound HTTP request.

## Internal Tools

Internal tools operate on Voxos-owned agent data for the authenticated user.
Current internal tools include:

| Tool | Risk | Kind |
| --- | --- | --- |
| `search_agent_history` | Medium | Account evidence search |
| `read_agent_history` | Medium | Bounded cited context |
| `record_agent_improvement` | Medium | Policy-assessed knowledge candidate |
| `list_agent_memories` | Low | Read-only |
| `list_agent_skills` | Low | Read-only |
| `get_agent_skill` | Low | Read-only |
| `list_task_plans` | Low | Read-only |
| `search_contacts` | Low | Read-only, denied by default |
| `delegate_agent_task` | Medium | Queues a durable child run with a user-allowed model |
| `create_agent_memory` | Medium | Mutating |
| `propose_agent_memory` | Medium | Mutating |
| `archive_agent_memory` | Medium | Mutating |
| `create_user_skill` | Medium | Mutating |
| `update_user_skill` | Medium | Mutating |
| `archive_user_skill` | Medium | Mutating |
| `create_task_plan` | Medium | Mutating |
| `create_agent_schedule` | Medium | Schedules a future turn in the current chat |
| `update_task_step` | Medium | Mutating |
| `complete_task_plan` | Medium | Mutating |

The docs index adds read-only product documentation tools. Those tools are safe
to grant because they read global product docs rather than user secrets.

`search_contacts` searches only the authenticated user's saved contacts and
returns a typed `not_found`, `unique`, or `ambiguous` resolution with contact
IDs, display names, and full phone numbers. It does not return notes, email
addresses, or admin flags. Agents are instructed to search first for a named
person, proceed with a unique match, ask the user to choose between ambiguous
matches, and ask for a number or contact creation only when no match exists.

Access remains controlled by the authenticated tenant and agent grant.
Connecting a call-capable Twilio or Telnyx resource adds this low-risk
prerequisite to the default agent only when no explicit contact-search decision
exists. Migration backfill follows the same rule for existing allowed
place-call grants. An explicit denial is never replaced; while denied, the
dependent place-call integration tool is not advertised to that agent.

An approved phone integration tool resolves the returned contact ID inside the
same tenant and requires a complete bounded caller-safe objective. The
initiating run ID comes from trusted execution context rather than model
arguments. The objective becomes durable phone-session context but does not
expand caller access, MCP trust, or the effective tool-policy snapshot.

`delegate_agent_task` is denied by default and requires an explicit internal
tool grant. It accepts a scoped task, isolated or summary context, and an
optional model from the user's provider-specific subagent allowlist. It does
not accept user, agent, run, session, provider, credential, URL, or token
authority in model arguments. The parent run and target agent are derived from
trusted execution context, and the child cannot expand the parent's tool
permissions. A child does not inherit `delegate_agent_task`; nested
self-delegation therefore cannot recursively requeue the same unavailable
capability. Delegation is intended for independent multi-step work, not as a
way to reach a tool that is already available in the current run. Agents should
call a direct tool, or use the run's tool catalog, for ordinary single-action
lookups.

`create_agent_schedule` is available only during an interactive authenticated
text-chat turn and only for explicit reminders, future checks, monitoring, and
follow-ups. It creates a one-shot or fixed-interval schedule whose future
responses return to the current chat. The authenticated run context supplies
the owner, agent, run, and chat authority; the model supplies only the task
name, future instruction, timing, and timezone. The tool does not accept
another chat or a Browser Account, and scheduled runs, webhooks, manual worker
runs, and subagents cannot use it. `create_task_plan` remains a planning record
and does not schedule execution or notifications.

## Default Web Search

Each new account starts with the canonical keyless Exa MCP endpoint enabled,
trusted, and connected for `web_search_exa` and `web_fetch_exa`. A data
migration adds the same server for existing accounts that do not already have
an Exa endpoint. Existing Exa records are left unchanged so a previous opt-out
or custom API-key setup is not replaced.

The platform default stores a versioned snapshot of the two low-risk tool
schemas, so normal chat and voice startup do not need a discovery request. An
explicit MCP connection test refreshes that cached discovery data from Exa. If
an older or incomplete server record is missing a schema for an allowed tool,
the runtime performs one live capability discovery, uses the returned schema
for the current run, and repairs the bounded cache for later runs. Allowed tools
are stored first when a server exposes more definitions than the cache limit.
Only tools returned by cache or live discovery are exposed; the runtime does
not invent an empty schema for a missing definition. Only the exact canonical
keyless preset identity, transport, URL, and auth mode qualify for
platform-default access.

Every agent receives low-risk default grants for those two tools while the
server remains enabled, trusted, connected, and allowlisted. A persisted denied
grant overrides the default for that agent. Users can also disable the Exa
server from MCP settings, which removes it from newly created text runs and
voice sessions; enabling it again follows the normal test-and-grant flow.
Keyless access is subject to Exa's free-plan limits, and users can configure the
API-key preset when they need higher limits.

## Tool Approval Flow

1. Open Tools.
2. Toggle each internal tool that the default agent may use.
3. Start a text run or voice session.
4. The run receives an effective tool policy snapshot.
5. Tool execution checks that snapshot or the persisted grant.
6. An audit event records allow/deny decisions.

The Tools page also configures action-level provider routing. A route groups
equivalent implementations without grouping unrelated tools from the same
server. For example, `web.search` includes Exa `web_search_exa`, Tavily
`tavily-search`, Firecrawl `firecrawl_search`, and Browserless
`browserless_search`. Automatic mode selects the lowest-priority authorized
implementation: Exa is the current default, followed by Tavily, Firecrawl, and
Browserless. Browserless scrape, map, performance, export, agent, and other
browser capabilities remain separate tools and are unaffected by the web-search
route.

A pinned route names an exact tool and server. If that implementation becomes
unavailable or loses its grant, the route exposes no substitute until the user
changes it; pinned routes never silently fall back. Automatic routes can select
the next authorized implementation. The resolved choice is stored in the same
effective policy snapshot as the grants, so it cannot change during an active
run or voice session.

## Browser Use And Saved Profiles

The recommended Voxos Local Browser is a built-in Browser Account provider and
does not use MCP. Browserless and Browser Use Cloud remain optional hosted
connections configured under MCP, while reusable website identity is managed
separately under **Browser Accounts**. A hosted Browser Account references one
exact enabled, trusted, connected provider connection and reuses its encrypted
provider token rather than duplicating it.

Browserless stored-credential accounts require static bearer authentication and
an explicit `browserless_agent` allowlist/grant. Browser Use managed-profile
accounts require API-key-header authentication and `run_session`. Both tools
are high risk and disabled by default in their presets.

Web chat and authenticated in-app voice expose one general `use_browser`
capability. It accepts an agent-authored instruction plus either a public HTTPS
start URL or an opaque saved-profile handle. The tool description lists only
the safe labels, primary origins, and handles for profiles assigned to that
exact agent after intersecting hosted profiles with their underlying MCP grant.
Execution performs the same checks again. Phone callers and subagents do not
receive saved-profile authority.

A scheduled task can instead reference one ready assigned stored-credential
local or Browserless account;
that binding is validated at schedule creation and again from the persisted
scheduled root run. Voxos resolves provider and website credentials, profile
origins, and budgets server-side. Public browsing accepts validated HTTPS URLs;
saved profiles cannot override or broaden their configured origins. Ordinary
generic MCP arguments cannot supply raw account IDs, provider sessions, or
credentials. See [Browser Accounts](Browser-Accounts.md) for setup, provider
trust, action policy, threat model, and incident response.

## Large Tool Catalogs

Voxos resolves grants and action routes before composing the model's tool view.
Small catalogs continue to attach their tool schemas directly. When the
authorized external catalog reaches ten tools or roughly 10,000 schema tokens,
text runs keep internal tools and the selected implementation of each preferred
action direct. Only the remaining long-tail external tools move behind three
catalog tools:

- `tool_search` searches only the run's authorized, route-resolved catalog.
- `tool_describe` returns the full schema for one opaque result ID.
- `tool_call` validates the target arguments and delegates to the original
  authorized provider.

Catalog IDs are scoped to the current run view. Searching, describing, or
calling a result cannot add a grant, select a filtered provider, supply a user
or server identity, or bypass the target tool's existing argument validation
and authorization audit. The run builds this registry once from tool definitions
cached by the server's explicit connection test; it does not rediscover every
MCP server while preparing a chat or again before dispatching a tool call.
Execution still uses the live authenticated MCP connection. Voice keeps direct
LiveKit tools to avoid adding a search round to realtime conversations, but it
applies the same snapshotted action route before composing those tools.

LiveKit voice sessions persist the same kind of effective policy snapshot before
tools are composed. Provider phone calls add a caller-access intersection:
limited callers can receive `web_search_exa` only through a qualifying Exa
server identity, while admin contacts and authenticated browser sessions retain
the channel's normal approved policy. A qualifying server must belong to the
same tenant, use a canonical URL from the current `exa` preset, retain
`preset_id=exa` in its safe metadata, be enabled, trusted, and connected, and
explicitly allow `web_search_exa`. The selected agent's base policy must also
already grant that exact server ID and tool. A custom server or gateway that
merely advertises a tool with the same name does not qualify. The restriction
applies to both tool enumeration and execution.

Inbound SMS and WhatsApp peers have a separate channel intersection because
receiving a message does not authenticate the sender as the tenant owner. The
reply run keeps only granted low-risk current-time/product-doc tools plus
granted `web_search_exa` from the tenant's enabled, trusted, connected
canonical Exa preset server. When the exact normalized sender number matches a
saved Admin contact, the intersection may also retain an existing
`search_contacts` grant. Unknown and non-admin senders cannot receive contact
access. Contact results remain limited to IDs, display names, and phone-number
endings.

Admin caller status on either messaging medium does not expose
`create_task_plan`, `create_agent_schedule`, integration delivery, private
memories, credentials, other mutating tools, or arbitrary outbound actions.
`create_task_plan` stores a plan but does not schedule execution or send
communications. The authenticated text-chat scheduling tool can only target
its current chat; it cannot address an inbound messaging sender. The
communications adapter, not an LLM tool call, delivers the reply to the
persisted verified sender.

## Location-Aware Tool Calls

For a foreground text turn or owner in-app voice session with a fresh shared
location, the agent instructions provide WGS84 latitude, longitude, horizontal
accuracy, and capture time. The model uses the location only when it materially
helps the request, such as nearby place search, maps, travel, or local weather.

The discovered tool schema remains authoritative. Voxos does not inject one
global coordinate field into every MCP call. The agent translates the snapshot
to the tool's required latitude/longitude, lat/lon, location-bias, or structured
location shape. GeoJSON coordinate arrays use longitude then latitude. A street
address or place name must be resolved by a suitable map or geocoding tool; it
must not be inferred from coordinates. Curated preset normalization still
applies after the model produces schema-valid arguments, including Google Maps
Grounding Lite's camelCase location contracts.

Google Maps Grounding Lite `search_places` has a typed presentation adapter.
Successful structured results are validated into place cards from the returned
Place ID, attribution title, exact place and directions URLs, and optional
coordinates. Invalid cards are skipped independently without inventing missing
fields. Google URLs must resolve to supported Maps destinations and cannot use
credential-like query fields, fragments, or nonstandard ports. The full result
remains available to the model and the current response stream, while durable
tool and runtime events store only a completion or failure marker and the
assistant transcript text. The completed answer receives at most six cards,
preferring places it names or cites and otherwise using the most recent search.
This prevents Google Maps place content from being retained in chat history.
Other MCP place tools continue to render their ordinary answer links until they
have a provider-specific result contract and attribution adapter.

## Remote MCP Servers

MCP servers are user-owned and remote-only. Voxos accepts Streamable HTTP and SSE
MCP transports. Local stdio commands are intentionally rejected by the frontend
because they would let browser users register commands on the worker host.

Supported auth modes:

- No auth.
- Static bearer token.
- API key header.
- OAuth authorization code.
- OAuth client credentials.
- Private-key JWT.

Browser OAuth requires a persistent deployment-level
`MCP_OAUTH_STATE_SIGNING_SECRET`. This is a Voxos callback-integrity secret, not
a provider credential and not a per-user secret. Keep it identical across API
replicas. OAuth client details, the PKCE verifier, tokens, and refresh tokens
remain encrypted in the user secret vault.

For providers with OAuth metadata and dynamic client registration, the catalog
does not ask the user for a client ID or client secret. OpenBnB follows this
path: select **Start sign-in**, approve access on the hosted OpenBnB page, then
return to Voxos. The page tests the authenticated server and discovers tools
when focus returns; **Finalize connection** provides a manual retry if needed.

The MCP page supports a catalog of common remote MCPs and a custom server form.
After creating a server, authenticate it if required, test discovery, set
explicit allowed tools, and approve grants for agents.

## Curated MCP Example Skills

Operators can copy useful MCP-backed example workflows into one user's editable
skills without loading every available example. The catalog, Markdown bodies,
and dedicated loader scripts live under `examples/skills/`:

```bash
make example-skills-list
make example-skills-load USER_ID=<local-voxos-user-id> MCP=github DRY_RUN=1
make example-skills-load USER_ID=<local-voxos-user-id> MCP=github
```

The loader matches servers by canonical preset and optional variant metadata,
not by a user-editable label or database ID. Before any skill write, it verifies
that every dependency belongs to the selected tenant, is enabled and trusted,
and still advertises and allows every required upstream tool. A server that
needs authentication or remains untested can produce an editable installed
skill with a visible readiness warning; reconnect or test it before trying to
run the workflow. Disabled, untrusted, failed, missing, undiscovered, or
unallowed dependencies block only the affected skill.

Installing an example does not authenticate an MCP server, test a connection,
change an allowlist, create an agent tool grant, or expose credentials. Runtime
use still requires an enabled, trusted, connected server and an exact
server-specific grant for the selected agent. Existing skill slugs are never
overwritten, so user edits and archived copies are preserved.

The OpenBnB `accommodation-shortlist` example requires `airbnb_search`,
`airbnb_listing`, `maps_distance_matrix`, `maps_search_places`, and
`maps_place_details` to remain both discovered and allowed. It compares a
bounded set of date- and guest-specific listings against travel anchors and
nearby essentials. It does not book accommodation, and users must verify
current price, availability, rules, and cancellation terms on the provider
site.

## Google Workspace Setup

Google Workspace MCP is currently available through the
[Google Workspace Developer Preview Program](https://developers.google.com/workspace/preview).
Google hosts a separate MCP endpoint for each Workspace product, so Voxos
creates one server connection for each selected product. The Voxos catalog
currently includes Gmail, Google Drive, Google Calendar, Google Chat, and
People. You can reuse the same Google OAuth web client ID and client secret for
each connection, but you must authorize and test each server separately.

Follow Google's
[Google Workspace MCP configuration guide](https://developers.google.com/workspace/guides/configure-mcp-servers)
for the current provider requirements. The shortest setup path is:

1. Join the Google Workspace Developer Preview Program.
2. In the [Google Cloud console](https://console.cloud.google.com/), create or
   select a project.
3. In **APIs & Services**, enable both the standard API and the matching MCP API
   for each product you want to connect:

   | Voxos product | Standard API | MCP API |
   | --- | --- | --- |
   | Gmail | Gmail API | Gmail MCP API |
   | Google Drive | Google Drive API | Google Drive MCP API |
   | Google Calendar | Google Calendar API | Google Calendar MCP API |
   | Google Chat | Google Chat API | Google Chat MCP API |
   | People | People API | People MCP API |

4. In **Google Auth Platform**, configure:
   - **Branding**: give the OAuth app a recognizable name such as `Voxos`.
   - **Audience**: choose **Internal** when available. Otherwise choose
     **External** and add each permitted Google account as a test user.
   - **Data Access**: add only the scopes for the products you intend to use.
     Copy the current scope list from the
     [OAuth consent section of Google's guide](https://developers.google.com/workspace/guides/configure-mcp-servers#configure-consent-screen).
5. Under **Google Auth Platform → Clients**, create a **Web application** OAuth
   client. Add the exact **OAuth redirect URI** displayed by the Voxos MCP form
   under **Authorized redirect URIs**. A default local installation normally
   displays:

   ```text
   http://localhost:7100/api/mcp/auth/callback
   ```

   Use the value shown by your running Voxos installation if it differs. Google
   requires an exact match.
6. Create the client and copy its client ID and client secret. Keep the secret
   private.

Then connect each product in Voxos:

1. Open **MCP**, select **Google Workspace**, and choose one product under
   **Variant**.
2. Paste the Google OAuth client ID and client secret.
3. Select **Connect Google Workspace**, approve the requested Google access, and
   return to Voxos.
4. Finalize or test the connection, review the discovered tools, and grant only
   the tools the agent needs. Prefer read-only tools until a workflow requires a
   write action.
5. Repeat for each additional Workspace product. Reuse the same OAuth client ID
   and secret; do not create another Google Cloud project or OAuth client.

For Google Chat, complete the additional Chat app configuration described in
Google's guide before connecting it. If Google reports a redirect mismatch,
compare the URI in the Voxos form with the OAuth client's authorized redirect
URI character for character. If an External OAuth app rejects the account,
confirm that account is listed under **Audience → Test users**.

The **Auth** panel can replace a stored OAuth client by submitting both the
client ID and client secret with **Save client & start sign-in**. Voxos stores
the replacement in the encrypted vault before opening the provider. Leaving
both fields blank reconnects with the existing client. **Delete server** is
available in Auth and Advanced and removes the server together with its stored
MCP credentials.

Flightradar24 has two catalog choices. **Local Compose** appears only when the
API registry contains the exact `http://flightradar24-mcp:8000/mcp` URL for the
`mcp` purpose; select it after starting the root Voxos stack and
`make mcp-stack-up`. It does not require a custom server entry or Voxos bearer
token. **Deployed HTTPS endpoint** accepts the complete public HTTPS URL of a
separately deployed instance, including a custom hostname and path. Arbitrary
unregistered private or loopback URLs remain blocked.

Monzo has one **Local Compose** choice. Voxos stores the separate MCP endpoint
bearer plus each user's confidential-client OAuth data and rotating Monzo
tokens. The sibling container stores no provider credentials. Voxos sends its
endpoint bearer and a short-lived, user-and-server-bound delegation; the
container exchanges that assertion at the private Voxos broker for only the
current access token.

The stateless container can serve multiple Voxos users. **Local Compose** is
available only when `http://monzo-mcp:8000/mcp` is registered for the `mcp`
purpose. The preset exposes no starter tool grants and runs with financial
writes disabled. Grant read tools explicitly.
Balance and transaction results enter model context and may be retained in
chats, runs, transcripts, and tool events. Do not expose the local endpoint
publicly or expose the private broker route through untrusted ingress.

Only enabled, trusted servers with cached tested or platform-curated tool
definitions and explicit allowed tools should reach agent sessions.

Voxos validates each model-produced MCP argument object against the server's
discovered JSON Schema before sending it. Invalid nested objects, missing
required fields, and values that violate referenced definitions are recorded
as tool failures and returned to the model for a bounded correction attempt;
the invalid request is not sent to the MCP server. If a server still returns
HTTP 400 or 422, Voxos reports an argument rejection rather than a connection
failure and allows the same bounded tool loop to correct it. Validation errors
name only schema paths and constraints, not rejected argument values.

Built-in and MCP tool failures are continuation results rather than fatal chat
errors. The failed call remains visible in tool activity, and the model is told
that it did not produce a usable result so it can still answer with any
successful results and clearly state what was not completed or verified. A
chat run fails only when the surrounding provider, persistence, or run-control
path cannot continue safely.

Curated presets may tighten an incomplete provider schema or normalize a
provider-specific resource identifier when the upstream tools expose
incompatible contracts. Google Maps Grounding Lite weather is handled this way:
the runtime enforces exactly one camelCase `location.address`,
`location.placeId`, or `location.latLng`, requires complete structured dates,
rejects extra fields locally, and removes the `places/` resource-name prefix
returned by Google name resolution before sending a Place ID to weather.
Weather guidance prefers calling `lookup_weather` directly with a specific
address, which avoids unnecessary name resolution.

Read-only MCP calls are executed concurrently in bounded waves of eight when a
curated preset marks the tool read-only or the MCP tool publishes a read-only
annotation. Unknown and potentially mutating MCP tools remain serial. A
seven-day forecast therefore runs in one wave, while results are returned to
the model in date/call order.

If local validation finds invalid arguments, later calls to the same tool in
that model response are not sent. If an MCP server rejects a locally valid call
with HTTP 400 or 422, calls already active in that bounded wave may also finish
or fail, but later waves for the same tool are skipped. The model receives an
output for every call ID and is asked to correct one request before batching
more. An exact rejected request is also skipped if repeated in a later tool
round. This bounds malformed provider traffic while retaining parallel valid
lookups.

MCP connection setup, discovery, and tool requests use bounded read deadlines at
both the HTTP/SSE transport and MCP session layers. SSE keepalive traffic does
not make an unanswered tool request unbounded. Cancelling the owning text run
also cancels every MCP coroutine in the active wave and closes their transport
contexts.

See also:

- [User MCP Servers](../user-mcp-servers.md)
- [MCP Service Catalog](../mcp-service-catalog.md)

## Account History And Knowledge Writes

The internal `search_agent_history` and `read_agent_history` tools expose bounded
retained evidence to authenticated owner conversations. The separately revocable
`record_agent_improvement` tool submits memory, skill, or diagnostic candidates.
Per-agent grants and persisted run/session policy govern these tools; account
automatic-saving consent governs whether an eligible new learning becomes active.

Model-facing legacy memory and skill write tools submit candidates for the same
policy. Confirmation tools cannot confer owner approval; owners use authenticated
Knowledge review. Standalone Memory MCP writes create inferred proposals, and
Skill MCP creation produces drafts; owner confirmation and active-skill revisions
use Knowledge. Model-supplied provenance is discarded. See
[Agent Knowledge And Persistence](Agent-Knowledge-And-Persistence.md).

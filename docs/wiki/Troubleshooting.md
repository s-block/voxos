# Troubleshooting

Last updated: 2026-10-31

Fixes for common Voxos problems, from the API, database migrations and sign-in
to voice, MCP servers, webhooks, and phone, SMS and WhatsApp channels.

## API Is Down

Check health:

```bash
curl http://localhost:7100/health
```

Start the API:

```bash
make run
```

If the database is missing migrations:

```bash
uv run alembic upgrade head
```

## Alembic Cannot Create The Vector Extension

If migration `202607050001` reports `permission denied to create extension
"vector"` or says the extension is missing, pgvector is not enabled in the
target database. The app role is intentionally not a superuser, so enable the
extension with a local Postgres admin role and retry migrations:

```bash
make db-enable-vector
uv run alembic upgrade head
```

`make db-enable-vector` expects `DB_HOST`, `DB_PORT`, `DB_NAME`,
`POSTGRES_ADMIN_USER`, and `POSTGRES_ADMIN_PASSWORD` in `.env` or the shell
environment.

## Frontend Cannot Sign In

Check `frontend/.env.local` for Firebase web config and `VITE_API_BASE_URL`.
Check root `.env` for `FIREBASE_PROJECT_ID`. The backend must verify the same
Firebase project that issued the browser ID token.

## Chat Images Cannot Be Added Or Sent

The attach control appears only when the selected catalog model declares image
input. Unknown/custom models are rejected server-side with `409` even if a
client is stale. Current support is image input to text output only.

If upload fails, check that the source is JPEG, PNG, or HEIC, is at most the
configured source limit, is not animated or corrupt, and does not exceed the
pixel/dimension limits. The server normalizes the image before enforcing the
5 MiB default stored-image limit. A partial multi-image upload never starts the
model stream; remove the failing image or retry the preserved draft.

For local Compose, confirm both API and `agent-run-worker` mount
`voxos-attachments` at `/var/lib/voxos/attachments` and use
`ATTACHMENT_STORAGE_BACKEND=local`. For host mode, ensure
`ATTACHMENT_LOCAL_PATH` is writable and persistent. For deployed mode, use
`ATTACHMENT_STORAGE_BACKEND=s3`, configure the bucket/prefix/region, and verify
the task role can list the prefix and get, put, and delete its objects. The
bucket must remain private; do not work around a `403` with public access.

If a historical thumbnail is missing, check the authenticated content request,
the tenant/chat ownership, attachment deletion state, and object-store logs.
The API intentionally returns the same not-found response for a missing object
and a wrong-user request.

## Flightradar24 Is Missing From The MCP Catalog

The catalog always offers **Deployed HTTPS endpoint**. **Local Compose** appears
only when the API has the complete Compose URL registered for the `mcp`
purpose. The root
`docker-compose.yml` sets this automatically, but the API container must be
recreated after pulling the setting:

```bash
docker compose up -d --no-deps --force-recreate api agent-run-worker
make mcp-stack-up
```

Refresh the MCP page after both containers are running. If the local connection
fails, check `make mcp-stack-logs`, confirm `FR24_API_KEY` is set in `.env`, and
confirm both containers are attached to the `voxos_default` network. Do not use
`127.0.0.1:8181` in a custom server entry: inside the API container,
`127.0.0.1` refers to the API container itself.

## Monzo Is Missing Or Cannot Authenticate

The Monzo preset appears only when the API registry contains the exact
`http://monzo-mcp:8000/mcp` URL. Root Compose configures it; recreate the API
and agent worker after pulling the change:

```bash
docker compose up -d --no-deps --force-recreate api agent-run-worker
```

If the container does not start, confirm
`MONZO_MCP_HOST_ENDPOINT_TOKEN_FILE` is an absolute owner-only regular file,
contains at least 32 bytes, and is readable by the configured non-root UID/GID.
Enter the same value as the preset's MCP endpoint bearer. A `401` from the MCP
usually means those two values differ.

If Monzo sign-in does not start, confirm the OAuth client ID and secret and
register the exact callback URI displayed in Voxos. If the callback succeeds
but tools report that authorization is required, approve the new connection in
the Monzo mobile app and reconnect.

Broker `401` or `403` responses indicate a missing, expired, tampered, or
wrong-server delegation. Confirm the API and agent worker use the same
`MCP_CREDENTIAL_DELEGATION_SIGNING_SECRET`, then restart them. Broker `409`
requires reconnecting Monzo; `502` means provider refresh failed.

If container startup rejects the endpoint-bearer file, confirm it is a real
non-symlink regular file, owner-only, at least 32 bytes, and readable by the
configured container user.

Insufficient-permission responses after login normally require approving the
new connection in the Monzo mobile app. Transaction history is also subject to
Monzo's documented 90-day window after the initial five minutes. Financial
writes are intentionally unavailable in the supported Compose setup.

## Provider Key Saved But Runtime Is Not Ready

Some providers support credential storage before worker runtime support. The
Providers page marks those as `Adapter needed`. Runtime-ready voice integrations
are OpenAI Realtime, Google Gemini Live, and xAI Grok Voice Agent.

## OpenAI-Compatible Model Endpoint Cannot Connect

The URL is called by the Voxos backend, not the browser. For root Docker
Compose, use `http://host.docker.internal:<port>/v1`, not `localhost`. Confirm
the model server listens on a host address reachable from Docker and that the
host firewall permits only the intended local/Docker clients. Register the
complete URL under `OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON` with purpose
`text_provider` and kind `local_container`.

For host-mode Voxos, register and use `http://localhost:<port>/v1`. Unregistered
destinations require HTTPS on port 443 and global DNS. Every API and
durable-worker replica that may execute a registered endpoint still needs a
working network route.

Common errors:

- A connection or DNS error: confirm the backend process or container can
  resolve and reach the entered hostname and port.
- HTTP `401` or `403`: enter the endpoint's bearer key again. Saving the custom
  provider with an empty key intentionally selects unauthenticated access.
- HTTP `404`: the base URL normally needs its `/v1` path.
- An empty reply or textual tool syntax: confirm the model supports structured
  OpenAI-style tool calls and that the server has the correct tool-aware chat
  template or parser enabled.

Test reachability from the API container:

```bash
docker compose exec api curl http://host.docker.internal:11434/v1/models
```

The returned model ID must exactly match an ID entered on Providers. See
[Self-Hosted Models](Self-Hosted-Models.md) for current runtime commands and
setup guidance.

## Voice Cannot Start

Check:

- A runtime-supported provider key is saved and active.
- LiveKit credentials are saved.
- The LiveKit dev server or LiveKit Cloud URL is reachable.
- The room token request succeeds.
- The selected provider model and voice are valid for that provider.
- `make worker` is running and the call demand reaches `ready` before the
  configured worker startup timeout.

There is no supervisor HTTP health endpoint. Confirm liveness from the worker
process log `Started demand-driven LiveKit worker supervisor`. A healthy cold
project start then produces this correlated sequence across API and supervisor
logs:

1. `Requested LiveKit project worker`
2. `Assigned LiveKit project worker`
3. `LiveKit project worker registered`
4. `Prepared LiveKit call route`

Correlate events with `runtime_session_id` and the opaque `project_key`. Do not
copy an unfiltered tenant LiveKit URL into logs or support tickets; userinfo,
query strings, fragments, and signed URL values must be treated as secrets.

Common demand or project-worker failure codes mean:

- `worker_start_timeout`: no supervisor became ready before the API deadline.
  Confirm the supervisor is running and connected to the same database before
  increasing the timeout.
- `tenant_project_capacity_exhausted`: that tenant already has the configured
  number of live or idle-retained project fingerprints. Reuse the saved project
  or wait for its idle TTL before rotating again.
- `project_capacity_exhausted`: the cluster-wide live project-lease limit is
  full. Add supervisor capacity or wait for an idle registration to close.
- `project_call_capacity_exhausted`: the shared project registration already has
  the configured number of ready or active calls. Retry after a call finishes.
- `invalid_livekit_settings`: the tenant settings or vault record could not be
  resolved. Save the LiveKit settings again for that authenticated user.
- `livekit_settings_resolution_timeout`: the tenant settings or vault decrypt
  exceeded the per-project resolution deadline. Check database/KMS latency and
  keep that deadline below the caller-facing worker start timeout.
- `livekit_settings_changed`: settings changed after demand was created, or the
  API and supervisor use different `VOICE_ROUTE_SIGNING_SECRET` values. Align
  configuration and start a fresh call.
- `livekit_endpoint_not_allowed`: the supervisor's endpoint policy rejected the
  saved URL. Production defaults to public WSS with globally routable DNS;
  controlled local/private LiveKit deployments must explicitly use
  `VOICE_LIVEKIT_ENDPOINT_POLICY=trusted_private` and still constrain egress.
- `registration_timeout`, `registration_connection_failed`, or
  `registration_failed`: the isolated child could not register with that
  LiveKit project. Check saved credentials, DNS/TLS, and reachability from the
  supervisor host.
- `worker_process_start_failed`, `worker_process_exited`,
  `project_worker_failed`, `agent_server_stopped`, or `agent_server_closed`:
  inspect the adjacent supervisor traceback and host process/memory limits.
- `demand_expired`: the call was not activated or heartbeated before its demand
  TTL elapsed.
- `worker_readiness_expired`: the registration lease changed or expired between
  admission and token creation. Retry the call so readiness is re-established.
- `worker_lease_reclaimed` or `worker_registration_stopped`: the owning
  supervisor or project child was lost; in-process calls are not resumed.
- `demand_already_active`: the same route was replayed after one worker had
  already claimed it.
- `demand_lease_lost`: an active job no longer owns its worker-fenced demand;
  the job shuts down instead of continuing on stale routing authority.
- `retained_project_route_conflict`: a retry attempted to rotate a runtime to a
  different LiveKit project while its previous registration was still retained.

If the supervisor exits immediately with
`VOICE_WORKER_LEASE_TTL_SECONDS must exceed shutdown timeout plus two poll
intervals`, increase the lease TTL or reduce the shutdown timeout or poll
interval. All API and supervisor replicas must also share the same
`VOICE_AGENT_NAME`.

If projects remain pending while one supervisor is healthy, check
`VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_SUPERVISOR` and cluster replica count.
The local cap intentionally leaves excess projects pending so another replica
can claim them; the cluster-wide cap rejects excess project demand.

`no warmed process available for job, waiting for one to be created` is an
expected LiveKit Agents message for the demand-driven worker. Voxos registers
the active project with zero idle job processes and creates a job process when
the call arrives. It is not a registration failure. Use one local `make worker`
process unless intentionally testing multiple supervisor replicas; database
leases fence duplicate project ownership, but duplicate terminals make logs
harder to correlate.

## MCP Server Is Not Available To Agents

Check:

- The server URL is remote HTTPS and uses Streamable HTTP or SSE, or is an
  enabled exact-host local Compose preset.
- Auth is complete if required.
- Server test/discovery succeeded.
- `allowed_tools` is explicit.
- The server is enabled and trusted.
- The agent has tool grants for the discovered MCP tools.

## MCP Tool Reports Invalid Arguments

A `ToolArgumentSchemaError` means Voxos rejected model-produced arguments
before contacting the MCP server. The activity panel shows the failing schema
path, and the bounded tool loop gives the model a chance to submit a corrected
call. An `McpToolCallRejectedError` means the server returned HTTP 400 or 422
after local validation; it is not a connection failure.

For Google Maps Grounding Lite weather, use a specific address directly:
`{"location":{"address":"London, UK"}}`. Use exactly one camelCase location
field. Do not combine `address` and `placeId`, send extra top-level fields, or
pass the `places/` prefix returned by `resolve_names`; Voxos strips that prefix
when it is the only mismatch. A forecast date must be an object with integer
`year`, `month`, and `day` fields.

If the run still cannot recover, open the MCP server in Tools, run its
connection test again to refresh discovered tool definitions, and compare the
tool's current input schema with the failed path. For example, a provider may
require `location` or `date` to be structured objects rather than strings.
Voxos intentionally does not display rejected argument values in these errors.

Several completed `lookup_weather` cards are expected for a multi-day forecast:
the upstream tool accepts one date per call. Voxos runs read-only MCP calls in
waves of at most eight, so a seven-day request can show seven successful cards
from one tool-call wave. If one request is rejected by the server, other calls
already active in that wave may also report a result, but later same-tool waves
are skipped and the model is prompted to repair one request.

## Chat Stop Leaves A Run Active

The Chat control should be labeled **Stop run**. It cancels the durable run ID
from the active stream and aborts the browser request. The backend records the
turn as `cancelled`, propagates the request to child runs, and interrupts the
current provider await or every tool call in the active parallel wave. A
disconnected inline chat stream is also finalized as cancelled.

If an older local build only says **Stop stream**, restart both the API and
frontend with the current code. Inspect Operations after stopping: the run
should leave `running`/`cancelling`, release its lease, and show a
`run_cancelled` event. If an MCP server accepts a request but never responds,
the MCP session read deadline is the independent backstop; check the API log
for a bounded connection failure rather than an indefinitely active tool card.

## Agent Webhook Does Not Start A Task

Open **Operations → Webhooks** and confirm that the definition is enabled. A
deleted, disabled, or unknown webhook returns `404`. An active definition with
a missing, old, duplicated, or malformed token returns `401`; rotate the token
and replace the caller's saved value when its status is uncertain. Send the
token in exactly one supported header, not in the query string.

The delivery must be an `application/json` object no larger than 16 KiB. Arrays,
invalid JSON, and unsupported content types are rejected before enqueueing. If
the source retries, send a stable `Idempotency-Key` so a timeout or `503` can
wake the same durable run instead of creating another task. A `503` after
enqueue can mean the worker launch failed even though the run exists; inspect
Operations by the returned or previously recorded run id and check
`agent-run-worker` logs before changing the definition.

If the run remains queued, start `make worker-runs` in host development or
confirm the Compose `agent-run-worker` is healthy and uses the same database as
the API. A run that fails during execution commonly needs an active text
provider and valid credential for the webhook owner. The JSON body cannot grant
tools; review the selected agent's existing tool grants when the task cannot
perform an expected action.

## MCP OAuth State Signing Is Not Configured

If **Start sign-in** reports `MCP OAuth state signing is not configured`, add a
stable random value to the root `.env`:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Save the output as:

```dotenv
MCP_OAUTH_STATE_SIGNING_SECRET=replace-with-the-generated-value
```

Do not copy the placeholder literally, reuse a provider key, or commit the
value. Recreate the Compose API container so it reloads `.env`:

```bash
docker compose up -d --no-deps --force-recreate api
```

For host-mode development, stop and restart `make run`. Then choose **Start
sign-in** again; a state created before the secret was added or rotated cannot
be reused.

OpenBnB uses OAuth metadata discovery, dynamic client registration, and PKCE,
so its Voxos catalog flow does not need an OpenBnB client ID or client secret.
If provider approval completes but the MCP card still says `needs auth`, return
to the Voxos tab and select **Finalize connection** once. Also confirm that the
callback shown in the setup panel is reachable by the browser and registered by
the provider. The local OpenBnB flow supports the default loopback callback at
`http://localhost:7100/api/mcp/auth/callback`; providers that reject loopback
callbacks require a public HTTPS `VITE_API_BASE_URL`.

## Phone Webhooks Or Media Streams Fail

Check:

- `API_PUBLIC_BASE_URL` is set to a public `https://` URL.
- The provider webhook points to the Voxos route.
- Twilio/Telnyx signatures are valid.
- The local tunnel is running if using ngrok.
- Websocket routes use `wss://` when provider media streams are enabled.
- `make worker` is running, can read `voice_call_demands`, and can resolve the
  persisted call tenant's LiveKit settings through tenant RLS and the vault.

## Telnyx SMS Does Not Arrive Or Reply

Open Phone, expand the Telnyx integration, and use **Verify** before changing
configuration. Troubleshoot from the earliest missing evidence:

1. Find the test in Telnyx Message Detail Records by time and safe message ID.
   Confirm the account balance, number status, SMS capability, sender
   registration/compliance, and destination permissions.
2. Check Telnyx webhook attempts. The number must be assigned to the stored
   enabled messaging profile, using webhook API v2 and the current public Voxos
   SMS URL. A changed ngrok hostname requires a confirmed Repair.
3. Check the Phone activity panel. No message row after a successful provider
   send means the provider webhook did not reach or pass signature/resource
   validation. Do not troubleshoot the LLM first.
4. If the reply is `suppressed`, the exact sender number is not a saved contact.
   Add the contact and send a new SMS; the older suppressed message is never
   replayed automatically.
5. If the message row exists, inspect its agent-run and reply states. A queued
   run requires `make worker-runs` in host development (or the default Compose
   `agent-run-worker`), the same database/vault settings as the API, an active
   text provider, and a valid text credential. Inbound reply delivery does not
   use the agent's send-SMS tool grant.
6. If an outbound reply exists, follow its Telnyx message ID to the final
   recipient status. `delivery_unconfirmed` is not treated as delivered;
   `sending_failed` and `delivery_failed` retain only a safe error code/title.
7. If the reply is `generated` or `dispatching`, inspect the worker failure near
   the correlated run. `provider_accepted` means Telnyx accepted the submission,
   not that the handset received it. `ambiguous` means submission may have
   occurred but Voxos did not receive a conclusive result; do not manually
   replay the run without first reconciling the provider message records.

Telnyx reports international inbound and outbound SMS as number capabilities,
not messaging-profile switches. Voxos Verify or Repair can update the assigned
profile and product but cannot turn a false international capability on. Ask
Telnyx support for a number that explicitly reports the required capability;
do not replace a UK mobile merely because another inventory result says `sms`.
Telnyx UK coverage checked on 2026-07-21 reported `international_sms: false`
for mobile numbers, and the normal UK mobile inventory returned no
`international_sms` result.

A response can span multiple SMS segments even when it looks short. GSM-7 uses
160 units for one segment and 153 for multipart messages, while Unicode uses 70
and 67 UTF-16 units. Curly punctuation or emoji can switch the whole message to
Unicode. Voxos records its estimated encoding and segment count and attempts one
semantic condensation pass; it never fixes overflow by cutting the answer at a
raw character boundary.

Telnyx retries slow or unsuccessful webhooks. Voxos claims inbound messages by
provider message ID before enqueueing, so a retry should still show one message
and at most one run. Record timestamps and safe IDs only; do not copy message
bodies, complete phone numbers, webhook route keys, API keys, or raw payloads
into logs or support notes.

Outbound status sends use their per-message status callback while inbound
traffic continues to use the messaging profile webhook. If inbound messages
appear on the status route or final outbound state never arrives, inspect the
profile-versus-message webhook settings and run Verify again.

## WhatsApp Does Not Link Or Reply

Start with the prerequisite:

1. The exact number must be a connected Twilio or Telnyx phone resource in the
   current Voxos tenant.
2. That same E.164 number must be accepted by the WhatsApp Business mobile app.
   A Telnyx/Twilio number appearing in Voxos does not guarantee WhatsApp will
   accept it for mobile-app registration.
3. The API, `agent-run-worker`, and `whatsapp-worker` must share the same
   database and secret-encryption/KMS configuration.

For host development, run:

```bash
make worker-runs
make worker-whatsapp
```

For Compose, run:

```bash
make up
docker compose --profile whatsapp ps whatsapp-worker
docker compose --profile whatsapp logs whatsapp-worker
```

The container healthcheck requires a fresh worker health file, a successful
database poll, and a connected bridge. Sustained failed or stalled polls cause
the worker to exit so Compose restarts it automatically. If it exits before
polling, check migrations, database/RLS access, vault encryption configuration,
the Node executable, and the compiled bridge script. Node.js 20 or newer is
required for a host worker.

If the UI shows no eligible number, reconnect the Twilio or Telnyx integration
and phone resource. Setup options exclude disabled, deleted, non-E.164, and
wrong-tenant resources. Voxos currently permits one linked WhatsApp session per
user; disconnect the existing one before selecting another number.

For a new mobile registration, choose a verification method immediately before
requesting the code in WhatsApp Business. The capture window is ten minutes and
matches the exact selected provider resource.

- For Telnyx, choose **Verify with a call**, then choose **Call me** in WhatsApp
  Business. The incoming setup call should be answered without starting the
  normal voice agent. WhatsApp can first request a varying DTMF digit; Voxos
  extracts the digit from the final prompt transcript, sends that exact digit,
  then collects the spoken code across subsequent transcript events. The
  spoken code should appear in the panel. If it does not, check the signed
  Telnyx voice webhook, `call.transcription` delivery, and the safe
  `verification_dtmf_sent` event summary.
- Choose **Verify with SMS** for Twilio or Telnyx. If a code does not appear,
  confirm that the provider's SMS webhook is publicly reachable and receiving
  the message. It must pass the normal signature/account/number checks and
  contain a plausible numeric code. The parser accepts variable formatting and
  a bounded four-to-eight-digit code; users and operators must not assume a
  particular length or grouping.

If there is no inbound provider event, stop debugging the frontend or agent
worker: Voxos never received the registration traffic. Check the number's
international-inbound capability and switch to the call fallback. A domestic
SMS test proves only domestic routing and does not prove that an international
OTP route will work.

Arm Voxos before every WhatsApp request. If the capture window expired or a
previous attempt was cancelled, select **Verify with a call** or **Verify with
SMS** again and wait for the corresponding waiting state before requesting the
code. Caller numbers and requested DTMF digits can vary, so neither should be
allowlisted as a fixed value. If WhatsApp imposes a retry timer, wait for it to
finish; repeated requests can extend the lockout.

A code delivered outside the active window follows the normal provider path.
If the wrong code is captured, choose **Not the WhatsApp code**. An SMS is
replayed once through normal SMS handling; a voice candidate is deleted.

Voxos cannot enter the registration code into the mobile app. Enter the shown
code manually, wait for WhatsApp Business to confirm the number, then choose
**I entered this code**.

If the number is already registered, select **It is already registered** and
then **Link WhatsApp**. If the panel says it is preparing a link code, keep the
worker running: the challenge API can return
`challenge_not_ready` while the bridge creates a code, and the frontend polls
again. QR and pairing codes expire after the configured challenge TTL; use
**Refresh code** rather than scanning an obsolete image. The UI intentionally
hides the code while settings are read-only.

For a pairing code, enter it under WhatsApp Business **Linked devices** using
**Link with phone number instead**. Do not enter it as the mobile app's initial
account-registration SMS code; these are two separate codes and steps.

`number_mismatch` means the authoritative WhatsApp PN identity differs from the
selected provider number. `pn_identity_unavailable` means WhatsApp reported
only an LID or another identity that cannot prove the phone number. Neither can
be overridden in the browser. Voxos removes the attempted linked device and
requires a fresh link for the correct WhatsApp Business account.

`needs_relink` means WhatsApp revoked or invalidated the companion session.
Confirm that the mobile account is still active, then use **Relink WhatsApp**.
`reconnecting` can be transient; persistent reconnecting usually means the
worker cannot restore the encrypted session or WhatsApp no longer accepts it.
`waiting_for_capacity` means all configured session slots are occupied; add a
worker replica or raise `WHATSAPP_MAX_SESSIONS_PER_WORKER` only within the
bridge's advertised capacity.

If inbound text appears to do nothing:

- An unknown LID-only sender is recorded with a `suppressed` reply and receives
  no automated response. It remains unlinked until WhatsApp supplies an
  authoritative PN/LID mapping that matches a saved contact. Send a new message
  after the contact is linked; an older suppressed message is not replayed.
- A stored identity marked `conflict` means WhatsApp aliases previously pointed
  at different people or phone numbers. Voxos intentionally withholds saved
  contact context and suppresses automated replies instead of guessing.

1. Confirm the panel is `connected`, not merely linked or reconnecting.
2. Test a new direct or group text from another account. Broadcasts,
   newsletters/status, media, historical message appends, and messages sent by
   the linked account are intentionally ignored.
3. If the activity reply is `suppressed`, confirm that the sender has an exact
   saved-contact match. Otherwise, check that a durable `communication_reply`
   run was queued and that
   `agent-run-worker` has an active text provider and credential.
4. Check the WhatsApp outbox. `queued` needs a connected
   `whatsapp-worker`; `submitting` is in flight; `sent` means WhatsApp returned
   a message ID; `failed` is terminal; and `ambiguous` means submission may
   have happened and must not be automatically replayed.

If a group participant is shown only as an LID, confirm the linked account can
still see the participant in the group. Voxos checks current group metadata and
the encrypted Baileys LID mapping store. If no authoritative mapping can match
the participant to a saved phone-number contact, Voxos records the group message
but suppresses its automated reply.

The linked-device transport does not yet persist recipient delivery receipts,
so `provider_accepted`/`sent` is not proof that a handset displayed the reply.
For proactive chat messages, the approved action queues delivery only after it
resolves a saved contact and verifies the connected linked-device session.

If disconnect remains in `disconnecting`, check that `whatsapp-worker` is
healthy and has a fresh successful poll before waiting for the remote logout.
The local Compose worker ignores cache artifacts, so test-created Python
bytecode must not restart a live linked-device session. A healthy restarted
worker reclaims an expired session lease and retries the logout safely. Do not
delete database rows manually; that could leave the mobile account's linked
device active.

Finally, protocol or account restrictions can be external to Voxos. WhatsApp
may change companion-device behavior, limit the account, or revoke a device.
Do not bypass those controls. Review WhatsApp and carrier terms, recipient
consent, privacy/data-processing requirements, and bridge dependency licenses
before a production rollout.

## Outbound Call Asks About Contacts Or Loses Its Goal

For a named recipient, the text agent should call `search_contacts` before
asking whether the person is saved. Check the selected agent's Tools grants:
connecting a call-capable resource adds the low-risk prerequisite only when no
explicit decision exists. An explicit `search_contacts=denied` decision hides
the dependent place-call tool. One result proceeds, multiple results require a
choice, and no result requires a number or new contact.

Every new agent-started call must contain both the returned contact ID and a
non-empty objective. If the call connects but starts with generic assistance,
inspect the tenant call row using safe IDs: it should have an
`outbound_objective`, stable `media_participant_identity`, `agent_ready_at`,
and opening state. Do not paste the objective or full destination into logs or
support notes. The runtime prompt snapshot may contain the objective for
auditing because it is tenant-owned persisted context.

No opening should be generated until `answered_at` and `media_ready_at` are
both present. Telnyx `streaming.started` is media evidence only; it must not set
`answered_at`. An immediate callee “hello” cannot win the first-turn race
because input remains disabled until the objective kickoff is scheduled, after
which normal listening and interruption are enabled.

If dialing never begins, distinguish project readiness from per-call readiness:

- `worker_start_timeout` means no project worker registered.
- `voice_agent_readiness_timeout` means explicit dispatch did not reach
  `agent_ready_at` before `VOICE_AGENT_READY_TIMEOUT_SECONDS`.
- `outbound_call_ended_before_agent_ready` means the reservation ended while
  the API was waiting.
- `outbound_media_ready_timeout` means the carrier bridge did not become ready
  after answer.
- `outbound_opening_failed` means bounded generation/playout attempts were
  exhausted.

Use
`GET /api/integrations/{integration_id}/calls/latency?provider=twilio` (or
`telnyx`) for safe sample counts and p50/p95 component timings. It does not
return objectives, phone numbers, transcripts, or raw provider data. Carrier
doubles verify ordering and idempotency, not real delivery or audio latency;
establish a baseline with approved destinations before setting an SLO.

## Telnyx Outbound Call Is Rejected

Voxos checks LiveKit first, then reads the Telnyx number, Voice API application,
outbound voice profile, assignment, enabled state, and non-empty destination
allowlist before reserving worker demand. Use the Phone readiness result to
distinguish LiveKit routing from Telnyx routing. Agent tools also require a
saved tenant-owned contact; authenticated direct API calls require a valid
E.164 destination.

If Telnyx rejects the dial after worker admission, the call activity row shows
a bounded failure code and Voxos cancels the reserved demand. Correlate the
call-session ID with the provider call-control ID and safe Telnyx error code.
Do not immediately retry an ambiguous provider response because outbound call
submission is not durably idempotent across a new request.

If the signed webhook succeeds but the media websocket closes with code `1011`,
look for `Closing Twilio media stream because LiveKit voice routing is not
configured` or the corresponding Telnyx log. Then use the voice failure-code
guidance above; one tenant's routing failure should not stop other project
workers.

If a Telnyx call answers and the persisted agent enters `speaking` but the
caller hears silence, look for `Started Telnyx PCMU media downlink`. That log
means Voxos subscribed to the agent track and sent a valid 20 millisecond,
8 kHz mono PCMU frame to Telnyx. If it is absent, inspect the adjacent
call-session/runtime-session correlation for `media_downlink_failed`.

If the agent cannot hear the caller, look for `Started Telnyx PCMU media
uplink`. It is emitted once after the first valid caller PCMU payload is decoded
and published to LiveKit. If uplink is present but no user speech state or
transcript follows, confirm the carrier bridge published its synthetic track as
LiveKit `SOURCE_MICROPHONE`; Agents RoomIO ignores `SOURCE_UNKNOWN` for user
audio. Then inspect participant subscription and model VAD. If uplink is absent,
inspect the Telnyx stream configuration and incoming media frames before
changing model settings.

The API and isolated voice job configure the `voxos` logging namespace at INFO
without raising third-party SDK log levels. A healthy Telnyx exchange should
make `Activated LiveKit call demand`, `Started Telnyx PCMU media uplink`,
`Started Telnyx PCMU media downlink`, and `Completed LiveKit call demand`
visible using server-derived correlation fields only.

A Telnyx or Twilio call should not end merely because both sides are silent for
`VOICE_INACTIVITY_TIMEOUT_SECONDS`; that timeout applies to direct LiveKit
sessions. Correlate `Activated LiveKit call demand` and `Completed LiveKit call
demand` by runtime session and check their safe `source` and `close_reason`
fields. For phone sources, a normal completion should follow the verified media
participant disconnect, provider stop, an explicit trusted action, lease loss,
or a real model/session failure—not a generic `away` event. A
`dropping pass-through signal — no stream available` warning immediately after
an old 30-second shutdown usually reflects room deletion after the premature
close rather than the original cause.

`media_stream_protocol_error` means Telnyx returned a websocket media error.
Voxos persists a sanitized `media_stream_error` containing only the documented
numeric code, bounded title, and stream identifier; raw details, media bytes,
phone numbers, route keys, and authentication tokens are not logged. Common
provider titles include `invalid_media` and `rate_limit_reached`.

A normal Telnyx `stop` frame or websocket disconnect is awaited during bridge
shutdown and should not produce `Task exception was never retrieved`. If that
warning appears, verify the API is running the current bridge code and capture
the first preceding `media_downlink_failed` or protocol-error log.

## Docs Validation Fails

Run:

```bash
make docs-validate
```

Fix missing `Last updated` lines, broken links, duplicate titles, or a stale
manifest. Regenerate the manifest if page content changed intentionally.

# Voice And LiveKit

Last updated: 2026-10-05

Voice sessions use LiveKit for realtime room connectivity and the Voxos voice
worker for provider-specific realtime model sessions.

## Runtime-Supported Voice Providers

| Provider | Runtime status | Default model | Default voice |
| --- | --- | --- | --- |
| OpenAI | Ready | `gpt-realtime-2.1-mini` | `marin` |
| Google | Ready | `gemini-3.1-flash-live-preview` | `Puck` |
| xAI | Ready | `grok-voice-think-fast-1.0` | `Ara` |

Credential storage also exists for Hume, ElevenLabs, and Mistral. Their listed
voice models are not yet wired into a LiveKit worker runtime adapter.

OpenAI `gpt-realtime-2.1-mini` is the recommended default for routine voice
agent work. Its audio rates are $10 input, $0.30 cached input, and $20 output
per 1M audio tokens; text rates are $0.60 input, $0.06 cached input, and $2.40
output per 1M text tokens. Full `gpt-realtime-2.1` remains selectable for
quality-first, reasoning-heavy conversations at $32/$0.40/$64 per 1M audio
input/cached-input/output tokens and $4/$0.40/$24 per 1M text tokens.

The full model is about 3.2 times the mini model's uncached audio input and
output rates. Use full only when task-specific evaluation shows a material
quality gain. The model selector displays these descriptions and pricing
summaries. Removed OpenAI preview models are no longer suggested, while an
already persisted custom model remains visible so users can migrate it
deliberately.

## LiveKit Settings

Set LiveKit credentials on the Connection page or through the API:

```bash
curl -X PUT "$VOXOS_API/api/livekit/settings" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"livekit_url":"ws://localhost:7880","livekit_api_key":"<key>","livekit_api_secret":"<secret>"}'
```

For local Docker Compose, the LiveKit dev server listens on
`ws://localhost:7880`.

## Voice Session Flow

1. Save a runtime-supported voice provider key on Providers.
2. Save LiveKit credentials on Connection.
3. Open Voice.
4. Keep LiveKit enabled.
5. Confirm room, participant, and agent name.
6. Start voice.

The frontend asks the backend for a LiveKit participant token. The backend puts
a server-signed `voxos_route` in dispatch metadata, binding the local Voxos user,
agent, runtime session, and room. The voice worker verifies that route before it
enters tenant scope and resolves provider preferences or encrypted credentials.
Plain `user_id` metadata is descriptive only and is never tenant authority.

When the authenticated web or Flutter client has foreground location sharing
enabled, it includes one WGS84 snapshot in the token-source request at call
startup. The backend validates freshness and ranges before placing it in the
server-minted participant and dispatch metadata. The worker accepts it only for
an owner `livekit_api` session, adds it to the live provider instructions, and
removes it from persisted room metadata and the durable prompt snapshot. Phone,
outbound-provider, limited-caller, and other non-client voice sources receive no
implicit device location. A missing or unavailable fix never blocks the call.

Phone media bridges join the same tenant LiveKit room as server-owned
participants. Inbound bridge tokens dispatch the agent. Goal-directed outbound
calls explicitly dispatch the agent before dialing, then join the later carrier
bridge through a stable server-created participant identity without requesting
a second dispatch. Telnyx caller audio is decoded from 8 kHz PCMU into LiveKit
PCM; agent audio is resampled back to 8 kHz mono and sent as Telnyx-compliant
20 millisecond PCMU frames. Provider protocol errors and downlink failures are
scoped to the owning call and do not stop the project registration or another
tenant's call.

Telnyx and Twilio publish their synthetic caller tracks with LiveKit source
`SOURCE_MICROPHONE`. LiveKit Agents RoomIO subscribes to microphone publications
for user audio; an unclassified `SOURCE_UNKNOWN` track can carry valid PCM while
still being ignored by the agent input pipeline.

The activated, RLS-protected demand row is also the only authority for session
source. Telnyx and Twilio demands disable the generic LiveKit speech-idle timer,
so ordinary conversational silence does not close a phone call. The verified
provider media bridge is the linked room participant; its websocket stop or
disconnect closes the agent session and room. Direct `livekit_api` demands and
unknown sources retain `VOICE_INACTIVITY_TIMEOUT_SECONDS` behavior.

## Browser Use

Authenticated in-app voice sessions created through `livekit_api` receive the
same single `use_browser` capability as text chat. The voice model supplies a
complete instruction and can either browse from a public HTTPS start URL or
select an opaque saved-profile handle from the assigned label/origin catalog.
This supports public and signed-in browser tasks without a separate voice
workflow or one tool per account.

For saved profiles, execution binds the call to the latest finalized user
utterance and its server-issued message identifier, then rechecks ownership,
agent assignment, profile origins, browser authority, and any hosted
provider-tool grant. Voxos supplies credentials and provider state outside the
model's arguments. Browser tasks default to read-only; state-changing mode is
for actions explicitly requested by the authenticated user.

Browser capabilities with saved profiles are not exposed to Twilio, Telnyx, unknown-source,
or admin-contact phone sessions. Caller ID and the contact admin flag are not
strong enough authentication for stored website credentials. Scheduled tasks
use their separate explicit stored-credential Browser Account binding described in
[Operations Runs And Schedules](Operations-Runs-And-Schedules.md).

Voice instructions include current UTC datetime context when the provider
session starts. The rendered prompt also includes `Voice session started at
(UTC)` so the model can distinguish startup time from older memory or background
context. Voice transcript items persisted by the runtime keep their
timezone-aware `created_at` datetimes.

Realtime sessions can run long enough for startup prompt time to become stale.
The default internal voice tool set includes `get_current_datetime`, which
returns the current server UTC datetime as an ISO 8601 string, and voice prompts
tell the model to call it before answering time-sensitive questions.

Voice receives the same factual-grounding contract as text and messaging. The
model should use an available web-search or authoritative retrieval tool for
current, niche, ambiguous, disputed, consequential, recommendation-based,
source-specific, or uncertain factual claims, and an explicit request to
search or verify always requires a successful search. Stable elementary facts,
calculations, transformations, creative requests, ordinary conversation, and
facts supplied by the caller do not require retrieval. If search fails or is
unavailable, the spoken answer should make material uncertainty clear.

Grounded voice answers name the important sources naturally without reading raw
URLs aloud and can offer to provide links in a follow-up message. The policy is
included in both the authenticated owner prompt and the restricted
caller-safe prompt, so it applies to browser voice and verified phone routes.

All supported voice providers receive the versioned `voice:v1` response
contract after persona instructions. It asks for natural, speakable plain
language, normally one or two sentences/about 30–50 words and one main idea per
turn, with no Markdown, tables, code fences, or raw URLs read aloud. When more
detail is useful, the agent gives the key point and offers to continue instead
of starting a monologue. Initial greetings use the same contract. Google Gemini
Live also receives the profile's supported output-token cap; prompt policy is
the primary guard for providers whose realtime wrappers do not expose that
setting. Voice session prompt snapshots record `voice:v1`.

## Goal-Directed Outbound Calls

Agent-started calls require a saved contact ID and a bounded caller-safe
objective. The call record stores that objective under tenant RLS; it is not
placed in provider payloads, room or participant metadata, activity responses,
signed routes, or operational logs. The worker resolves it only for the exact
tenant, selected agent, carrier, and runtime session, then appends an
`Outbound Call Task` section to the caller-safe prompt before the runtime prompt
snapshot is written.

Outbound initialization and first speech are separate operations for OpenAI,
Gemini, and xAI sessions. The worker starts the realtime session before carrier
dialing with audio input disabled and waits for the stable bridge participant.
It records agent readiness so the API can dial only after provider/model setup
has completed. This starts the system before the callee answers, but it never
generates or plays speech before answer.

The first reply waits for both the carrier-owned `answered_at` signal and
LiveKit `RoomIO` media readiness. The worker atomically claims one pending
opening, schedules a code-owned objective kickoff, enables listening and
barge-in, and records first speech plus completed, interrupted, or failed
playout. A failed opening can be retried while the call remains active;
duplicate webhooks, reconnects, or jobs cannot claim another completed opening.
The objective stays in system context after interruption so later turns remain
goal-directed. The identity introduction is explicitly limited to that first
spoken turn. Later replies continue naturally without restating the agent's
name unless the callee asks who is speaking or appears confused, even when the
persisted objective itself includes an instruction to introduce the agent.

`VOICE_AGENT_READY_TIMEOUT_SECONDS` bounds pre-dial provider/model readiness.
`VOICE_OUTBOUND_ANSWER_TIMEOUT_SECONDS` bounds the answer-plus-media gate.
`VOICE_OUTBOUND_OPENING_TIMEOUT_SECONDS` bounds media readiness and each
opening playout, while `VOICE_OUTBOUND_OPENING_RETRY_ATTEMPTS` limits retries.
Terminal provider state shuts down the prewarmed job.

## Phone Caller Access

For verified Twilio and Telnyx phone routes, the worker resolves caller access
from the persisted call session, not LiveKit room metadata. Incoming calls
match the saved contact's E.164 `from` number; outgoing calls match the `to`
number. The call stores the contact and admin decision once so later contact
edits cannot escalate an active session.

Admin contacts retain the selected agent's normal approved voice policy.
Unknown and non-admin callers receive an intersected policy containing at most
`web_search_exa` from the tenant's qualifying Exa preset server. Qualification
requires the persisted server ID to use a canonical URL from the current `exa`
preset, retain `preset_id=exa` safe metadata, be enabled, trusted, and
connected, explicitly allow the tool, and already be granted in the selected
agent's base policy. A custom server or gateway that exposes a same-named tool
does not qualify. Missing or invalid voice policy snapshots fail closed during
both tool listing and execution.

Limited callers also receive a public caller-safe prompt. It can include the
saved display name, voice settings, current time, and available public tools,
but excludes owner custom instructions, persona details, memories, skills, and
active plans. Authenticated browser voice sessions retain the existing private
instruction path.

Caller ID can be spoofed, forwarded, or reassigned. The contact admin flag is
not a replacement for strong authentication or a future step-up check for
high-impact tools.

## Worker Model

Browser token minting resolves the authenticated tenant's settings, persists
demand, and waits for registration. Verified inbound phone setup persists
demand with the call session, commits it, and waits before returning streaming
TwiML or answering Telnyx; its media bridge uses a dispatch-bearing token.
Outbound setup instead waits for project registration, explicitly dispatches
the call job, waits for the per-call `agent_ready_at` signal, and then dials.
Its later bridge token has no `agent_name`:

```bash
make worker
```

The supervisor does not register every configured tenant. It starts one isolated
child per demanded credential fingerprint, shares it for simultaneous calls
with identical settings, and retains an unused child through the idle TTL for
burst reuse before draining it. One project's bad credentials or child failure
does not stop other projects.

One supervisor resolves up to `VOICE_WORKER_START_CONCURRENCY` demanded
projects concurrently. Each tenant settings lookup is bounded by
`VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS`, and a completed healthy
project publishes readiness without waiting for slower projects.

Do not set LiveKit project credentials in `.env`. The supervisor derives tenant
authority from an RLS-protected demand row, re-enters that tenant scope, and
decrypts only the settings needed for the active project. The internal
`VOICE_ROUTE_SIGNING_SECRET` remains application config for signed route
metadata and opaque project fingerprints.

Every API and supervisor replica in one environment must share
`VOICE_ROUTE_SIGNING_SECRET` and `VOICE_AGENT_NAME`. The lease TTL must exceed
the shutdown timeout plus twice the supervisor poll interval. Live project
leases are bounded cluster-wide and per supervisor, live or idle-retained
fingerprints are bounded per tenant, and ready/active calls are bounded per project by the
`VOICE_WORKER_MAX_*` settings (including
`VOICE_WORKER_MAX_CALLS_PER_PROJECT`, default `32`). The `make up` command
enables Compose's long-running `voice-worker` profile; its saved LiveKit URL
must resolve from both caller and container. The API persists demand and waits
for that supervisor to register the required project worker. Database demand
and lease coordination fence duplicate work across supervisor replicas.

The default `VOICE_LIVEKIT_ENDPOINT_POLICY=public_wss` requires WSS and
globally routable DNS immediately before a child starts. Local Compose uses the
explicit `trusted_private` policy for its private plain-WS server. This check is
not a substitute for production egress controls because DNS can change before
the SDK connects or reconnects.

See [Demand-Driven Multi-Tenant LiveKit Workers](../livekit-multi-tenant-worker-architecture.md)
for lifecycle constraints, process isolation, RLS, and tradeoffs.

## Owner History And Learning

Verified owner LiveKit sessions can use the same granted account history and
improvement tools as text chats. Phone caller permissions do not grant account
history access, including admin-contact calls. Reading retained phone speech
from an owner conversation requires that call's persisted transcript permission.
An owner session closed with an error queues bounded analysis for the durable
worker, which uses the saved text provider. Review and automatic-saving settings
are in [Knowledge > Improvements](Agent-Knowledge-And-Persistence.md).

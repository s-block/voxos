# Phone Integrations

Last updated: 2026-08-21

The Phone page connects communication providers, stores phone credentials,
registers phone-number resources, and routes voice, SMS, or WhatsApp events
into Voxos.

## Supported Providers

| Provider | Current capabilities |
| --- | --- |
| Twilio | Credentials, phone number resource connection, inbound/outbound voice, inbound/outbound SMS, status webhooks, media streams, tool actions. |
| Telnyx | Phone setup, dedicated voice and messaging routing, inbound/outbound voice, inbound/outbound SMS, media streams, tool actions. |
| WhatsApp linked device | Link one existing Twilio or Telnyx number through the WhatsApp Business mobile app, receive live direct and group text, send durable agent replies, and send approved messages to saved contacts. |

All integration secrets use the tenant-scoped user secret vault.

## Twilio Setup

The Twilio form asks for:

- Label.
- Account SID.
- API Key SID.
- API Key Secret.
- Auth Token.

After credentials are saved, Voxos can list candidate phone numbers. Select the
phone number resources and capabilities to connect. Voxos creates webhook routes
for voice, SMS, status, and media stream handling.

## Telnyx Setup

The Telnyx form asks for:

- Label.
- E.164 phone number.
- API key.
- Webhook public key.
- Confirmation that Voxos can assign the number to Telnyx routing.
- Optional transcript storage.

Voxos first checks that the selected number is active and exposes both voice
and two-way SMS. It then creates dedicated Telnyx voice and messaging profiles,
attaches the number when confirmed, and stores only the resulting safe provider
identifiers and resource metadata. Setup fails with a capability-specific error
instead of marking an unsupported number as ready.

### Telnyx Verify And Repair

Each connected Telnyx card has a read-only **Verify** action. It checks the
number and its current assignments, the messaging profile and API v2 webhook,
the Voice API application, the outbound voice profile and configured
destination allowlists, the durable reply prerequisites, and LiveKit routing.
The result reports inbound SMS, outbound SMS, inbound calls, outbound calls,
agent reply processing, and LiveKit independently.

**Repair** requires an explicit browser and API confirmation. It updates the
existing Voxos-owned number assignments, webhook URLs, API versions, and
enabled states where they have drifted. It does not create replacement profiles
on every check. Missing provider objects require reconnecting the integration
instead of silently creating duplicates. Because the current public base URL is
used during verification and repair, restart the API with the intended stable
`API_PUBLIC_BASE_URL` before repairing an old tunnel URL.

These checks are available through tenant-scoped API routes:

- `GET /api/integrations/{integration_id}/telnyx/readiness`
- `POST /api/integrations/{integration_id}/telnyx/repair`
- `GET /api/integrations/{integration_id}/messages`
- `GET /api/integrations/{integration_id}/calls`
- `GET /api/integrations/{integration_id}/calls/latency`

Readiness and activity responses use full remote phone numbers and bounded safe
provider errors. They do not return credentials, route keys, message bodies, or
raw Telnyx payloads.

## WhatsApp Linked Device

Voxos links a WhatsApp Business mobile account as a companion device so the
selected agent can receive and reply to direct and group text messages sent to
a connected Twilio or Telnyx number. The mobile app remains the primary account
and may occasionally require relinking.

Twilio or Telnyx number rental, SMS or voice verification, mobile data, and
other carrier costs can apply. WhatsApp decides whether a number is eligible
for mobile-app registration and may change linked-device behavior or restrict
an account.

### Before You Start

The current release supports one linked WhatsApp number per Voxos user. A
single deployment remains multi-tenant: each user owns a separate encrypted
session, resource, conversation, and outbox.

### Choose The Number

The number shown to customers is the number registered in WhatsApp Business,
so select the dedicated Voxos business number rather than a team member's
personal WhatsApp number. That exact E.164 number must already be a connected
Twilio or Telnyx phone resource in the same Voxos account.

Phone-number shape and messaging reach are separate properties. A number can
look like a UK mobile and support domestic SMS while still rejecting an OTP
sent through an international application-to-person route.

| Number type | Registration guidance |
| --- | --- |
| Telnyx UK mobile | Supports ordinary SMS and voice, but Telnyx country coverage currently reports `international_sms: false`. Use **Verify with a call** for WhatsApp registration. |
| Twilio UK mobile | Twilio documents domestic and international messaging support. Confirm the actual number's capabilities and keep voice available as a fallback. |
| UK local, national, or landline | Do not assume SMS support. WhatsApp Business can use a voice call when the selected provider resource can receive the automated call. Voxos currently automates that call flow for Telnyx resources. |
| Toll-free, premium, shared-cost, VoIP, or other programmable DID | Eligibility and OTP delivery vary by carrier and WhatsApp. Test registration before assigning the number to customers. A connected Voxos resource is not, by itself, proof of WhatsApp eligibility. |

Telnyx exposes `sms` and `international_sms` as distinct inventory features;
messaging-profile assignment cannot convert one into the other. Twilio also
distinguishes domestic-only from globally SMS-enabled numbers. Check the
current [Telnyx number-search coverage](https://developers.telnyx.com/docs/numbers/phone-numbers/number-search)
or [Twilio SMS number capabilities](https://help.twilio.com/articles/223133887-Can-I-use-my-Twilio-number-for-SMS)
before buying or replacing a number.

An international sender does not normally make a UK subscriber pay to receive
an ordinary SMS while in the UK; carrier and roaming terms still apply. For
example, O2 states that receiving a call or message in the UK from overseas has
no additional charge in its
[international calling guidance](https://www.o2.co.uk/help/international-and-network/international-calling/calling-abroad-standard-rates).
The practical risk for programmable numbers is nondelivery, because the number
or upstream carrier may not accept the sender's route.

### Register And Link

Before opening the WhatsApp setup panel:

1. Connect the exact E.164 phone number to Voxos through Twilio or Telnyx. The
   source integration and phone-number resource must both be connected.
2. Install and open WhatsApp Business on a phone. If it offers the phone's
   existing personal number, choose **Use a different number** and enter the
   dedicated Voxos number.
3. Open **Phone** at `/integrations`, find **WhatsApp linked device**, select
   the same Twilio or Telnyx number, shown in full, and arm the capture method *before*
   requesting the code in WhatsApp Business:
   - For a Telnyx number, choose **Verify with a call**, then choose **Call me**
     in WhatsApp Business and keep the Voxos page open. WhatsApp may first ask
     the receiver to press a digit. The requested digit varies between calls;
     Voxos reads the prompt, sends that digit, captures the spoken verification
     code, and displays it in the panel.
   - Choose **Verify with SMS** only when the selected number can receive the
     route WhatsApp uses. Voxos captures a numeric verification-code SMS for
     the exact selected resource during the ten-minute window.
4. Enter the code shown by Voxos into WhatsApp Business. Code length and spoken
   grouping can vary, so use the complete code displayed rather than assuming
   a fixed format. Wait for the app to confirm registration, then select
   **I entered this code** in Voxos.
5. Choose **QR code** or **Pairing code** in Voxos. In WhatsApp Business, open
   **Linked devices**, choose **Link a device**, then scan the QR or use
   **Link with phone number instead** and enter the temporary code.
6. Keep the page open until it shows **Number verified and connected**. The
   panel records separate verified and connected timestamps.

If the number was already registered, choose **It is already registered** and
start directly at the linked-device step.

The panel polls the authoritative backend state and changes to **Connected**
with verified and connected timestamps when the link is accepted. Temporary
registration codes and linked-device challenges are returned with
`Cache-Control: no-store` and are kept only in React memory. Linked-device
challenges expire and can be regenerated.

The Twilio or Telnyx integration supplies the expected number and default agent.
Its authenticated SMS webhook can deliver the one-time registration code to
the short setup window. For Telnyx voice verification, the signed voice webhook
answers the active setup call and starts inbound transcription without routing
the call to the normal voice agent. After linking, WhatsApp traffic uses the
linked-device socket. Provider webhooks still need a public
`API_PUBLIC_BASE_URL`.

### If The Registration Code Does Not Arrive

Do not repeatedly request new WhatsApp codes. WhatsApp applies retry cooldowns,
which can prevent both SMS and call attempts for an hour or longer.

1. Leave the number unchanged and check whether the provider received an SMS
   or call at the time of the request.
2. If the SMS never reached the provider, Voxos cannot recover it. The usual
   cause is number capability, international A2P reach, or upstream filtering;
   use **Verify with a call** when available.
3. For a voice retry, cancel an expired Voxos attempt, choose **Verify with a
   call** again, wait until the panel says it is waiting, and only then request
   **Call me** in WhatsApp Business. Each call must have an active capture
   window; caller ID and the requested DTMF digit can change.
4. If the provider received the event but Voxos did not show a code, verify the
   public webhook route and provider assignment. For Telnyx, use **Verify** on
   the phone integration before **Repair**; Repair can correct routing but
   cannot add an unsupported international-SMS capability.
5. If WhatsApp shows a cooldown, wait for its timer to expire before arming one
   fresh attempt. Requesting more codes during the cooldown does not help.

The SMS and call routes are alternatives for the same initial mobile
registration. After WhatsApp Business accepts either code, the QR or pairing
code is a separate linked-device step.

### Automatic Number Validation

Capturing a mobile-registration SMS or spoken code proves only that the
tenant's provider resource received a code. It does not authorize the number
or mark the WhatsApp integration connected.

The backend accepts SMS candidates only after the normal Twilio or Telnyx
signature, provider-account, route, and exact resource-number checks. A Telnyx
verification call must arrive through the signed voice route for the same
tenant integration and resource while **Verify with a call** is armed. Voxos
does not request a LiveKit worker for that call and does not create agent or
call-transcript rows. The temporary record, SMS envelope, spoken transcript
fragments, and parsed voice candidate are tenant-scoped and encrypted in the
user secret vault. A rejected SMS candidate is replayed once through the
provider's normal SMS path; a rejected voice candidate is deleted. Outside the
ten-minute setup window, normal SMS and voice routing is unchanged.

After WhatsApp establishes the companion session, the bridge reports its
phone-number (PN) identity. The backend normalizes that authoritative PN and
compares it with the selected tenant-owned Twilio or Telnyx resource. Only a
PN-bearing identity proof for the exact number can enable the integration,
resource, inbound processing, and outbound replies. A linked-device ID (LID)
alone is insufficient.

If the numbers differ, or WhatsApp cannot provide a PN identity, Voxos rejects
the validation, removes the attempted linked device, clears its local auth
state after confirmed logout, and shows that relinking is required. The
frontend never sends a user id, identity result, or
"validated" flag as authority.

### Runtime And Session Storage

`make worker-whatsapp` runs the Python supervisor and builds/starts its private
Node.js bridge. The complete Compose equivalent is:

```bash
make up
```

This is a shared multi-session service, not one operating-system process per
user. One worker owns one private stdio bridge and multiplexes up to
`WHATSAPP_MAX_SESSIONS_PER_WORKER` live linked-device sockets, 25 by default.
Database leases and generations fence duplicate workers and stale events, so
multiple worker replicas can share the same database. A connected WhatsApp
number still needs a live socket; durable credentials allow another worker to
restore that socket after a restart without scanning a new QR unless WhatsApp
has revoked the linked device.

Baileys credentials and Signal keys are never stored in a filesystem auth
directory. Voxos generates a per-integration data key, seals it in the tenant's
user secret vault, and AES-GCM encrypts each auth value and temporary challenge
in Postgres with tenant, integration, key identity, and revision bound as
authenticated context. The bridge child has no HTTP listener, database
credentials, vault credentials, or filesystem session store. Plaintext auth is
limited to the owning worker and bridge memory while needed.

Mobile-registration SMS envelopes and voice-verification data use a separate
vault purpose and are deleted when setup completes or is cancelled. The
WhatsApp worker has no cross-tenant access to the registration table or vault
entry.

### Inbound Messages And Agent Replies

The integration accepts live direct and group text notifications. It ignores
broadcasts, status/newsletter traffic, media, history message appends, and
messages sent by the linked account. History synchronisation may still enrich
identity mappings; it does not import old chat messages into agent context. An
approved agent can send a proactive WhatsApp message to a saved contact through
a connected, validated linked session. The action requires `search_contacts`
first, accepts the returned contact ID rather than a free-form number, and
queues the message for durable delivery by `whatsapp-worker`.

For each direct message, Voxos reads `remoteJid` and `remoteJidAlt`. For a group
message it reads `participant` and `participantAlt`. Each value is classified
with Baileys' PN and LID predicates instead of assuming that an identifier's
digits are a phone number. A PN JID is authoritative phone identity. An LID is
authoritative WhatsApp identity but is not itself a phone number.

When a message contains only an LID, the bridge checks contact records, current
group participant metadata, Baileys' Signal LID mapping, and mappings learned
from live mapping events or history synchronisation. Signal mappings remain in
the integration's encrypted remote-auth state and survive worker restarts. A
verified PN/LID pair is stored in one tenant- and integration-scoped identity
record. When the PN produces an exact E.164 match to a saved contact, the
inbound message and durable agent run retain that contact ID and display name.
WhatsApp display names are labels only and never establish contact identity or
access.

If no PN mapping exists, Voxos retains the participant using the LID without
deriving a number from its digits or guessing a saved contact. The message is
recorded, but its reply is marked `suppressed` because the sender cannot be
matched to a saved contact. A later authoritative PN/LID observation enriches
the same identity and can link it through exact E.164 contact matching for new
messages; it does not replay an older suppressed message. Conflicting aliases
are marked as a conflict and remain ineligible for automated replies.

Direct conversations are keyed by the sender's stable WhatsApp identity. Group
conversations are keyed by the group JID, so recent live messages from different
participants share the same bounded agent context with a sender label on each
inbound turn. Replies are sent back to the originating direct chat or group.
Saved-contact admin permissions are not applied to group messages because tool
results would be visible to other group participants.

An inbound message is acknowledged to the bridge only after Voxos has durably
claimed the provider message ID, stored the tenant-owned message, resolved its
per-peer conversation, and recorded a reply disposition. A saved contact queues
one `communication_reply` run. An unknown sender receives one idempotent
`suppressed` reply record with no run, transcript, outbox item, or automated
response. Duplicate notifications reuse the original disposition. Admitted
conversations use the `whatsapp:v1` mobile-response profile and the same
low-risk inbound tool-policy boundary as SMS.

`make worker-runs` generates the response and writes one durable WhatsApp
outbox item. `make worker-whatsapp` sends queued items only while the exact
tenant session remains validated and connected. Outbox state advances through
`queued`, `submitting`, and then `sent`, `failed`, or `ambiguous`. A returned
WhatsApp message ID marks the reply `provider_accepted`; it is not proof that a
recipient handset displayed the message because this transport does not yet
persist delivery receipts.

Retryable failures before a conclusive submission can return an item to
`queued`. A lost or uncertain submission result becomes `ambiguous` and is not
automatically resent. Worker leases and generations prevent a stale process
from completing another worker's item. On disconnect, queued items fail and an
item already submitting becomes ambiguous.

### Relink And Disconnect

Use **Relink WhatsApp** when the companion device is revoked or the panel shows
`needs_relink`. Relink first requests confirmed remote logout, deletes the old
encrypted auth state, advances the worker generation, and then presents a fresh
QR or pairing code for the same source number.

**Disconnect WhatsApp** immediately disables new delivery and shows
`disconnecting`. The worker confirms remote logout before it deletes session
keys and challenges, removes the vault data key, revokes the credential, and
removes the integration. If logout is uncertain, Voxos retains the encrypted
state and retries instead of claiming that the device was removed. To use a
different source number, disconnect the current integration completely and
start a new link.

Authenticated setup routes are:

- `GET /api/integrations/whatsapp/options`
- `POST /api/integrations/whatsapp/link`
- `GET /api/integrations/whatsapp/{integration_id}/status`
- `GET /api/integrations/whatsapp/{integration_id}/challenge`
- `POST /api/integrations/whatsapp/{integration_id}/challenge`
- `POST /api/integrations/whatsapp/{integration_id}/relink`
- `DELETE /api/integrations/{integration_id}`

### Operational And Compliance Limits

WhatsApp can change companion-device behavior, revoke sessions, rate-limit
accounts, or take enforcement action. There is no production compatibility or
delivery SLA. Use the integration only for numbers, recipients, and content you
are authorized to process, follow WhatsApp and carrier terms and opt-in rules,
and complete a privacy, data-processing, acceptable-use, and dependency-license
review before production rollout.

## SMS Processing And Delivery

A valid signed `message.received` webhook is matched to the configured phone
resource, durably claimed by Telnyx message ID, and stored before Voxos returns
success. An exact saved-contact match produces one reply record and at most one
queued communication-reply run. An unknown number is retained in activity with
a `suppressed` reply and receives no generated or outbound message. Retries
reuse the original disposition, so adding the number as a contact later affects
only new messages. Voxos binds each message to a reusable SMS conversation
isolated by tenant, provider, integration, phone resource, remote sender, and
agent. The active session rolls over after 24 hours of inactivity; different
senders and web-chat or voice sessions do not share its transcript.

The model produces response text, but it does not send the reply or select its
destination. The communication adapter derives the provider, source resource,
and verified inbound sender from tenant-scoped records, validates the complete
response, and submits it. The proactive send-SMS tool remains available for
approved agent actions, but it is not part of an inbound reply run.

Inbound SMS uses the versioned `sms:v1` response profile: plain text, direct,
normally one or two short sentences, with one segment preferred and two
segments the ordinary limit. Voxos calculates GSM-7 septets (including the
extension table) or UTF-16 code units rather than counting Python characters.
One GSM-7 segment holds 160 units and multipart segments hold 153 each; Unicode
uses 70 and 67 UTF-16 units. Meaningful Unicode and the user's language are
preserved. If a generated answer is too long, Voxos performs one tool-free
semantic condensation pass. If it is still outside the supported envelope, it
sends a complete short fallback instead of slicing or silently dropping it.

The inbound peer is not an authenticated Voxos user. Its run policy is the
intersection of the agent's grants and a low-risk channel allowlist. Current
time and product-doc tools may be used when granted; an explicitly granted
`web_search_exa` tool may also qualify only through the tenant's enabled,
trusted, connected server whose persisted Exa preset identity and URL match
the catalog.

An exact E.164 sender match to a saved contact marked **Admin** may additionally
receive `search_contacts`, but only when the selected agent already has that
internal-tool grant. The tool returns contact IDs, display names, and full phone
numbers. Unknown and non-admin senders cannot access contacts.
Even admin SMS senders cannot receive `create_task_plan`, integration delivery,
credentials, private memory, other mutating tools, or arbitrary outbound
actions through this channel. `create_task_plan` records planning data; it does
not schedule or send a message. The communications adapter, not an LLM tool
call, delivers the conversational reply to the verified inbound route.

If no allowed tool can establish a current fact, the SMS profile requires a
short honest limitation or focused follow-up question. The Admin flag relies on
caller ID and is not strong authentication, so higher-impact actions require a
dedicated confirmed and preferably step-up-authenticated workflow.

`agent-run-worker` is part of the default root Compose stack. Host-based local
development must run `make worker-runs` in a separate terminal. A stored inbound
message with a queued run cannot produce an automated reply while that worker
or the user's active text provider is unavailable.

Reply state is separate from carrier message state. Activity can show
`pending`, `generated`, `dispatching`, `provider_accepted`, `delivered`,
`failed`, `suppressed`, or `ambiguous`, together with the response profile,
estimated encoding, and segment count. A run succeeds only after provider
acceptance. Delivery callbacks remain authoritative for final delivered or
failed state. If a process loses the provider result after submission begins,
the reply becomes `ambiguous` and automatic resend is blocked.

Outbound Telnyx messages use a per-message status callback and disable messaging
profile callback precedence for that send, leaving the profile webhook for
inbound messages. Final state comes from each recipient in `payload.to[].status`:
`delivered` is delivered, `sending_failed` and `delivery_failed` are failed, and
`delivery_unconfirmed` remains a distinct terminal non-delivered state. Only
safe error codes and titles are retained for ordinary activity views.

Sender registration and allowed destinations remain Telnyx/account
requirements. US long-code and toll-free traffic can require 10DLC registration
or toll-free verification; verify the applicable number type, balance,
permissions, and compliance state in Telnyx before testing.

## Public URL Requirement

Provider webhooks need a public HTTPS API base URL. For local development, use
`make ngrok`, then set `API_PUBLIC_BASE_URL` to that `https://` URL before
configuring provider callbacks.

Webhook routes are under `/api/integrations/webhooks/...`. Media stream routes
use websocket paths under `/api/integrations/streams/...`.

## Voice Worker Routing

As part of the same tenant transaction that creates a verified voice call
session, Voxos records demand for that call's saved LiveKit project. It commits
the demand and waits for registration before returning Twilio streaming TwiML,
answering an inbound Telnyx call, or preparing an outbound call. Twilio and
Telnyx identifiers are matched only inside the verified webhook tenant scope;
they are not user authority.

For a new goal-directed outbound call, Voxos persists the bounded caller-safe
objective and trusted initiating run reference before provider submission. It
then explicitly dispatches the named LiveKit agent to the reserved room. The
worker loads the objective inside the exact tenant, agent, carrier, and runtime
session scope, renders the task prompt, starts the selected realtime model with
input disabled, and records `agent_ready_at`. Only after that readiness signal
does the API ask Twilio or Telnyx to dial.

The outbound carrier bridge uses a stable participant identity derived from
the server-created call-session ID. Its token intentionally omits
`agent_name`, because the agent was already dispatched; including it would
create a duplicate job. Inbound calls retain the dispatch-bearing participant
token path.

Outbound Twilio callbacks carry a server-created call-session correlation ID.
After Twilio signature verification, Voxos binds the provider Call SID to that
original reservation so an early callback cannot replace the selected agent,
runtime session, room, or worker demand.

Telnyx uses the same server-created reservation through base64 `client_state`
and a stable command ID for that reservation. Verified non-init events reuse
the exact tenant, integration, and resource-scoped call session instead of
creating or refreshing another worker demand. Provider completion releases the
live demand in the same tenant transaction; failed or rejected calls cancel it
with a provider-specific failure code.

The outbound opening is generated exactly once only after both verified carrier
answer and LiveKit media readiness. Twilio normally supplies answer first and
media second. Telnyx can establish its stream before originate, so
`streaming.started` records media readiness without setting `answered_at`;
`call.answered` or `call.bridged` supplies the answer signal. Input is enabled
as soon as the objective-led opening is scheduled, preserving normal barge-in.
An interruption does not remove the objective from the system prompt. The
agent introduces itself only in its first spoken turn, then continues without
repeating its name unless the callee asks or appears confused.

Pre-dial agent readiness, answer/media wait, opening generation/playout, and
carrier ringing are bounded. Current carrier adapters request a 30-second ring
timeout. Terminal provider states release the demand and close the prewarmed
job. Prewarming can increase realtime-provider session time while the phone
rings; measure that cost against the first-speech improvement.

Concurrent calls with identical LiveKit settings share the active project
worker. Different projects and registration failures are isolated in separate
child processes, and idle project workers drain automatically.

Phone session policy comes only from the activated, RLS-protected demand row.
For `telnyx` and `twilio` sources, ordinary speech inactivity does not shut down
the LiveKit agent: the call remains active until the verified provider media
participant disconnects. Browser, job, and room metadata cannot opt a session
into this behavior. The worker does not issue carrier hangup commands because
provider credentials and call-control authority remain in the tenant-scoped
communications layer.

Telnyx voice uses bidirectional RTP media with mono PCMU at 8 kHz. The Voxos
bridge publishes the caller into LiveKit and sends agent audio back to Telnyx
in explicit 20 millisecond frames (160 encoded bytes). A successful downlink
logs `Started Telnyx PCMU media downlink` once without audio content or tenant
credentials. The first valid caller frame likewise logs `Started Telnyx PCMU
media uplink` once with only call/runtime/stream correlation, codec, sample
rate, and encoded byte count. Telnyx websocket `error` frames are sanitized to
their numeric code and bounded title, persisted as `media_stream_error`, and
fail only the owning call with `media_stream_protocol_error`. Unexpected
LiveKit-to-Telnyx send or conversion failures use `media_downlink_failed`.

Both Telnyx and Twilio carrier bridges classify their local caller-audio
publication as LiveKit `SOURCE_MICROPHONE`. This classification is required by
LiveKit Agents RoomIO; it is separate from decoding a provider payload and
capturing a PCM frame. For a healthy Telnyx call, safe logs should show demand
activation, one first-uplink transition, one first-downlink transition, a
participant disconnect after provider stop/hangup, and one demand completion.

Outbound submission is not yet durably idempotent across a fresh API or agent
tool retry. If a provider response is lost after submission, do not
automatically place the call again; a new request creates a new reservation and
can produce a duplicate call.

Before a Telnyx outbound call reserves a voice worker, Voxos performs the
LiveKit preflight and a read-only provider-routing check. The number must still
be assigned to its active Voice API application and enabled outbound voice
profile, and the profile must have configured destination permissions. Provider
dial failure is stored as a safe call-session failure code and cancels the
reserved demand.

The tenant-scoped latency endpoint reports sample counts plus p50/p95
milliseconds for worker-registration-to-agent-ready,
agent-ready-to-provider-dial, provider-dial-to-answer,
provider-dial-to-media-ready, answer-to-first-speech, and
opening-scheduled-to-playout-complete. It never returns objectives,
destinations, transcripts, provider payloads, or signed routes. Establish a
real-carrier baseline before selecting an SLO.

## Contacts And Caller Access

Contacts are tenant-owned people with a display name, one E.164 phone number,
optional email and notes, and an `is_admin` caller-access flag. Phone numbers
are unique per user.

After a provider webhook has established the tenant, Voxos matches the remote
party by exact E.164 number: the `from` number for inbound calls and the `to`
number for outbound calls. The call session stores the matched contact ID,
display name, and admin decision once. Editing the contact later does not change
an active call. Unknown, withheld, malformed, or wrong-tenant numbers remain
limited.

An admin contact receives the selected agent's normal approved capabilities for
the voice channel. This does not bypass agent grants, MCP trust, server
allowlists, or provider authorization. A limited contact receives at most
`web_search_exa` from the tenant's verified Exa preset server: its persisted
server ID must use a canonical URL from the current `exa` preset, retain
`preset_id=exa` metadata, be enabled, trusted, and connected, explicitly allow
the tool, and already appear in the selected agent's base policy. A custom MCP
server or gateway with a same-named tool is not accepted. Voxos does not
install, trust, connect, allow, or grant search automatically.

Caller ID is not strong authentication. It can be spoofed, forwarded, or
reassigned, so only use the admin flag for trusted callers and avoid granting
high-impact tools without a future step-up check.

Approved text and durable agents use `search_contacts` before asking whether a
named person is saved. The result reports `not_found`, `unique`, or
`ambiguous`: one match can proceed directly, multiple matches require a choice,
and no match requires a number or contact-creation clarification. The agent
then passes the tenant-owned contact ID and complete caller-safe objective to
the phone integration's place-call tool. Voxos resolves the saved number inside
the run's tenant before calling Twilio or Telnyx. Authenticated provider API
actions still accept an E.164 destination directly and require an objective.

## Tool Access

Phone tools are integration tools. They require connected integrations,
per-agent grants, and tool authorization. Agent tool calls must not supply user
ids, credentials, URLs, headers, or other authority-bearing fields. Agent
place-call tools accept saved contact IDs instead of model-supplied phone
numbers and require a bounded objective. Connecting a call-capable phone
resource grants the default agent the low-risk `search_contacts` prerequisite
only when no contact-search decision exists. Existing allowed or denied
decisions are preserved. If contact search is denied, the dependent place-call
tool is omitted from that agent's advertised toolset.

## Four-Direction Smoke Test

Use only an approved destination and record timestamps plus safe provider IDs;
never paste message content, full numbers, route keys, or credentials into test
notes.

| Direction | Expected evidence |
| --- | --- |
| Inbound SMS | Telnyx webhook succeeds; one message, per-peer conversation, reply record, and queued run appear; the worker produces exactly one provider-accepted reply or an explicit failure/ambiguous state. |
| Outbound SMS | The API returns a Telnyx message ID; activity advances through accepted/sent to delivered, failed, or delivery unconfirmed. |
| Inbound call | One call session and worker demand appear; ringing/answer/media/hangup events correlate and demand is released. |
| Outbound call | A saved tenant contact and objective produce one reservation; agent/model readiness precedes provider dial; no opening occurs until answer plus media; exactly one objective-led opening and safe latency timestamps appear; provider failure also releases demand. |

The code paths and automated provider doubles are verified in the repository.
Do not treat that as proof of carrier delivery, registration, audio quality, or
destination permission until this matrix has been run with real approved
endpoints.

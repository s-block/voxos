# Frontend Guide

Last updated: 2026-10-05

The React/Vite console is a local test and operations UI for the Voxos API. It
uses Firebase client authentication, obtains an ID token from the signed-in
browser user, and sends that token to authenticated API routes.

The native Flutter app provides the signed-in chat and voice experience on iOS
and Android.

The web frontend loads Firebase configuration from its selected API's
`/api/client-config` response. Configure `FIREBASE_WEB_APP_ID` on that API.
Production images use the browser's current origin by default; local development
can set `VITE_API_BASE_URL`. Server selection retains independent Firebase
sessions for different configured projects. The optional public Logo.dev key
is supplied through `/runtime-config.js`.

## Navigation

The signed-in app exposes these pages:

| Page | Route | What it is for |
| --- | --- | --- |
| Chat | `/chat` | Text chats, streaming replies, per-chat provider/model overrides, tool activity, and exact `?chat=<id>` links from background work. |
| Voice | `/voice` | LiveKit voice sessions, room details, transcripts, and events. |
| Agent | `/agent` | Default agent identity, voice defaults, and text defaults. |
| Knowledge | `/knowledge` | Memories, skills, task plans, and account improvements. |
| Operations | `/operations` | Active runs, schedules, agent webhook setup, run history, and live events. |
| Providers | `/providers` | Central text and voice provider credentials. |
| Phone | `/integrations` | Twilio and Telnyx setup, phone numbers, Telnyx readiness/repair, message/call activity, and WhatsApp linked-device setup. |
| Contacts | `/contacts` | People, E.164 phone numbers, and caller-access flags. |
| Connection | `/connection` | LiveKit credentials and frontend connection mode. |
| Tools | `/tools` | Preferred provider routes for the default agent plus internal built-in tool grants. |
| MCP | `/mcp` | Remote MCP server catalog, setup, auth, test, and grants. |
| Browser Accounts | `/browser-accounts` | Built-in local or Browserless encrypted website credentials and Browser Use managed profiles, editable labels and exact website/login origins, Browser Use country, agent assignments, credential replacement, reauthentication, revoke, and delete. |
| Account | `/account` | Current Firebase user, foreground location opt-in, server selection, and sign-out. |

Exact Chat links load their requested active chat directly, including chats
older than the recent-chat sidebar window. A deleted or unavailable link falls
back to the most recent chat with a visible explanation.

Chat labels saved, incomplete assistant responses as **Partial reply**. The
response remains visible after refreshing or reopening the chat, while run
activity shows the failure.

## Signed-In UI Verification

The current page labels and workflows were verified in a local signed-in browser
session on 2026-07-05. The route sweep confirmed the sidebar labels, account
email display, and these route-specific sections:

- Chat: saved chats list, text provider and model overrides, one composer for
  messages and image requests, generated assistant images, run-aware stop
  control, and activity stream. Provider keys are saved on Providers.
- Voice: provider readiness, LiveKit toggle, room, participant, agent name,
  microphone setting, transcript panel, text panel, and event log.
- Agent: identity form; voice provider/model/pricing/voice/language/instructions;
  text provider/model/pricing/language/instructions plus the allowed and
  default subagent model controls; and an independent image-generation provider
  and model default. Image model details distinguish model capability from the
  hosted API's generation or editing support, show current pricing guidance,
  and link to provider documentation. Text model
  choices use the same API catalog as Chat, and their detail panels list image
  input, tool-calling, and browser-use capabilities; choosing a default subagent
  model also enables it in the allowlist.
- Knowledge: memories, skills, task plans, and improvements with owner review and undo.
- Operations: active, scheduled, webhooks, history, and events tabs. Schedule creation can
  select a same-agent chat or create a new task chat, and has an optional ready
  stored-credential local or Browserless account selector filtered to the
  selected agent's assignments. Linked schedules and run details open their
  exact chat. Webhook setup binds an agent and fixed instruction, shows a new or
  rotated bearer token once with a ready-to-copy `curl`, and supports enable,
  disable, token rotation, and deletion. A relative route is shown safely when
  the API has no configured public base URL.
- Providers: credential cards for voice and hosted text providers, plus an
  OpenAI-compatible text card with a policy-allowed base URL, explicit model
  IDs, and optional bearer API key. Compatible OpenAI, xAI, and NVIDIA text
  credentials are also used for image generation; OpenAI and xAI credentials
  remain shared with their voice runtimes.
- Phone: Twilio credential form, Telnyx phone setup form, connected resources,
  and the WhatsApp linked-device panel.
- Connection: LiveKit credential form and token endpoint/sandbox/manual modes.
- Tools: action-level preferred-provider selectors plus built-in read-only and
  mutating internal tool grant toggles.
- MCP: catalog categories, custom server form, server list, auth/test/grant flow.
- Browser Accounts: recommended built-in local provider or optional Browserless
  and Browser Use connection selection, public HTTPS and reviewed SSO origins,
  Browser Use two-letter country, assignments that default off, local or
  Browserless username/password submission and
  replacement, Browser Use manual non-recorded sign-in, safe status metadata,
  reauthentication, revoke, and delete. Connections missing
  `browserless_agent` or `run_session` are excluded from the selector with a
  direct recovery link to MCP settings, before website credentials can be
  submitted.
- Account: signed-in Firebase email, Firebase user id, foreground location
  opt-in, active server, and sign-out button.

The Contacts route and its create/edit/delete states were verified from source
and automated frontend tests on 2026-07-13. The signed-in browser route sweep
has not yet been rerun for that page.

On 2026-08-25, the Operations webhook setup route and one-time credential panel
were verified from source, a focused component test, frontend lint, and a
production build. A local Firebase API sign-in check succeeded. The available
browser sessions reached the sign-in gate, so the signed-in visual route sweep
was not rerun for this tab.

On 2026-08-08, a signed-in local Chrome check confirmed the Browser Accounts
route, its provider-neutral setup copy, and the optional **Browser Account**
selector on Operations > Scheduled. The local account had no configured browser
provider, so Browserless-specific username/password and credential-replacement
states were verified through component tests rather than a real provider login.

On 2026-08-09, a signed-in local Chrome check confirmed that Chat no longer
renders a per-message Browser Account selector and that Operations > Scheduled
still renders its optional **Browser Account** binding. Focused frontend tests
cover direct message sending without an approval request and the retained
schedule selector.

The Telnyx readiness/activity card, full phone identifiers, explicit Repair
confirmation, and recent message/call states were verified from source and
focused frontend tests on 2026-07-15. Message rows now distinguish reply
generation, dispatch, provider acceptance, delivery, failure, suppression, and
ambiguous submission, and show the safe estimated segment count. A signed-in
browser and real carrier smoke test have not yet been run for those states.

The WhatsApp panel was verified from source and focused frontend tests on
2026-07-21. It lists eligible Twilio/Telnyx numbers in full, offers Telnyx users
**Verify with a call**, offers **Verify with SMS**, and keeps a direct **It is
already registered** path. During registration it polls the no-store backend
state, shows a captured numeric code and its channel, provides copy,
wrong-code dismissal, and cancellation actions, and explains that the user
must enter the code in WhatsApp Business. It then supports QR or pairing-code
linking, hides temporary link codes while settings are read-only, regenerates
expired challenges, and polls until authoritative backend validation changes
the state to connected. It also surfaces reconnecting, capacity, relink,
failure, and disconnect-cleanup states. A signed-in browser plus real WhatsApp
Business handset link has not yet been run.

The current OpenAI, Google, and xAI model catalogs, model pricing summaries,
recommended defaults, and text subagent allowlist/default controls were
verified from source and focused automated tests on 2026-07-27. OpenAI text
choices include the GPT-5.6 tiers, GPT-5.5, GPT-5.4 mini, and GPT-5.4 nano;
GPT-5.4 mini is the primary text default. A new signed-in browser route sweep
has not yet been run for these controls.

The image-generation defaults and their separate provider catalogs were
verified from source and focused API and frontend tests on 2026-08-17. The
catalogs contain GPT Image 2 for OpenAI, the quality and economical Grok Imagine
image tiers for xAI, and FLUX.2 Klein 4B, FLUX.1 Schnell, and FLUX.1 Dev through
NVIDIA NIM. The saved image provider can differ from the active text provider.
Without an explicit choice, an enabled NVIDIA credential selects hosted FLUX.2
Klein 4B automatically; hosted edits route to an enabled OpenAI or xAI editor
because NVIDIA's hosted preview does not accept arbitrary private references.

The Chat **Stop run** control was verified from source and focused frontend
tests on 2026-07-27. It reads the server-issued run ID from the active stream,
requests durable cancellation, and aborts the stream so an in-flight provider
or every tool request in an active parallel batch is interrupted. The server
also treats a disconnected inline stream as cancellation, and the activity
model distinguishes `run_cancelled` from `run_failed`. A signed-in browser
smoke test has not yet been rerun for this control.

Dedicated subagent chats and their parent-chat delivery were verified from
source and focused frontend tests on 2026-08-02. Their delegated prompts and
responses are labelled **Delegated task** and **Subagent** from persisted
transcript sources instead of being presented as user-authored messages. Parent
chat loading includes recent child status, output, runtime events, and tool
events. While a child is active, the UI polls only delegated-run summaries; it
reloads the full chat once after all children finish. New child results are
durable parent transcript messages with a correlation key that suppresses the
legacy output projection, while tool events remain in Activity. A signed-in
browser reached the local Chat shell on 2026-08-02, but
the disposable account had no text-provider key, so it did not run a real
delegated provider call.

The web and Flutter chat image composers were verified from source and focused
automated tests on 2026-09-19. Both clients keep text and up to four ordered
images in one draft, support repeated image selection, removal and reordering,
upload the frozen draft with bounded concurrency, and start one stream for the
complete turn. Web also accepts validated images pasted into the composer or
dropped anywhere in the chat conversation and gives a visible drop-target cue.
Its picker was confirmed in a signed-in local Chrome session. Mobile users can
take a photo, choose existing photos, or paste a copied image from the add menu;
the composer long-press menu also exposes image paste. Historical thumbnails
use authenticated byte/blob requests. Web object URLs are revoked when
replaced or unmounted; Flutter keeps a bounded thumbnail cache and opens full
images explicitly.
Both clients use the same composer and streamed message request for text,
prompt-based generation, and edits. They render a generated attachment from the
safe `generate_image` completion event while the assistant turn is active, then
replace it with durable chat history. Current uploaded references take
precedence; conversational follow-ups can edit the latest generated image
without a client-selected mode or a second image-specific request. Flutter
renders those assistant thumbnails and offers a full-screen **Save image**
action backed by iOS Photos and the Android gallery.
Real-device HEIC/photo-picker and gallery-save behavior still require device
smoke tests.
`CHAT_IMAGE_INPUT_ENABLED` is the shared rollout switch: when disabled, both
clients hide or reject image selection while ordinary text chat remains
available. While the rollout is enabled, the attachment action stays visible;
providers with an image route keep it enabled even when the ordinary text model
is text-only. Web names the routed multimodal model in the attachment tooltip
when image analysis uses a different model.

## Foreground Location

Web and Flutter location sharing is an explicit per-device, per-account opt-in.
The first enable action triggers the browser or operating-system permission
prompt. Web deployment permits geolocation only for the same origin; browser
geolocation still requires a secure context such as HTTPS, except for supported
local development origins. The iOS app declares when-in-use access, and Android
declares coarse and fine foreground access without background permission.

Both clients keep only the preference in local storage. A successful coordinate
snapshot is cached in memory for at most five minutes, refreshed before a text
send, and captured once at voice startup. Location errors leave chat and voice
usable and are surfaced in the client without sending a coordinate. Disable the
setting to clear the in-memory snapshot and stop future requests.

## Place Recommendation Cards

Web and Flutter render validated Google Maps place recommendations below the
assistant answer in a separate **Places** group. A card shows the exact returned
place title and **Google Maps** attribution. It opens the provider URL in a new
web tab on React; Flutter launches the same HTTPS URL externally so iOS or
Android can route it to the installed Google Maps app. Duplicate card URLs are
not repeated in the generic **Sources** group.

Place cards arrive only with the completed message stream. Reconciliation keeps
cards attached to their exact durable assistant messages in memory across
subsequent sends, but later history loads do not restore Google Maps content.
Both clients reject credential-bearing URLs, non-HTTPS URLs, non-Maps Google
destinations, and lookalike domains before rendering a card.

## Common Flow

1. Start the API and frontend.
2. Sign in with a Firebase account accepted by the configured Firebase project.
3. Open Providers and save a key for each text or voice provider you plan to
   use.
4. Open Agent to choose independent default text and voice providers and
   models. Chat provider and model selectors affect only that conversation.
5. Open Connection and save LiveKit credentials if you want voice sessions.
6. Open Tools, choose automatic or pinned providers for equivalent actions, and
   approve the internal tools the agent should be able to use. Automatic web
   search currently shows Exa when it is authorized and available.
7. Open Contacts to add the people that incoming and outgoing calls should
   match.
8. Open MCP or Phone to connect external tools and communication channels. For
   accounts without an existing Exa endpoint, MCP shows the connected keyless
   Exa default and lets the user disable it through the normal server controls.
   Platform-default metadata is descriptive; backend server state and agent
   grants remain the authority for tool access. For
   WhatsApp, select the exact connected Twilio/Telnyx number. The Phone page can
   securely catch its mobile-registration SMS or, for Telnyx, its verification
   call before continuing to the QR or pairing-code linked-device flow.
9. To use Browser Accounts, select the built-in local provider and save the
   website credentials. No MCP setup is required. Browserless and Browser Use
   are optional hosted alternatives that must first be configured and tested
   under MCP. A local or Browserless account becomes ready after Voxos encrypts
   its website credentials; a Browser Use account requires manual sign-in and a
   two-letter browser country that defaults to `GB`. Changing that country
   requires manual sign-in again. Assign the account to the intended agent,
   then ask for the signed-in browser task directly in Chat or authenticated
   in-app voice. Chat no longer has a per-message Browser Account selector. In
   Operations, a ready assigned local or Browserless account can be attached
   explicitly to a schedule.
10. To let the agent answer requests such as “weather near me” or “restaurants
    near me,” open Account on web or Settings in Flutter and enable **Location
    for your agent**. The setting applies only to foreground text and in-app
    voice actions from that device.

Provider keys and external tokens are submitted once to the backend and stored
through the encrypted user secret vault. The frontend does not receive plaintext
secrets back from the API.

Browser Account setup URLs, credential replacement, and authorization responses
use no-store API responses. The setup URL moves directly into a
separate browser window and is not rendered or retained in React state. Account
cards expose neither provider profile/session IDs nor stored website
credentials; they show only whether credentials are present.

## Reviewing Agent Improvements

Open `/knowledge/improvements` to inspect proposed and applied account knowledge.
The screen shows policy reasons, assessment results, proposed content,
before/after content, and cited evidence. It supports approve, reject, undo,
refresh, and loading older records. A stale review or later knowledge edit
produces a conflict message while preserving the displayed proposal. Read-only
server connections can inspect improvements and evidence; writes require an
editable connection. The automatic-learning checkbox saves the account's standing
choice and is off until explicitly enabled.

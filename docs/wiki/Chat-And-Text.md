# Chat And Text

Last updated: 2026-10-05

Text chat gives each user durable chat sessions, streaming replies, ordered
image attachments, tool events, usage records, transcripts, run history, and
multiple text provider APIs. A text chat can also be the durable destination
for scheduled work, Codex Cloud tasks, and child-run results.

Accepted user messages remain in chat history when provider setup fails. If a
response fails after text has appeared, the filtered text remains saved as a
partial reply. Run activity shows the failure, and the next message can retry
the request.

## Supported Text Providers

| Provider | Default model | Notes |
| --- | --- | --- |
| OpenAI | `gpt-5.4-mini` | Cost-efficient primary default; GPT-5.6 tiers plus GPT-5.5 and GPT-5.4 nano remain selectable. |
| OpenAI compatible | First configured model ID | User-entered local or remote endpoint, manual model list, optional bearer key, streaming Chat Completions, and structured tool calls. Custom models are text-only unless future capability metadata says otherwise. |
| xAI | `grok-4.5` | Flagship default; `grok-4.3` and `grok-build-0.1` are selectable lower-cost agentic tiers. |
| NVIDIA API Catalog | `openai/gpt-oss-20b` | Fast tool-capable default for ordinary text. Image turns and browser execution route to Step 3.7 Flash. Current capabilities and terms are shown in the model selectors. Availability and limits apply. |

Text credentials are stored through the encrypted user secret vault. Providers
is the central place to save text and voice keys. Agent settings select text
and voice providers independently, so a text conversation can use a different
provider and model from the voice agent. A shared OpenAI or xAI key is available
to text, voice, and image capabilities supported by that provider. One NVIDIA
API Catalog key supports both text chat and NVIDIA-hosted image generation.

Provider settings return typed image-input, tool-calling, and browser-use
capabilities alongside model descriptions, recommendations, and current pricing
summaries. This catalog is the single availability source
for the Agent primary model, subagent policy, and Chat model selectors. A saved
subagent allowlist remains an explicit user policy, but the default subagent
selector displays the full current catalog and adds a newly selected default to
that allowlist. The OpenAI catalog is
limited to current general-purpose models that support streamed Responses API
requests and function calling. It excludes deprecated models, legacy snapshots,
specialized realtime/audio/image models, and superseded mini/nano families.
xAI text chat uses its Responses endpoint, including Responses-style tool
continuations and multimodal content. NVIDIA API Catalog text chat uses the
OpenAI-compatible Chat Completions endpoint, including Chat Completions-style
tool-call and tool-result messages. NVIDIA's catalog endpoint is fixed by the
provider integration rather than being user-configurable. Changing the
application default does not replace an authenticated user's explicitly saved
model choice.

The OpenAI-compatible provider uses the user-entered base URL and explicit
model list. Voxos does not trust `/v1/models` as a capability catalog and does
not infer image or browser support from arbitrary names. Unregistered endpoints
must use public HTTPS on port 443 and global DNS; exact deployment-owned local
or same-network container endpoints can be registered separately. Provider
requests never follow redirects. See
[Self-Hosted Models](Self-Hosted-Models.md).

Agent settings store image-generation provider and model defaults separately
from the active text provider. The provider response exposes a dedicated image
output catalog for OpenAI, xAI, and NVIDIA NIM, including descriptions, pricing
or deployment guidance, model editing capability, hosted-endpoint editing
capability, and provider documentation links. Saving this default validates
both the model catalog entry and the user's compatible stored provider
credential. Without an explicit saved image default, Voxos automatically uses
NVIDIA FLUX.2 Klein 4B when an NVIDIA key is enabled, then chooses an available
image model from the active or other enabled text providers. Image routing does
not change the provider or model used by existing or new text chats.

GPT-5.6 and GPT-5.5 use their default reasoning modes, so Voxos omits a saved
temperature for those requests. For GPT-5.4 mini and nano requests with
function tools, Voxos requests low reasoning effort and omits temperature.
Nemotron 3 interactive chat uses instruct mode even when tools are visible;
manual, scheduled, and subagent runs use thinking mode. If a provider returns
reasoning without either a final answer or a structured tool call, Voxos makes
one recovery attempt in fast/instruct mode. Nemotron-specific request options
are not sent to other model families available through NVIDIA API Catalog.

OpenAI requests use `store: false`, so Voxos remains the durable source of truth
for transcripts, tool events, and usage. During a multi-round tool turn, Voxos
replays the completed Responses output items and function outputs in order.
Before execution, model-produced arguments are checked against the selected
tool's complete JSON Schema as well as the authority-field policy. A schema
mismatch is recorded as a failed tool call without contacting the tool, then
returned to the model with a safe correction instruction. The model may issue
a corrected call within the execution budget. Each turn allows four execution
rounds and four separate catalog-discovery rounds for `tool_search` and
`tool_describe`. Discovery does not consume execution rounds; a response that
mixes discovery and execution consumes one round from each budget. After one
invalid call, sibling calls to the same tool in that provider response are
skipped, and an exact rejected request is not executed again in a later round.
Each skipped provider call still receives a function output so continuation
state stays complete.

Unexpected tool execution errors follow the same continuation contract. The
failed call is recorded for diagnostics and returned to the model as an
explicit failure result. The model must still produce a final response, must
not claim the action succeeded, and should explain what could not be completed
or verified. These recoverable failures do not end the whole chat run.

When the user explicitly asks for a reminder, future check, monitor, or
follow-up, the agent can create a one-shot or fixed-interval schedule that
posts future responses into the current chat. The schedule derives its user,
agent, run, and chat scope from the authenticated turn. A task plan is separate:
it records steps but does not schedule future execution.

When a provider response contains several independent calls, Voxos runs tools
declared safe for parallel reads in bounded waves of eight. Results, persisted
tool events, and function outputs are still committed in the model's original
call order. Internal read-only tools are eligible, as are curated MCP tools
marked read-only and custom MCP tools that publish the protocol's read-only
annotation. Writes, phone/SMS actions, unknown MCP tools, and other
side-effecting tools remain serial barriers, so reads are never reordered
across them.

Encrypted reasoning content is requested and carried only through that
in-flight turn so stateless reasoning and function calling remain coherent.
An explicit incomplete Responses stream is recorded as a failed run instead of
being persisted as a completed assistant answer.

## Current Location

The signed-in web and Flutter clients can attach a user-approved WGS84 location
snapshot to the current streamed message. The typed payload contains latitude,
longitude, horizontal accuracy in metres, and a timezone-aware capture time.
The server validates coordinate ranges and ignores snapshots older than 15
minutes or implausibly far in the future.

A fresh snapshot is rendered into only that turn's live instructions. It is not
added to the chat prompt snapshot or transcript. If the turn delegates work, a
still-fresh snapshot can travel as child-run input and is checked again when the
child starts. Scheduled, webhook, communication-reply, and other runs without a
foreground client do not receive an implicit device location.

## Place Recommendation Cards

When the Google Maps Grounding Lite `search_places` tool supports an assistant
recommendation, the completion stream includes typed place cards alongside the
answer. Each card uses the provider-returned place title, Place ID, attribution,
and exact Google Maps URL. The web client opens that HTTPS URL in Google Maps on
the web; the Flutter client hands the same URL to the operating system, which
can open the Google Maps app when it is installed.

Cards are deduplicated by provider and Place ID, restricted to credential-free
Google Maps HTTPS destinations, and labelled **Google Maps**. The assistant
receives the exact source URLs in its tool continuation so it can cite the
matching place without constructing a URL. A completion includes at most six
cards referenced by the answer; if the answer does not name a result exactly,
the bounded most-recent search result is used. Ordinary Markdown links remain
available for providers that do not expose a typed, attributable place result.

Google Maps place content is transient. It is sent in the live
`message_completed` event and retained only in the current web or Flutter
client session; transcript, tool-event, and run-event storage keeps a redacted
completion or failure marker instead. The live event includes the durable
assistant message ID so clients can preserve and correlate cards during normal
post-send reconciliation. Reloading chat history preserves the assistant text
but does not reconstruct a Google place card from stored content.

## Browser Use

Chat exposes one high-risk `use_browser` capability for general browser work.
The model supplies a complete instruction and either a public HTTPS start URL
or an opaque handle for one of the ready saved browser profiles assigned to the
chat's exact agent. Public tasks do not require a profile. Signed-in tasks use
the profile's safe label and origin catalog, so a request such as “Sign in to
Eon and get my bills” can select the matching login automatically.

The tool sends the agent's browser instruction unchanged to the selected
browser runtime. Skills can provide reusable browser runbooks or site guidance
without adding provider code. The runtime is not specialised for login,
dashboards, billing, or retrieval; the same contract supports public research,
authenticated reading, downloads, and explicitly requested browser actions.

Voxos resolves profile IDs, provider sessions, origins, secrets, and the user's
currently selected compatible text model outside model-controlled arguments.
Saved profile handles are opaque and are rechecked against the authenticated
user, exact agent assignment, and any hosted provider-tool grant at execution.
The model never receives stored usernames, passwords, provider tokens, or model
keys. Profile tasks cannot broaden their configured navigation origins, and
browser authority belongs only to the root run rather than subagents.

Browser tasks default to `read_only`. The model may request
`state_changes_allowed` only when the user explicitly asked for a state-changing
browser action. Treat that mode as high risk because prompt policy and origin
controls do not provide an external transaction approval interceptor. See
[Browser Accounts](Browser-Accounts.md) for secret, provider, and navigation
boundaries.

## Image Input

All current OpenAI and xAI text catalog models accept image input and return
text. NVIDIA image input is available with Step 3.7 Flash, Nemotron 3 Nano
Omni, and MiniMax M3; the other curated NVIDIA models remain text-only. When a
known text-only catalog model is selected, an image-bearing turn uses the
provider's configured multimodal route while the chat keeps its ordinary text
default. NVIDIA routes these turns from GPT-OSS 20B to Step 3.7 Flash. Unknown
models do not inherit this fallback. SMS/MMS media, WhatsApp media, and voice
media are not part of this chat feature.

The signed-in web and Flutter composers support one draft containing optional
text and up to four ordered images. Users can add images over repeated picker
actions, remove or reorder them, keep editing the text, and press Send once.
The web composer also accepts JPEG, PNG, and HEIC files pasted into the message
field or dropped anywhere in the chat conversation, and shows a drop target
while files are dragged over it. The Flutter add menu can take a photo, choose
existing photos, or paste a clipboard image; image paste is also available from
the composer's long-press menu.
The attachment action remains in both composers whenever the deployment rollout
is enabled and either the text route can analyze images or a configured image
editor can use them as private references. The web composer names the routed
multimodal model when it differs from the selected text model. The clients stage
every selected image first, then
issue exactly one streaming request containing the complete text and ordered
attachment IDs. An upload failure does not open the provider stream, and the
draft remains available for retry. Image-only turns are valid.

JPEG, PNG, and HEIC sources are decoded as untrusted input. Voxos applies
orientation, removes metadata, rejects animation and unsafe dimensions, and
normalizes each accepted image to JPEG or PNG. The defaults allow four images,
5 MiB per normalized image, and 20 MiB per turn. Image analysis consumes
additional provider input tokens.

Normalized full images and thumbnails live in private object storage. Local
development uses a shared persistent filesystem. Deployed installations can use
the supported private S3 backend when container-local files are not durable or
shared between processes. Postgres stores tenant-owned metadata and the ordered
transcript relationship under forced RLS. Binary content is retrieved through
authenticated chat routes and is never exposed through public bucket URLs.

## Image Generation And Editing

The signed-in web and Flutter clients use one composer and one streamed chat
path for ordinary replies, image creation, and image edits. Ask naturally for
an image in the conversation. The text model can invoke the authorized
`generate_image` tool with a self-contained prompt built from relevant chat
history and an explicit `generate` or `edit` action. The tool is available to
configured root chat runs; it is a serial, cost-bearing internal action and is
not delegated to subagents or exposed to voice and messaging channels.

For an edit, current-turn uploaded images take precedence. Without current
references, Voxos uses the most recent generated assistant image from the same
tenant-owned chat. The previous image bytes are sent to an editing-capable
provider, so a follow-up such as “put them on a beach” preserves the visual
subject as well as the conversational description. A text-only orchestration
model receives only a neutral marker that private references are available to
the tool; it does not receive image bytes or claim visual understanding. If an
edit has no usable source, includes more than three current references, or has
no eligible editor, the tool reports a recoverable error instead of silently
creating an unrelated new image.

The generated result is normalized through the attachment pipeline, stored as
a private tool artifact, and bound to the same final assistant response. A safe
attachment summary arrives in the normal `tool_call_completed` stream event so
web and Flutter can show it immediately; durable chat reconciliation remains
authoritative. **Stop run** cancels the same active provider/tool stream. If
image generation succeeds but the final text continuation fails, Voxos keeps
the paid artifact with a deterministic recovery caption instead of discarding
it.

Voxos honors an explicitly saved image provider while its credential is active.
Without an enabled explicit choice, automatic generation prefers
NVIDIA-hosted FLUX.2 Klein 4B whenever the user has an active NVIDIA credential,
followed by an available image model from another enabled text provider. OpenAI
GPT Image 2 and xAI Grok Imagine provide hosted generation and private-image
editing. FLUX.2 Klein supports multi-reference
editing when deployed as a self-hosted NVIDIA NIM, but NVIDIA's hosted API
Catalog preview does not accept arbitrary private reference images. A hosted
edit therefore routes to an enabled OpenAI editor first, then xAI, without
changing the saved generation default.

The Flutter transcript renders generated assistant images. Opening one shows
the full authenticated attachment and a **Save image** action. iOS requests
photo-library add access, while Android uses the system gallery permission
contract needed by the device version.

## Subagent Model Policy

Text settings store a user-owned allowed model list and one default subagent
model in the existing provider configuration JSON. OpenAI defaults to all six
curated models with `gpt-5.6-terra` as the balanced delegation default. xAI
defaults to its current three model choices with `grok-4.3` as the delegation
default. NVIDIA defaults to `openai/gpt-oss-20b` for delegated text work, while
the same curated NVIDIA catalog remains available for explicitly allowed
subagent models.

The `delegate_agent_task` internal tool is denied by default and must be granted
on Tools. Once granted, a parent text agent can omit the model to use the
configured default or explicitly request one model from the allowlist. The
backend derives the parent user, agent, run, provider, and model from trusted
run context, validates the requested model, intersects the child tool policy
with the parent and target agent policies, and queues a durable child run. The
worker creates and binds a dedicated child chat with the selected provider and
model. The child keeps its full prompt, activity, and response in that chat.
After successful completion, its final response is also appended once as an
assistant message to the originating parent chat. Isolated mode starts from
only the delegated prompt; summary mode adds only the explicit parent summary.
Child transcript entries use
`subagent_task` and `subagent_output` sources so the UI does not attribute the
delegated task to the user.

Delegation permission is not inherited by a child run. This prevents a child
without the requested capability from queuing the same task recursively. The
parent run receives `subagent_started` and `subagent_completed` events; the
completion event identifies the durable child run and child chat. Loading the
parent chat also returns its recent delegated runs, child output, runtime
events, and tool events. These child records are loaded in one batched query per
record type rather than once per child. While a child remains active, the
frontend polls a lightweight delegated-run summary endpoint for status and tool
activity. The durable parent transcript is authoritative for new child results;
the delegated-run output projection remains only as a fallback for historical
runs that predate parent-chat delivery.

Voxos does not silently classify tasks or replace a user's active model. The
model choice is explicit in the child payload and parent `subagent_started`
event, keeping cost and quality decisions inspectable.

## Frontend Workflow

1. Open Providers and save the required provider key.
2. Open Agent and choose the text and voice defaults independently.
3. Open Chat and optionally choose a provider and model for that chat.
4. Create or select a chat.
5. Write an ordinary message, ask naturally for a new image, or attach images
   and describe the edit you want. A follow-up can refer to the latest generated
   image in the same conversation without attaching it again.
6. Watch streamed response chunks, tool calls, run events, or the generated
   assistant image in the transcript.
7. Use **Stop run** to cancel the exact durable run behind the active response.
   Voxos aborts the provider or every tool await in the active parallel batch,
   requests cancellation for child runs, records `run_cancelled`, and closes
   the response stream. Closing or otherwise disconnecting the stream also
   cancels an inline run so it cannot remain leased as background work.

## API Workflow

Create a chat:

```bash
curl -X POST "$VOXOS_API/api/chats" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Local test"}'
```

Stream a message:

```bash
curl -N -X POST "$VOXOS_API/api/chats/$CHAT_ID/messages/stream" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"What can you do?"}'
```

For an image turn, upload each image to
`POST /api/chats/$CHAT_ID/attachments` as multipart form data with
`client_draft_id`, `client_source`, and `file`. Then send one JSON stream
request:

```json
{
  "text": "Compare these",
  "attachment_ids": ["attachment-1", "attachment-2"],
  "client_turn_id": "client-generated-idempotency-key"
}
```

Use
`GET /api/chats/$CHAT_ID/attachments/$ATTACHMENT_ID/content?variant=thumbnail`
or `variant=full` with the Firebase bearer token to retrieve content.

The streamed chat route is the user-facing conversational workflow. The direct
endpoint remains available for compatible API clients and exact-prompt
automation that already knows its complete prompt. It does not apply the chat
model's conversational prompt synthesis. Generate an image, or include up to
three already staged attachment IDs to edit private reference images:

```bash
curl -X POST "$VOXOS_API/api/chats/$CHAT_ID/images/generate" \
  -H "Authorization: Bearer $FIREBASE_ID_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt":"A tiny library on the moon",
    "attachment_ids":[],
    "client_turn_id":"client-generated-idempotency-key",
    "client_source":"web"
  }'
```

The response identifies the routed provider and model and returns the durable
user and assistant messages. Reusing the turn ID with the same prompt and
references returns the existing result; different content is an idempotency
conflict.

## Persistence

Text chats are durable. The runtime persists sessions, transcripts, tool events,
usage, run records, run events, and effective tool policy snapshots. The agent
can use approved internal tools, approved MCP tools, and approved integration
tools during a text run.

User-owned background work can link its run to one active text chat owned by the
same user and agent. Meaningful operation updates are stored as ordinary
assistant transcript messages. Each update has a deterministic run-and-stage
correlation key, so retrying a worker cannot insert the same update twice. Each
new text message, including a background update, also refreshes the chat's
activity time and brings it to the top of the chat list. Internal scheduler,
queue, lease, and supervisor polling does not create chats or post heartbeat
messages.

Each image-bearing user transcript owns an ordered attachment set. Reusing the
same client turn key reconciles to that existing turn instead of creating a
second transcript. Provider data URLs exist only during the current invocation;
they are not stored in transcripts, events, runs, or logs. Old image turns are
replayed only while the separate history image-count and normalized-byte
budgets allow them and the model selected for the current turn supports image
input. Ordinary text turns on GPT-OSS remain text-only and do not load stored
image bytes; later image turns routed to Step can replay images within those
budgets. Unknown models replay transcript text without loading image bytes.

The stream includes its durable `run_id` on normalized events. The frontend uses
that server-issued ID for cancellation; it does not infer a run from chat
history or submit a user-supplied owner ID. A cancelled turn is a distinct
terminal state, not a failed assistant answer.

Transcript messages retain their database `created_at` datetime and chat API
responses expose those values as offset-aware ISO 8601 strings. Each provider
request receives current UTC datetime context in the rendered system
instructions.

When recent transcript history is replayed to a model, Voxos adds a bounded
`Conversation Timeline` system message before the replayed user and assistant
messages. The timeline lists each replayed message's position, role, and
`created_at` ISO datetime without changing the stored message text. It also
includes the current UTC datetime, chat start datetime when available, and the
last prior message datetime when there is prior replayed history.

The provider receives a run-scoped tool view after grants and equivalent-tool
routes are resolved. Small authorized catalogs include every tool directly,
with bounded descriptions. Larger external catalogs keep internal tools and
the selected provider for each preferred action direct, while long-tail tools
are exposed through `tool_search`, `tool_describe`, and `tool_call`. Search uses
exact name matching plus a lightweight BM25 index over action, name,
description, and parameter names. Result IDs can reach only tools already
present in that immutable run catalog, and execution still passes through the
target provider's normal schema, authority-field, grant, and audit checks. Tool
definitions are prepared once per run from the last successful MCP discovery;
the runtime does not contact every MCP server just to assemble or redispatch
the catalog.

For direct tools that succeeded in an earlier turn and are still available,
Voxos also supplies recent execution facts from its own records. Historical
result bodies are not promoted into system instructions; the model is directed
to call the tool again when it needs the data.

The default internal tool set includes `get_current_datetime`, which returns the
current server UTC datetime as an ISO 8601 string. Agents should use it when an
exact current time matters.

All text and messaging channels also receive the shared factual-grounding
contract. It requires an available web-search or authoritative retrieval tool
for current, niche, ambiguous, disputed, consequential, recommendation-based,
source-specific, or uncertain factual claims. An explicit request to search,
verify, fact-check, or double-check online always requires a successful search
before the agent can say it searched. Stable elementary facts, calculations,
transformations, creative requests, ordinary conversation, and facts already
provided by the user can be answered without retrieval.

Grounded answers should use retrieved evidence, cross-check consequential or
conflicting claims, and cite source names and links where the channel supports
them. If retrieval fails or is unavailable, the agent should identify that it
is relying on general knowledge and communicate material uncertainty rather
than silently implying current verification. A single factual lookup stays in
the current run instead of being delegated to a child agent.

## Medium-Aware Responses

Text generation uses a trusted, versioned response profile selected from the
session channel rather than from model or tool arguments. Web chat currently
uses `web_chat:v1`: Markdown and structured detail are available when useful,
simple questions stay concise, and explicit requests for detail can use a
larger output budget. SMS uses the stricter `sms:v1` contract documented in
[Phone Integrations](Phone-Integrations.md). The `whatsapp:v1` profile is used
for WhatsApp linked-device replies. It favors mobile-friendly short paragraphs
or compact bullets, normally stays near 100 words, and writes the generated
answer to the durable WhatsApp outbox. The shared WhatsApp worker sends that
reply to the persisted inbound peer.

Each profile supplies both a recent-message limit and a character budget.
Provider context retains complete recent user/assistant turns, even when one
turn exceeds the soft character target, instead of slicing an individual
message. The durable transcript remains intact. Output budgets are separate
from input context: a short SMS answer can still use relevant recent history.
OpenAI and xAI map the profile cap to Responses `max_output_tokens`.

Session prompt snapshots record the profile identifier so persisted runs show
which version governed the response without storing phone numbers in metadata.

## Investigating Conversation Failures

An owner can ask an agent to investigate retained errors across the account's
chats. Granted history tools return source citations, bounded excerpts, and
coverage limits. The agent can submit evidenced lessons for automatic policy
assessment or owner review. Use
[Knowledge > Improvements](Agent-Knowledge-And-Persistence.md) to control
automatic saving and review results. Terminal failed owner turns also queue
bounded analysis in the durable worker.

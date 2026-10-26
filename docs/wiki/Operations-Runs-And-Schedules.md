# Operations Runs And Schedules

Last updated: 2026-10-05

Operations shows durable agent execution. It is split into Active, Scheduled,
Webhooks, History, and Events.

## Runs

Agent runs are persisted units of work. A run records owner user id, agent id,
kind, payload, status, delivery state, effective tool policy, parent/root
lineage for subagents, schedule or webhook provenance, timestamps, and errors. User-owned text work can also
carry a `chat_id`, which is its durable user-visible destination. Run events
provide the detailed execution timeline; chat messages provide concise updates
the user should see in the conversation.

The active runs view highlights running and queued work. The history view shows
completed runs. Selecting a run shows timeline and metadata.
When a run has a linked chat, its detail view includes **Open linked chat**.

Subagent payloads can also contain a provider and model selected by the text
subagent model policy. A parent may request only a model in the user's current
allowlist; omitting it uses the configured balanced default. The durable worker
passes those values into dedicated child-chat creation and binds that chat to
the child run while retaining `parent_session_id` lineage. The parent receives
`subagent_started` with the selected model and `subagent_completed` with the
child run/chat reference. The child's final answer is appended once to the
originating parent chat while the full delegated prompt, transcript, and tools
remain in the dedicated child chat. Parent chat reads also include recent child
status and tool activity for Operations diagnostics and historical fallback.

## Error Reflection

Terminal failures in authenticated owner text turns and owner LiveKit sessions
queue a `reflection` run in the same database transaction. Triggers coalesce to
one analysis per conversation per hour. The durable run worker processes them
using the tenant's saved text model and encrypted credentials. Start it with
`make worker-runs` and keep the scheduler running for normal retry and lease
recovery.

An analysis reads bounded retained history plus the triggering conversation and
can produce up to three memory, skill, or diagnostic proposals. Account policy
determines automatic application or owner review in
[Knowledge > Improvements](Agent-Knowledge-And-Persistence.md). The original
reflection job records completion, failure, or a skipped prerequisite/budget
reason; retries reuse that job. Partial batches report their persisted candidate
count, and cancelled work stops before a further application.

Generation and assessment share a durable account limit of 40 model calls per
24-hour window. Each call has a 45-second deadline, a 4,096-token output cap, and
no tool access. Configure a text provider and credential even when investigating
voice failures. Missing credentials or exhausted budget skip automatic analysis;
an unavailable assessment leaves an on-demand candidate for review.

## Schedules

Schedules enqueue future runs. Supported schedule kinds include one-shot and
interval schedules. A schedule stores objective/payload, status, catch-up
policy, next run time, ownership metadata, and one stable text chat. Schedule
creation can select an existing chat for the same agent or create a new task
chat. Every occurrence reuses that chat, and Operations provides an **Open
chat** action. Existing schedules created before chat links were introduced
claim one chat on their first run and reuse it afterward.

An interactive authenticated text-chat request can also create a one-shot or
fixed-interval schedule directly when the user explicitly asks for a reminder,
future check, monitor, or follow-up. The scheduled instruction runs as the same
agent and posts its response back into the originating chat. User, agent, run,
and chat IDs come from trusted execution context and cannot be selected by the
model. Scheduled runs, webhooks, manual worker runs, and subagents cannot create
more schedules through this conversational tool. This path does not attach a
Browser Account; use Operations when an unattended task needs a saved website
login.

Deleting a linked chat pauses and disables its active schedules in the same
database transaction. Reassign a paused schedule to another valid same-agent
chat before resuming it. Resume and **Run now** reject an unavailable
destination, and the scheduler safely pauses a stale schedule instead of
stopping its processing loop. A deleted, non-text, wrong-agent, or cross-account
chat cannot be selected.

A schedule may also reference one ready stored-credential local or Browserless Browser Account assigned to
the same agent. Selecting an account authorizes that schedule's root runs to use
the encrypted website username and password for the persisted scheduled browser
instruction. Schedule creation and updates validate the authenticated tenant,
provider mode, ready state, and agent assignment. Enqueueing snapshots the
selected account onto the durable run, so editing the schedule cannot switch an
already queued run to another identity. Runtime checks the snapshot again.
Revoking the account or removing its assignment makes an affected run fail
clearly; it cannot silently answer without performing the requested login.

The Browser Account is optional and is selected once when the schedule is
created or edited; scheduled executions do not require runtime approval.
Browser Use managed profiles cannot be attached because the schedule
contract supports stored-credential accounts only. Hosted Browserless
scheduled runs still require the agent's effective tool-policy snapshot to
include the connection's high-risk `browserless_agent` grant; the built-in
local provider requires no MCP connection or generic internal-tool grant. Credentials and
account authority do not transfer to subagents. The bound browser capability is
the only tool offered in the run's required first tool round. After its result
is recorded, the model may interpret that result and use its ordinary tools.

## Agent Webhooks

The **Webhooks** tab creates inbound JSON triggers for durable agent work. Each
definition fixes one agent, a name, and an instruction. Voxos returns the
trigger URL and bearer token only when the definition is created or its token
is rotated; copy the generated `curl` before dismissing it. The stored
credential is a one-way token hash, so it cannot be recovered later. Disable a
trigger to stop deliveries temporarily, rotate the token after exposure, or
delete the definition when it is no longer needed.

Send an `application/json` object to the definition's `/deliver` route with
either `Authorization: Bearer <token>` or `X-Voxos-Webhook-Token: <token>`.
Tokens in query parameters are rejected. Bodies are limited to 16 KiB, and the
rendered task must fit the normal 24,000-character agent-turn limit. Supply an
`Idempotency-Key` or `X-Voxos-Idempotency-Key` of at most 256 bytes when the
source may retry; Voxos namespaces it to the webhook so the same source key can
be used safely on another definition.

An accepted delivery returns `202` with a durable `run_id`; it does not wait for
the model. Each new delivery has `webhook_agent_task` kind, records its
`webhook_id`, snapshots the selected agent's current effective tool policy, and
creates a new isolated task chat when the worker claims it. The saved
instruction is trusted configuration. Event JSON is durable untrusted task
input and cannot select the user, agent, chat, model, credentials, or tool
policy. It can still contain prompt-injection text that influences tools the
selected agent already has, so use a dedicated agent with only the grants the
automation needs. The event becomes part of the durable run and chat transcript;
do not include secrets or other data you do not want retained there.

The generic route authenticates only the Voxos bearer token. Direct GitHub,
Stripe, Svix, and similar callbacks require the provider's exact raw-body
signature verifier. When a source cannot set a bearer header, use a trusted
n8n, Pipedream, Zapier, or small adapter that verifies the provider callback
before forwarding the JSON to Voxos.

Unknown, disabled, and deleted definitions return `404`; an active definition
with a missing or invalid token returns `401`. Unsupported media returns `415`,
invalid or non-object JSON returns `422`, and an oversized request returns
`413`. A worker-launch failure can return `503` after the run is already
durable. Retry with the same idempotency key to receive and wake the same run.

Run scheduler locally:

```bash
make scheduler
```

Run the durable worker locally:

```bash
make worker-runs
```

The default root Docker Compose stack runs the same worker as
`agent-run-worker`. It is required for inbound SMS and WhatsApp agent replies
as well as other queued durable work. A signed inbound SMS webhook
intentionally performs only the minimum durable transaction: it stores one
tenant-owned message, resolves its per-peer SMS session, creates one
reply/outbox record, claims the provider event, enqueues one
`communication_reply` run, and returns. LLM and provider submission happen
later in this worker. The model returns response text; the adapter-owned
dispatcher sends it through the server-verified route.

The agent-run worker is a long-running process that polls the durable queue.
Run the scheduler as a separate long-running process so due schedules, delayed
retries, and stale worker leases are discovered. Database claims, leases, and
tenant re-entry remain the execution authority, so multiple worker replicas
cannot claim the same run.

For SMS, the Phone page distinguishes a received message, its correlated run,
and reply states from generation through provider acceptance and carrier
delivery.
If the message is present but the run stays queued, verify that the worker uses
the same database and vault configuration as the API and that the user has an
active text provider. Inbound reply delivery does not require or expose the
send-SMS integration tool to the model. Readiness reports worker liveness as
unknown when it cannot be proven from durable state rather than assuming a
process is healthy.

WhatsApp uses the same durable run shape with the `whatsapp:v1` response
profile, but transport submission belongs to a separate outbox and
`whatsapp-worker`. The bridge acknowledges an inbound live one-to-one text only
after the provider message ID, message, per-peer conversation, reply binding,
and run are durable. The run worker generates the response and commits one
`queued` outbox item; completing the run does not mean a handset received the
reply.

Run the transport worker locally with:

```bash
make worker-whatsapp
```

The worker claims outbox rows only for a validated, connected session. State
advances from `queued` to `submitting`, then `sent`, `failed`, or `ambiguous`.
A conclusive WhatsApp message ID changes the correlated reply to
`provider_accepted`; recipient delivery receipts are not yet persisted. A
worker takeover marks an interrupted submission ambiguous instead of replaying
it, and stale lease generations cannot complete current work.
Disconnect fails queued replies and marks an already-submitting reply
ambiguous.

An approved durable agent can search saved contacts and place a call by contact
ID through the existing phone integration tools. A one-shot schedule can use
that same flow; Voxos does not create a separate call-task queue. Because
provider submission is not yet exactly-once across an uncertain response or a
fresh run retry, do not automatically retry a scheduled place-call action after
an ambiguous provider result.

## Long-Running Operations

Codex Cloud tasks use the same destination contract. Task creation can select a
same-agent chat or create a titled task chat atomically with the run. The task
posts concise, retry-safe messages when submission succeeds and when tracking
reaches a completed, failed, cancelled, or uncertain terminal state. It does
not post on every remote poll. Operations retains the detailed progress events
and links to both the task chat and the safe remote task URL.

Chat delivery is secondary to operation state. If a linked chat is deleted
while work is running, the external result and run state remain authoritative;
submitted Codex work continues tracking from its durable task record, and the
worker records or logs the skipped update instead of repeating the external
operation. Failures that do not already produce a remote or uncertain terminal
message post one generic failure message when the run exhausts its retry
attempts.

## Events

Live run events stream through the API. The frontend shows live events on the
Operations Events tab and activity panes on Chat and Voice. Events remain the
diagnostic audit stream and are not copied wholesale into chats.

## Tool Policy Snapshots

When work starts, Voxos captures an effective tool policy snapshot. This keeps a
run's tool permissions and equivalent-tool provider choices stable even if the
user changes grants or preferred routes later. A resolved action route contains
the selected exact tool source or an explicit unavailable result for a pinned
provider; runtime discovery cannot silently choose another implementation.
Subagents receive a policy intersected with parent permissions and resolve
routes against that intersection. LiveKit voice sessions also persist an
effective policy before tool composition so an active phone call cannot gain
access or switch providers after a later contact, grant, or route edit.

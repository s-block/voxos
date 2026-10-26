# Browser Accounts

Last updated: 2026-08-26

Voxos exposes one general `use_browser` tool in text chat and authenticated
in-app voice. It accepts a complete browser instruction and either:

- a public HTTPS start URL for ordinary browsing; or
- an opaque handle for a saved Browser Account assigned to the exact agent.

A Browser Account is an optional credential profile, not a separate type of
browser agent. Selecting a profile adds its authorized origins and encrypted
login state to the same general browser-task contract. Website behavior belongs
in the agent instruction or a reusable skill; it is not encoded in provider or
application code.

Browser Accounts are enabled by default and require no values in `.env`. Voxos
supports three profile providers:

- **Voxos Local Browser (recommended)** stores an encrypted website username
  and password in the user secret vault. Root Docker Compose runs a private
  Stagehand task service and pinned Steel Chromium process. Every task gets an
  ephemeral browser session; no Chromium profile directory is shared.
- **Browserless stored credentials** stores the same encrypted credential pair
  in Voxos and supplies it to Browserless only while an authorized task is
  running.
- **Browser Use managed profile** lets the user sign in manually in a
  short-lived Browser Use Cloud live session. Browser Use retains the profile;
  Voxos stores an encrypted opaque profile reference, not the website password.

The browser task runtime uses the encrypted credential for the text provider
currently selected in Voxos. It keeps a selected model that is explicitly
catalogued for browser use; otherwise it chooses that provider's browser-model
route. NVIDIA therefore keeps GPT-OSS 20B for ordinary chat but uses Step 3.7
Flash inside browser tasks. Routing never changes providers or crosses
credential boundaries.

## General Browser Tasks

For public browsing, ask the agent to use the browser and identify where to
start. The model passes a complete instruction and public HTTPS `start_url` to
`use_browser`. Text chat can add a narrow set of exact `allowed_origins` when a
legitimate workflow crosses sites. Public tasks do not use saved credentials or
acquire a Browser Account lease.

For signed-in work, ask naturally, for example, “Use my Eon Next profile to
sign in and tell me the next payment shown.” The tool description lists only
safe labels, primary origins, and opaque handles for profiles assigned to that
agent. Voxos resolves the selected profile, provider, origins, session, and
secrets outside model-controlled arguments and checks the assignment again at
execution. The model never receives the username, password, provider token,
profile ID, or model API key.

Browser tasks default to `read_only`. Read-only tasks may navigate,
authenticate, dismiss overlays, and inspect information, but their browser
policy forbids purchases, payments, messages, uploads, cancellations, profile
changes, and other writes. `state_changes_allowed` is available only when the
user explicitly requested a state-changing browser action. This policy is an
agent control, not an enforceable transaction checkpoint, so saved website
identities should still have the least privilege practical for their intended
work.

The built-in `browser-use` skill tells the Voxos agent how to formulate a
complete task, select public browsing or an assigned profile, keep origins
narrow, and choose the action mode. Additional user skills can provide reusable
site or workflow guidance without changing the browser service.

## Local Browser Setup

1. Start the normal Docker Compose stack. It includes private `browser-agent`
   and `steel-browser` services; neither publishes a host port.
2. Open **Browser Accounts** and keep **Voxos Local Browser (recommended)**
   selected.
3. Enter a label, one exact public HTTPS website origin, any reviewed login or
   SSO origins, the website username and password, and the agents that may use
   the profile.
4. Ask the assigned agent to complete a browser task with that profile. No
   per-message selector or repeated approval is required.
5. For unattended work, explicitly attach the ready local profile to a schedule
   in **Operations**. The queued run receives an immutable account snapshot and
   must execute its persisted browser instruction.
6. Use **Edit details** to change the label, primary origin, reviewed login
   origins, or assignments. Replace stored credentials separately after a
   password change. Revoke or delete the profile when it is no longer needed.

Local execution sends the agent's instruction unchanged to the generic
Stagehand service together with a bounded step count, exact origin set, action
mode, selected model transport, and optional named secret variables. The
service contains no website, login, dashboard, billing, or extraction workflow.
The pinned self-hosted Steel runtime supports one active session, so the service
serializes local tasks. Signed-in use also holds an account-specific database
lease to prevent concurrent use across API or worker replicas.

The reviewed origins are exact public HTTPS origins. A CDP guard pauses new
pages and popups and blocks top-level HTTP or HTTPS navigation outside that set
before it loads. Cross-origin scripts, frames, images, and API requests required
by an approved page may still load; the guard is a navigation boundary, not a
complete network sandbox. Add every legitimate identity-provider origin used
during login. MFA, passkeys, CAPTCHA, device approval, and automation blocking
can still stop an unattended task. Voxos reports a blocked task rather than
guessing the result.

## Browserless Setup

1. Open **MCP**, add Browserless, save its API token as bearer authentication,
   enable and trust the connection, test it, and allow `browserless_agent` for
   the intended agent.
2. Create a Browser Account using that connection. Supply the label, exact
   website and reviewed SSO origins, username, password, and agent assignments.
3. Ask the assigned agent to use the profile. Voxos calls the same general
   `use_browser` tool; the saved profile selects Browserless behind the server
   boundary.
4. For unattended work, attach the ready Browserless profile to a schedule for
   the same assigned agent.

The Browser Accounts form lists only supported provider connections whose
required execution tool is allowed. If the form asks for
`browserless_agent`, add that tool to the connection's allowed tools and agent
grant, save the MCP tools, and refresh the form. Website credentials are not
submitted while the provider requirement is unresolved.

Browserless runs its hosted agent over the authenticated `/chromium/cli`
WebSocket endpoint. Voxos sends the instruction, start origin, reviewed origin
set, bounded step count, action policy, and vault credentials for that task.
Returned text is bounded, and exact occurrences of the supplied username and
password are redacted before it enters model context. Browserless can still
observe credentials, browser content, and the authenticated session during the
task. See the
[Browserless hosted agent API](https://docs.browserless.io/open-api/chromium-cli).

The default endpoint is the Browserless London fleet.
`BROWSER_ACCOUNTS_BROWSERLESS_BASE_URL` can select another official fleet or a
trusted self-hosted deployment. Browserless stored-credential automation works
only when the site can complete authentication with the stored username and
password. Voxos does not store TOTP seeds, recovery codes, passkeys, or security
question answers.

## Browser Use Cloud Setup

1. Open **MCP**, configure Browser Use Cloud with API-key-header
   authentication, enable and trust it, test it, and grant `run_session` to the
   intended agent.
2. Create a Browser Account with its two-letter browser country, exact website
   origin, and every exact login or SSO origin. Select **Start secure sign-in**.
3. Sign in and complete MFA or a passkey in the non-recorded Browser Use live
   session. Return to Voxos and select **I'm signed in**.
4. Ask for a browser task in Chat or authenticated in-app voice and select the
   saved profile through natural language.
5. Reauthenticate if the managed profile expires. Revoke or delete the account
   to stop known sessions and request remote profile deletion.

Changing the browser country invalidates a managed profile and requires secure
sign-in again. Browser Use managed profiles are not available to unattended
schedules; schedules support stored-credential local and Browserless profiles.

## Authorization And Scheduling

Saved-profile browser execution requires the intersection of:

- a verified local Voxos user and tenant-scoped database session;
- a persisted web-chat root run, authenticated owner voice session, or
  scheduled root run owned by that user;
- a ready Browser Account assigned to the exact agent;
- an opaque profile handle currently advertised to that agent;
- for hosted profiles, the enabled, trusted, connected MCP provider and exact
  high-risk tool grant; and
- an available account lease.

Text chat sends the browser instruction provided in the `use_browser` call and
binds signed-in execution to the durable run and exact client turn. Owner voice
binds it to the latest finalized user utterance and server-issued message ID.
The selected account and request are consumed before provider execution, and
the same account cannot be replayed for that turn. Subagents and phone callers
do not receive saved-profile authority.

A schedule stores an explicitly selected local or Browserless Browser Account
ID after validating tenant ownership and exact-agent assignment. Each queued
run snapshots that ID, so later schedule edits cannot change authority already
assigned to a run. The backend obtains the exact task from the persisted run and
requires browser execution in the first model round.

## Stored Data And Secrets

Voxos stores the profile label, provider, optional provider-connection ID,
normalized origins, authentication mode, status, assigned agents, lifecycle
timestamps, leases, and category-only audit events. Interactive signed-in
execution stores a consumed authorization with user, account, agent, session,
client-turn identifier, task, origins, and optional durable run. Browser Use
profiles also store the two-letter browser country.

Secrets use separate context-bound vault purposes:

- local and Browserless username/password payloads use
  `BROWSER_ACCOUNT_CREDENTIAL`;
- Browser Use profile references use `BROWSER_PROFILE_REFERENCE`;
- temporary provider task and session handles use
  `BROWSER_SESSION_REFERENCE`; and
- the local runtime resolves the selected text-provider key and its
  capability-routed browser model from the existing vault entry only for the
  task.

Plaintext credentials, provider tokens, profile or session IDs, CDP URLs, and
live-view URLs are never Browser Account response fields. Browser Account
validation errors omit submitted values, and credential and setup responses use
`Cache-Control: no-store`.

Database encryption protects against a database-only compromise. It does not
protect credentials from an authorized Voxos backend, the active local browser
runtime or hosted provider, or the target website. Browser output can enter
durable chat, transcript, run, and tool-event records under their normal
retention rules.

## Safety And Residual Risk

| Threat or asset | Primary controls | Residual risk |
| --- | --- | --- |
| Database-only compromise | AES-GCM or KMS-envelope vault, context-bound secret purposes, forced tenant RLS | Key or backend compromise changes the outcome. |
| Cross-tenant or wrong-agent access | Firebase-derived local user, scoped sessions, fresh ownership and assignment checks, forced RLS | A superuser or `BYPASSRLS` application role invalidates database isolation. |
| Model supplies authority | One typed tool, opaque assigned-profile handles, fresh grant checks, server-owned credentials and sessions | The model chooses among advertised profiles; webpage content can still influence the browser agent. |
| Prompt injection | Page content is explicitly untrusted, instructions and variables are separated, origin and action policies are included | Prompt policy is not a complete browser action or network interceptor. |
| Scheduled credential use | Authenticated schedule creation, explicit profile binding, immutable per-run snapshot, required first-round execution | Anyone authorized to edit the schedule can change future runs. |
| Browser runtime or provider compromise | Private local services or separate hosted provider token, bounded sessions, local top-level navigation guard, least-privilege identities | The runtime or provider sees active credentials, authenticated state, and page content. |
| Secret in provider output | Exact credential redaction and output cap | Transformed, encoded, partial, or page-derived sensitive data may still be returned. |
| Concurrent profile use | One heartbeating database lease per Browser Account; serialized local runtime; expired-lease reconciliation | Activity outside Voxos can still race with Voxos. |
| Remote cleanup failure | Browser Use deletion retry and backlog metric; Browserless waits for its executor-owned WebSocket to close | Provider-side state may require console cleanup. |

## Incident Runbook

For a suspected credential, provider-token, session, or browser-content
compromise:

1. Revoke the affected Browser Account. Set
   `BROWSER_ACCOUNTS_ENABLED=false` only when a broader account-feature kill
   switch is required.
2. For hosted mode, disable or revoke the associated Browserless or Browser Use
   MCP connection. For local mode, isolate the private browser services while
   investigating.
3. Revoke target-site sessions and rotate the website password. Replace the
   stored credentials only after root-cause review.
4. For Browser Use, stop remaining sessions and verify remote profile deletion.
   Preserve `deletion_pending` so cleanup can retry.
5. Review category-only audit events and operational metrics. Do not copy page
   content, full URLs, task text, or secret values into logs or incident
   tickets.
6. Re-enable the profile only after session or key rotation and validation with
   a non-production identity.

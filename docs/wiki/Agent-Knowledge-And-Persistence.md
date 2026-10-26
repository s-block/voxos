# Agent Knowledge And Persistence

Last updated: 2026-10-05

Voxos agents are durable. Their identity, memories, skills, task plans, sessions,
runs, schedules, transcripts, usage, tool events, grants, action-level tool
route preferences, audits, reflections, and proposals are persisted in
Postgres.

## Tool Route Preferences

Each agent can keep an automatic or pinned provider preference for a semantic
action such as `web.search`. Preferences do not grant tools. Resolution starts
from the agent's allowed tools, attaches trusted route metadata from the MCP
preset catalog, and selects at most one implementation for each action. The
result is copied into the effective policy for a text run, scheduled run,
subagent, or voice session.

Automatic preferences use catalog priority and can move to the next available
authorized implementation. Pinned preferences identify one exact tool source
and server and fail closed if it is unavailable. Subagents resolve again after
the parent and target policy intersection, so routing cannot restore a tool
removed by inheritance rules.

## Agent Identity

Each user has a default agent. The Agent page can update the display name and
description. The backend stores identity in `user_agents` and scopes ownership by
the durable local Voxos user id.

## Memories

Memories are retained facts, preferences, instructions, relationships, project
context, or task context. A memory has:

- Kind.
- Status such as active, proposed, archived, superseded, rejected, or hidden.
- Sensitivity.
- Source.
- Text.
- Normalized key.
- Confidence.
- Optional evidence session, transcript, or reflection ids.

Users can create, edit, and confirm memories in Knowledge. Granted agent tools
can inspect or archive memories and submit suggested knowledge through the
account improvement policy. Model-supplied confirmation and source claims do
not establish owner approval.

## Skills

Skills are reusable instruction documents. Developer skills can ship with the
app, and user skills are user-owned records. Each skill has a name, slug,
category, description, Markdown body, status, scope, and timestamps.

Agents can list relevant active skills and read a specific skill. Granted
mutating tools can suggest new skills, stage revisions, or archive user skills.
Revisions leave the active document intact until owner approval. Drafts are
labelled as awaiting review; active skills remain subordinate to developer
instructions and tool grants.

### Curated MCP example skills

Voxos has three distinct skill sources:

- Developer skills ship with the application and are globally available.
- User skills are editable, tenant-owned database records.
- Curated MCP examples live under `examples/skills/` in the repository and
  become ordinary user skills only when an operator selectively installs them.

List the available example groups without connecting to the database:

```bash
make example-skills-list
```

Preflight one group for an explicit local Voxos user before writing anything:

```bash
make example-skills-load USER_ID=<local-voxos-user-id> MCP=openbnb DRY_RUN=1
```

Remove `DRY_RUN=1` to install the ready examples. `MCP` accepts a canonical
group such as `github`, `openbnb`, `google-workspace:gmail`, or a
comma-separated selection. Bare `google-workspace` expands to every curated
Workspace product group and reports that expansion. There is no implicit
all-groups mode.

Installation checks the selected user's current MCP records and reports created,
existing, blocked, and reconnect-warning results. It does not connect OAuth,
enable tools, or grant them to an agent. Installed copies can be edited in
Knowledge. A repeated load never overwrites an existing slug in any status,
including an archived or user-edited copy. To intentionally load a newer
template, permanently delete the existing user skill in Knowledge and run the
same selective command again.

## Task Plans

Task plans persist multi-step work. A plan stores the objective, priority,
status, acceptance criteria, step list, and events. Steps can be proposed,
active, in progress, blocked, completed, cancelled, or archived.

The Knowledge page edits task plans directly. The Operations page shows runs and
schedules that execute work over time.

## Subagents

Subagents are child agent runs. They are persisted as child `AgentRunRecord`
rows under the same local user. Subagents keep parent, root, and session lineage
and receive an effective tool policy intersected from the parent run and target
agent. The runtime must use `SubAgentService` so depth, fanout, ownership, and
tool policy checks are not bypassed.

## Account History And Improvements

Authenticated owner text and LiveKit voice conversations can use
`search_agent_history`, `read_agent_history`, and `record_agent_improvement`
when granted. History includes all agents in the same account. Search defaults
to seven days, accepts windows up to 31 days, and filters by channel, agent,
category, or tool. Pages contain at most 100 records and use a stable cursor.
Citations identify retained transcripts, tools, runs, sessions, or runtime
errors. Reads add bounded context. Coverage and truncation describe the returned
records; event counts are not incident totals.

History returns sanitized excerpts and operational summaries. Prompt snapshots,
raw arguments, credentials, and raw runtime payloads are excluded. Phone speech
is available only when the persisted call explicitly enables transcripts;
private speech is withheld while operational outcomes remain visible. Phone
participants and subagents cannot use the account history or learning tools.

Open **Knowledge > Improvements** (`/knowledge/improvements`) to inspect
proposals, policy reasons, model assessments, citations, and previous/applied
content. Approve or reject a proposal, undo an applied change, or load older
improvements. Review binds to the exact saved content and detects later target
edits. Undo preserves later owner changes and reports a conflict when restoration
would overwrite them.

The account starts in review-only mode. Enable **Automatically apply
well-supported, low-risk new memories and skills** to save eligible additive
knowledge. Both the candidate and a separate assessment must score at least
0.95. Complete supporting evidence and current active knowledge must be
available, with no detected conflict. This is a policy threshold, not a measured
accuracy guarantee.

Automatic memory eligibility is limited to low-risk facts, preferences,
projects, and task context supported by an authenticated owner statement or
independent observations. New skills need corroboration across conversations
and observed successful tool outcomes. Sensitive content, revisions, uncertain
claims, and incomplete context require review. Unsafe content is rejected.
Diagnostic follow-ups remain review records. Repeated submission returns the
same proposal and does not reactivate rejected or undone knowledge.

Deleting a source chat or redacting its run withdraws dependent proposals and
scrubs their copied content. Unchanged automatic knowledge is archived and
scrubbed, including previously undone or archived targets. Explicitly approved
knowledge and later owner-authored edits are retained. History retrieval results
are not copied into durable tool-output evidence.

See [Operations Runs And Schedules](Operations-Runs-And-Schedules.md) for
error-triggered analysis and model prerequisites.

## What Is Not User Knowledge

The product documentation index is global product documentation. It is not
tenant-owned user data and must not contain provider keys, chat transcripts,
private memories, phone numbers, OAuth tokens, or other user secrets.

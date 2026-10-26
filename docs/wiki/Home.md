# Voxos Documentation

Last updated: 2026-08-26

Voxos is a user-owned AI agent backend and local console. It combines text chat,
LiveKit voice sessions, durable agent state, schedules, inbound agent webhooks, phone integrations, a
WhatsApp Business linked-device route, remote MCP servers, encrypted
provider credentials, secure Browser Accounts, and tool approval workflows.

This wiki is the canonical in-repository documentation set. It follows GitHub
Wiki conventions: `Home.md` is the landing page, `_Sidebar.md` is the navigation
source, and page filenames become wiki page titles when copied to a GitHub Wiki
repository.

## Start Here

- [Quick Start](Quick-Start.md) explains local setup, Docker Compose, and the
  frontend.
- [Frontend Guide](Frontend-Guide.md) walks through Chat, Voice, Agent,
  Knowledge, Operations, Providers, Phone, Connection, Tools, MCP, and Account.
- [Providers And Keys](Providers-And-Keys.md) explains text and voice provider
  credentials.
- [Self-Hosted Models](Self-Hosted-Models.md) covers OpenAI-compatible local or
  remote endpoints and current Linux, macOS, Windows, and Docker setup.
- [Voice And LiveKit](Voice-And-LiveKit.md) explains LiveKit setup and realtime
  voice sessions.
- [Phone Integrations](Phone-Integrations.md) covers Twilio, Telnyx, SMS/voice,
  and WhatsApp Business linked-device setup and messaging.
- [MCP And Tools](MCP-And-Tools.md) explains remote MCP servers and internal
  tool approvals.
- [Browser Accounts](Browser-Accounts.md) explains the built-in local Stagehand
  browser, Browserless encrypted website credentials, Browser Use profiles,
  chat and schedule authority, provider
  trust, and incident response.
- [Agent Knowledge And Persistence](Agent-Knowledge-And-Persistence.md) explains
  agents, subagents, memories, skills, task plans, and what is persisted.

## Developer Areas

- [Developer Setup](Developer-Setup.md) covers local development commands,
  tests, workers, and migrations.
- [Architecture And Security](Architecture-And-Security.md) covers API
  composition, tenancy, RLS, secret storage, and authorization boundaries.
- [Docs Indexing And Agent Retrieval](Docs-Indexing-And-Agent-Retrieval.md)
  explains how the wiki is validated, chunked, indexed, and exposed to agents.

## Existing Focused Docs

- [User MCP Servers](../user-mcp-servers.md)
- [MCP Service Catalog For VoxOS](../mcp-service-catalog.md)

## Verification Notes

This wiki was created from the backend routers and services, the React frontend
routes, the existing README files, and a signed-in local UI route sweep on
2026-07-05, with later source and automated-test refreshes recorded on each
page. External provider flows such as real phone calls, a real WhatsApp Business
handset link, OAuth provider approvals, LiveKit media, and paid model API calls
still require valid external accounts or credentials in the operator's
environment.

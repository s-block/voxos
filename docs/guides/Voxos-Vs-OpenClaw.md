# Voxos vs OpenClaw: two self-hosted AI agents compared

Last updated: 2026-11-06

Voxos and OpenClaw are both MIT-licensed, self-hosted AI agents that can take
phone calls and use WhatsApp and SMS. They differ most in who shares one install.

This page is written by the Voxos project. The facts about OpenClaw come from
[its documentation](https://docs.openclaw.ai/) on the date above, so check there
before you decide. OpenClaw changes quickly.

## At a glance

| | Voxos | OpenClaw |
| --- | --- | --- |
| License | MIT | MIT |
| Runs on | Docker Compose or Kubernetes, with Postgres | A gateway on your own machine or server |
| Phone calls | Twilio, Telnyx | Twilio, Telnyx, Plivo, through its Voice Call plugin |
| Realtime voice | OpenAI, Gemini, xAI | Includes OpenAI and Gemini examples |
| Messaging | SMS, WhatsApp | WhatsApp, Twilio SMS and many other channels |
| MCP | Remote servers only (Streamable HTTP, SSE) | Stdio, SSE and Streamable HTTP |
| Schedules | Yes | Yes, cron jobs |
| Sign-in | Firebase | Pairing and allowlists |

## The main difference: sharing an install

OpenClaw's [security guide](https://docs.openclaw.ai/gateway/security) assumes one
trusted boundary per gateway: one operator, or a team whose members trust each
other. For people who don't trust each other, it recommends separate gateways and
credentials.

Voxos is built so several signed-in users can share one deployment:

- Postgres [row-level security](../wiki/Architecture-And-Security.md) separates
  each user's data.
- Provider keys and tokens are encrypted per user, with AES-GCM locally or AWS or
  Google Cloud KMS in deployments. OpenClaw's
  [storage guide](https://docs.openclaw.ai/gateway/security/secrets-and-storage)
  describes files under its state directory, protected with file permissions and
  full-disk encryption.
- Each agent only gets the [tools you grant](../wiki/MCP-And-Tools.md#tool-approval-flow),
  and each decision is audited.
- Phone callers without the admin flag get a limited set of tools and a prompt
  that leaves out your instructions and memories. See
  [Contacts and caller access](../wiki/Phone-Integrations.md#contacts-and-caller-access).

OpenClaw has its own controls too, including per-agent tool allow and deny lists,
sandboxing, and DM pairing. See its
[tool permissions guide](https://docs.openclaw.ai/gateway/security/tool-permissions).

## Choose OpenClaw if

- You want one personal assistant, and you're the only person who needs to trust it.
- You want a lot of chat channels, or Plivo for calls.
- You want local stdio MCP servers without running a gateway in front of them.
- You want a very large community, and plugins to go with it.
- You'd rather skip Firebase and Postgres.

## Choose Voxos if

- Several people will share one install, and each needs their own data and keys.
- You want encrypted per-user credentials, backed by a cloud KMS in production.
- You want durable runs and schedules stored in Postgres.
- You want to deploy with the [Helm chart](../../helm/README.md).
- You want a web console and a [mobile app](../../app/README.md) for your own server.

## Try it

Start with the [Quick Start](../wiki/Quick-Start.md), then connect a number with
the [Twilio guide](Twilio-Phone-Agent.md) or the
[Telnyx guide](Telnyx-Phone-Agent.md).

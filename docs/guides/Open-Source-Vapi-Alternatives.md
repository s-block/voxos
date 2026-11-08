# Open-source alternatives to Vapi and Retell

Last updated: 2026-11-06

Vapi and Retell are hosted voice AI platforms. Voxos and Dograh are open-source
projects you run yourself, and they solve different problems.

This page is written by the Voxos project. The facts about other products come
from their own pages on the date above, so check those pages before you decide.

## At a glance

| | Voxos | Dograh | Vapi | Retell |
| --- | --- | --- | --- | --- |
| Where it runs | Your infrastructure | Your infrastructure | Vapi's managed platform | Retell's platform |
| License | MIT | BSD 2-Clause | Not stated on its pricing or docs pages | Not stated on its pricing page |
| Listed price | Free software, plus your own providers | Free to self-host, plus your own providers | $0.05/min hosting, plus providers | $0.07 to $0.31/min pay as you go |
| Phone providers | Twilio, Telnyx | Twilio, Vonage, Telnyx, Plivo, Vobiz, Cloudonix, Asterisk ARI | Twilio, Vonage, Telnyx, Vapi telephony | Twilio, Telnyx, SIP, its own numbers |

Sources: [Dograh](https://github.com/dograh-hq/dograh),
[Vapi pricing](https://vapi.ai/pricing),
[Retell pricing](https://www.retellai.com/pricing).

## When a hosted platform fits better

Choose Vapi or Retell if you don't want to run servers, patch anything, or be the
one who gets paged. A hosted platform also makes sense if you'd rather have a
vendor handle scaling and you're fine paying per minute for it.

## When Dograh fits better

Dograh describes itself as the open-source, self-hostable alternative to Vapi and
Retell. It's the closer match if your goal is a voice agent product with more
telephony options than Voxos has. Its README notes telemetry is on by default,
with an `ENABLE_TELEMETRY=false` opt-out.

## When Voxos fits better

Voxos is a whole agent rather than only a call pipeline. One agent has
[voice and phone calls](../wiki/Voice-And-LiveKit.md),
[SMS and WhatsApp](../wiki/Phone-Integrations.md),
[text chat](../wiki/Chat-And-Text.md),
[memory and skills](../wiki/Agent-Knowledge-And-Persistence.md),
[schedules](../wiki/Operations-Runs-And-Schedules.md) and
[MCP tools](../wiki/MCP-And-Tools.md). It suits you if you want one agent that
does things for you across all of those, on infrastructure you control.

Voxos places outbound calls to saved contacts with a stated goal. It isn't built
for bulk dialing campaigns.

You should also know that voice runs on OpenAI, Gemini or xAI realtime models, so
self-hosting Voxos doesn't make voice calls fully local. Setup needs a Firebase
project for sign-in.

## What about the cost?

Self-hosting removes the platform fee, but you pay for your own server and your
own time. The [voice agent cost calculator](../../web/src/pages/voice-agent-cost.astro)
shows where that crosses over for your call volume.

## If you want to write the agent yourself

[LiveKit Agents](https://docs.livekit.io/agents) and
[Pipecat](https://pipecat.ai) are open-source frameworks for building your own
voice agent in code. Voxos uses LiveKit under the hood.

## Try it

Start with the [Quick Start](../wiki/Quick-Start.md), then connect a number with
the [Twilio guide](Twilio-Phone-Agent.md) or the
[Telnyx guide](Telnyx-Phone-Agent.md).

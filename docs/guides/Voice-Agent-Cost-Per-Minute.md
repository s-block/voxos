# How much does a voice AI agent cost per minute?

Last updated: 2026-11-13

A self-hosted voice AI phone agent costs about $0.03 to $0.11 a minute in
phone and model fees, plus your own server. The phone line is about a cent a
minute. The voice model is most of the bill, from about $0.02 to $0.10 a minute
depending on which one you pick. Hosted platforms add their own per-minute fee
on top.

Prices on this page come from each provider's own pricing page on 13 November
2026, in US dollars for US numbers. They change, so check before you budget.

## Where the money goes

A voice agent call has four costs:

1. **The phone line.** You pay the phone provider for each minute, a small fee
   to stream the call audio to your agent, and a monthly fee for each number.
2. **The voice model.** A realtime speech-to-speech model listens and talks
   back. Some charge per minute and some per token.
3. **Your server.** It runs the agent and the LiveKit media server. It's a flat
   monthly cost that depends on where you host and how many calls run at once.
4. **A platform fee,** if you use a hosted platform such as Vapi or Retell
   instead of running the agent yourself.

## Phone minutes

| | Inbound | Outbound | Audio streaming | Local number |
| --- | --- | --- | --- | --- |
| [Twilio](https://www.twilio.com/en-us/voice/pricing/us) | $0.0085/min | $0.014/min | $0.0044/min | $1.15/month |
| [Telnyx](https://telnyx.com/pricing/voice-api) | $0.0052/min | $0.007/min | $0.0035/min | $1.00/month |

Telnyx's figures are its $0.002 Voice API rate plus its starting SIP trunking
rates. So an inbound minute costs about $0.013 on Twilio and $0.009 on Telnyx,
streaming included.

## Voice models

These are the realtime models Voxos can run. The per-minute figures for OpenAI
are estimates from its token prices, so read the notes under the table.

| Model | Listed price | About per minute |
| --- | --- | --- |
| [Google Gemini 3.1 Flash Live](https://ai.google.dev/gemini-api/docs/pricing) | $0.005/min audio in, $0.018/min audio out | $0.023 |
| [OpenAI gpt-realtime-2.1-mini](https://developers.openai.com/api/docs/pricing) | $10 per 1M audio tokens in, $20 out | $0.030 |
| [xAI Grok voice](https://docs.x.ai/developers/models) | $0.08/min | $0.080 |
| [OpenAI gpt-realtime-2.1](https://developers.openai.com/api/docs/pricing) | $32 per 1M audio tokens in, $64 out | $0.096 |

- The Gemini and OpenAI figures assume audio runs both ways for the whole call.
  In a real call people take turns, so the fresh audio usually costs less.
- OpenAI counts 600 audio tokens for each minute the caller talks and 1,200 for
  each minute the agent talks
  ([OpenAI's cost guide](https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime)).
  Each reply also re-reads the conversation so far, mostly at the cheaper cached
  rate, so later minutes of a long call cost more than early ones.
- Long system prompts and tool calls add text tokens on top.

[Realtime voice model pricing compared](Voice-Model-Pricing.md) covers more
models, including ones Voxos doesn't run.

## A worked example

Say you take 1,000 minutes of inbound calls a month on one Twilio number, with
gpt-realtime-2.1-mini:

| | Cost a month |
| --- | --- |
| Phone minutes and streaming: 1,000 × ($0.0085 + $0.0044) | $12.90 |
| One local number | $1.15 |
| Voice model: 1,000 × $0.030 | $30.00 |
| **Total before your server** | **$44.05** |

Swap the model and the total moves the most. Gemini 3.1 Flash Live brings it to
about $37, xAI Grok voice to about $94, and gpt-realtime-2.1 to about $110.

On a hosted platform, the platform fee alone for the same 1,000 minutes would be
$50 on Vapi ($0.05/min hosting) or $55 on Retell ($0.055/min voice
infrastructure), before models and phone minutes
([Vapi pricing](https://vapi.ai/pricing),
[Retell pricing](https://www.retellai.com/pricing)).

## When self-hosting is cheaper

Self-hosting swaps the per-minute platform fee for a flat server bill. If your
server costs $40 a month, it matches Vapi's $0.05 fee at 800 minutes a month.
Above that, every extra minute is cheaper self-hosted. Below it, you're paying
for a server you don't fully use, plus your own time to run it.

The [voice agent cost calculator](../../web/src/pages/voice-agent-cost.astro)
does this sum with your own minutes, phone provider, model and server cost.

## Ways to bring the cost down

- **Start with a smaller model.** Mini and flash models cost a fraction of the
  largest ones. Move up only if your calls need it.
- **Keep prompts short and stable.** OpenAI re-reads the conversation on every
  reply, and a prompt that doesn't change mid-call stays cached at the lower
  rate.
- **Keep calls short.** Silence and hold time still cost phone minutes, and
  per-minute models bill for the whole connection.
- **Compare phone providers.** Telnyx's listed rates are lower than Twilio's.
  The [Twilio](Twilio-Phone-Agent.md) and [Telnyx](Telnyx-Phone-Agent.md) guides
  show how to connect each one.

## Try it

Start with the [Quick Start](../wiki/Quick-Start.md), then read
[Voice and LiveKit](../wiki/Voice-And-LiveKit.md) to choose a voice provider.

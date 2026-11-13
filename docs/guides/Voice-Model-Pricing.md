# Realtime voice model pricing compared

Last updated: 2026-11-13

Realtime voice models for AI agents cost from about $0.02 to $0.16 a minute at
list price. Google's Gemini Live models are the cheapest per minute here.
OpenAI and Amazon charge per token, xAI, ElevenLabs and Deepgram per minute, and
some per-minute prices leave out the language model.

This page is written by the Voxos project. Prices come from each provider's own
pricing page on 13 November 2026, in US dollars at pay-as-you-go rates. They
change often, so check the linked page before you decide.

## Speech-to-speech models

| Model | Listed price | About per minute | Voxos runs it |
| --- | --- | --- | --- |
| Google Gemini 3.1 Flash Live, Gemini 3.8 Live | $3 per 1M audio tokens in, $12 out ($0.005/min in, $0.018/min out) | $0.023 | 3.1 Flash Live: yes |
| Google Gemini 2.5 Flash native audio | $3 per 1M audio tokens in, $12 out | Same token prices as above | Yes |
| OpenAI gpt-realtime-2.1-mini | $10 per 1M audio tokens in, $20 out | $0.030 | Yes |
| OpenAI gpt-realtime-2.1 | $32 per 1M audio tokens in, $64 out | $0.096 | Yes |
| OpenAI gpt-live-1 | $0.05/min, plus backend model and tool usage | $0.05 plus model | No |
| xAI Grok voice | $0.08/min | $0.08 | Yes |
| Amazon Nova 2 Sonic, Nova 2.5 Sonic | $3 per 1M speech tokens in, $12 out | Not published | No |
| ElevenLabs Realtime v1 Mini, v1 Max | $0.08/min, plus LLM usage | $0.08 plus LLM | No |

Sources: [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing),
[OpenAI API pricing](https://developers.openai.com/api/docs/pricing),
[xAI models and pricing](https://docs.x.ai/developers/models),
[Amazon Nova pricing](https://aws.amazon.com/nova/pricing/),
[ElevenLabs API pricing](https://elevenlabs.io/pricing/api).

## Voice agent APIs

These bundle speech-to-text, a language model and text-to-speech behind one
per-minute price.

| Service | Listed price | Voxos runs it |
| --- | --- | --- |
| Deepgram Voice Agent, Standard | $0.075/min | No |
| Deepgram Voice Agent, bring your own LLM and TTS | $0.050/min | No |
| Deepgram Voice Agent, Advanced | $0.163/min | No |
| ElevenLabs Speech Engine Agents | $0.08/min | No |

Deepgram bills per minute of WebSocket connection time, and its Growth plan
lowers each rate
([Deepgram pricing](https://deepgram.com/pricing)).

## How the per-minute figures are worked out

Providers that price per token don't publish a per-minute figure, so the table
converts where the provider gives a token rate for audio:

- **Google** lists the per-minute equivalent itself for Gemini 3.1 Flash Live.
  The $0.023 figure adds input and output as if audio ran both ways for the
  whole call, so it's a high estimate.
- **OpenAI** counts 600 audio tokens for each minute the caller talks and 1,200
  for each minute the agent talks
  ([OpenAI's cost guide](https://developers.openai.com/api/docs/guides/voice-latency-cost?voice-api=realtime)).
  The table uses a full minute of each. Each reply also re-reads the
  conversation so far, mostly at the cheaper cached rate, so long calls cost
  more per minute than short ones.
- **Amazon** doesn't publish how many tokens a minute of speech uses, so there's
  no per-minute figure for Nova Sonic here.

Per-minute prices are easier to budget, but they bill for the whole connection,
silence included. OpenAI's token prices skip silence that its voice activity
detection filters out, but add the conversation history each reply re-reads.

## Which one should you pick?

Price is one input. Latency, voice quality, tool calling and languages matter
too, and they're worth testing with your own calls. A sensible order is to start
with a lower-cost model, such as Gemini 3.1 Flash Live or gpt-realtime-2.1-mini,
and move up only if your calls need it.

To see what a model means for your monthly bill, including phone minutes and
your server, use the
[voice agent cost calculator](../../web/src/pages/voice-agent-cost.astro) or
read [how much a voice AI agent costs per minute](Voice-Agent-Cost-Per-Minute.md).

## Using these models with Voxos

Voxos runs OpenAI, Google Gemini and xAI realtime models with your own API key,
so you pay the provider's list price with no markup.
[Voice and LiveKit](../wiki/Voice-And-LiveKit.md) lists the supported providers
and their default models.

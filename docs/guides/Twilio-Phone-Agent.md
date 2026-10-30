# Build a self-hosted AI phone agent with Twilio

Last updated: 2026-10-30

Voxos answers calls to your Twilio number and places calls with a goal, using
OpenAI, Gemini or xAI realtime voice over LiveKit on your own machine.

This guide connects a local Voxos install to a Twilio number, tests a call in
each direction, and keeps callers you don't trust on a short leash.

## What you need

- A local Voxos stack from the [Quick Start](../wiki/Quick-Start.md), with
  sign-in to the console working.
- A Twilio account with a voice-capable number, an API key (its SID and
  secret) and the account's Auth Token.
- An API key for one realtime voice provider: OpenAI, Google or xAI.
- The [ngrok](https://ngrok.com/) CLI with an auth token, so Twilio can reach
  your machine.

## 1. Give the API a public address

Twilio delivers calls and texts to webhooks, so the API needs a public HTTPS
address. In a second terminal, start a tunnel to the local API:

```bash
make ngrok
```

Put the tunnel's `https://` address in `.env` as `API_PUBLIC_BASE_URL`, then
apply it:

```bash
make up
```

Twilio's audio streams use the same host over `wss://`. Connect the number
only after this step, because Voxos gives Twilio its webhook addresses at that
moment. [Public webhooks](../wiki/Quick-Start.md#public-webhooks) has more on
the tunnel.

## 2. Choose a voice

Open **Providers** and save the key for your voice provider. Then open
**Agent** and pick the voice provider, model and voice for the agent that will
take calls. For OpenAI, `gpt-realtime-2.1-mini` is the recommended default for
routine calls; [Voice and LiveKit](../wiki/Voice-And-LiveKit.md#runtime-supported-voice-providers)
compares the models.

## 3. Connect LiveKit

Every call runs in a LiveKit room. Open **Connection** and save your LiveKit
details. The local stack runs a LiveKit dev server on `ws://localhost:7880`,
with the API key `devkey` and the secret `secret`.

## 4. Connect the Twilio number

Open **Phone**, choose Twilio, and enter a label, the Account SID, the API Key
SID and secret, and the Auth Token. Voxos then lists the numbers on the
account. Select yours, with its voice and SMS capabilities.

Voxos creates webhook routes for voice, SMS, status updates and the audio
stream, and points the number's Twilio webhooks at them. If you disconnect the
number later, Voxos puts back the webhook settings it replaced. See
[Twilio setup](../wiki/Phone-Integrations.md#twilio-setup) for the details.

## 5. Save your contacts

Open **Contacts** and add the people the agent should know, each with a phone
number in E.164 form, such as `+447700900123`. Voxos matches callers by their
exact number.

Turn on the admin flag only for people you trust with the agent's full set of
approved tools. Everyone else, including saved contacts without the flag and
unknown or withheld numbers, gets a limited set of tools and a caller-safe
prompt that leaves out your instructions and memories. Caller ID can be
spoofed, so keep high-impact tools away from phone callers. See
[Contacts and caller access](../wiki/Phone-Integrations.md#contacts-and-caller-access).

## 6. Call the agent

Call your Twilio number. Voxos checks Twilio's signature, starts a voice
worker for the call, and the agent answers. The call shows up in the recent
activity on the **Phone** page.

## 7. Have the agent call someone

Agents place calls through a phone tool, and that tool needs a grant. Open
**Tools** and allow the call tool for your agent. Connecting the number
already lets the default agent search your contacts, unless you have set that
yourself.

Then ask in Chat, for example: "Call Sam and ask whether Tuesday at 10:30
still works." The agent looks Sam up in your contacts and hands the saved
contact and the goal to the call tool. Voxos gets the voice model ready before
it dials, and the agent says nothing until Sam answers, then opens with the
goal. [Goal-directed outbound calls](../wiki/Voice-And-LiveKit.md#goal-directed-outbound-calls)
explains each stage.

## If something goes wrong

- **Twilio reports webhook errors, or calls drop straight away.** Check that
  `API_PUBLIC_BASE_URL` matches the running tunnel. If the tunnel's address
  changed, update it, run `make up`, and connect the number again. More checks
  are in [Phone webhooks or media streams fail](../wiki/Troubleshooting.md#phone-webhooks-or-media-streams-fail).
- **The call connects but the agent never speaks.** Work through
  [Voice cannot start](../wiki/Troubleshooting.md#voice-cannot-start).
- **The agent asks whether someone is saved, or forgets the goal.** See
  [Outbound call asks about contacts or loses its goal](../wiki/Troubleshooting.md#outbound-call-asks-about-contacts-or-loses-its-goal).

Before relying on the setup, run the
[four-direction smoke test](../wiki/Phone-Integrations.md#four-direction-smoke-test)
with numbers you are allowed to call and text.

## Next steps

- Let the agent answer texts: [SMS processing and delivery](../wiki/Phone-Integrations.md#sms-processing-and-delivery).
- Link the same number to WhatsApp: [WhatsApp linked device](../wiki/Phone-Integrations.md#whatsapp-linked-device).
- Give the agent more to do with [MCP and tools](../wiki/MCP-And-Tools.md).

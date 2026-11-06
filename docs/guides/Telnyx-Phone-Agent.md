# Build a self-hosted AI phone agent with Telnyx

Last updated: 2026-11-06

Voxos answers calls to your Telnyx number and places calls with a goal, using
OpenAI, Gemini or xAI realtime voice over LiveKit on your own machine.

Voxos sets up the Telnyx side for you, then checks each part of it on request.
If you use Twilio instead, follow the
[Twilio guide](Twilio-Phone-Agent.md).

## What you need

- A local Voxos stack from the [Quick Start](../wiki/Quick-Start.md), with
  sign-in to the console working.
- A Telnyx account with an API key, its webhook public key, and an active number
  that has voice and two-way SMS.
- An API key for one realtime voice provider: OpenAI, Google or xAI.
- The [ngrok](https://ngrok.com/) CLI with an auth token, so Telnyx can reach
  your machine.

## 1. Get ready for calls

These steps are the same for both phone providers:

1. [Give the API a public address](Twilio-Phone-Agent.md#1-give-the-api-a-public-address).
   Do this before connecting the number, because Voxos gives Telnyx its webhook
   addresses at that moment.
2. [Choose a voice](Twilio-Phone-Agent.md#2-choose-a-voice).
3. [Connect LiveKit](Twilio-Phone-Agent.md#3-connect-livekit).

## 2. Connect the Telnyx number

Open **Phone**, choose Telnyx, and enter:

- A label.
- The number in E.164 form, such as `+15555550123`.
- The API key and webhook public key.

Then confirm that Voxos can assign the number to Telnyx routing. Voxos checks
that the number is active and has voice and two-way SMS. It then creates its own
voice and messaging profiles and attaches the number. If the number can't do
something, setup fails with an error that says what's missing. See
[Telnyx setup](../wiki/Phone-Integrations.md#telnyx-setup).

## 3. Check it with Verify

Each connected Telnyx card has a read-only **Verify** action. It reports inbound
SMS, outbound SMS, inbound calls, outbound calls, agent replies and LiveKit
separately, so you can see which part needs attention.

If your tunnel address changed, update `API_PUBLIC_BASE_URL`, run `make up`, and
then use **Repair**. Repair updates the webhooks and assignments Voxos owns.
[Telnyx verify and repair](../wiki/Phone-Integrations.md#telnyx-verify-and-repair)
has the details.

## 4. Save your contacts

Open **Contacts** and add the people the agent should know. Voxos matches
callers by their exact number. Turn on the admin flag only for people you trust
with the agent's full set of approved tools. Everyone else gets a limited set of
tools and a prompt that leaves out your instructions and memories. Caller ID can
be spoofed, so keep high-impact tools away from phone callers. See
[Contacts and caller access](../wiki/Phone-Integrations.md#contacts-and-caller-access).

## 5. Test both directions

Call your Telnyx number, and the agent answers. The call shows up in the recent
activity on the **Phone** page.

To have the agent call someone, open **Tools** and allow the call tool for your
agent. Then ask in Chat: "Call Sam and ask whether Tuesday at 10:30 still works."
[Goal-directed outbound calls](../wiki/Voice-And-LiveKit.md#goal-directed-outbound-calls)
explains each stage. Only call people who are happy to hear from your agent, and
check the rules for automated calls where you are.

Before relying on the setup, run the
[four-direction smoke test](../wiki/Phone-Integrations.md#four-direction-smoke-test)
with numbers you are allowed to call and text.

## If something goes wrong

- **Verify reports a problem.** Follow what it names, then use **Repair**. A
  missing provider object means reconnecting the integration.
- **Calls drop or webhooks fail.** Work through
  [Phone webhooks or media streams fail](../wiki/Troubleshooting.md#phone-webhooks-or-media-streams-fail).
- **The call connects but the agent never speaks.** See
  [Voice cannot start](../wiki/Troubleshooting.md#voice-cannot-start).

## Next steps

- Put the same number on WhatsApp: [Run an AI agent on WhatsApp](WhatsApp-AI-Agent.md).
- See what calls will cost with the
  [voice agent cost calculator](../../web/src/pages/voice-agent-cost.astro).

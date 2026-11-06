# Run a self-hosted AI agent on WhatsApp

Last updated: 2026-11-06

Voxos links a WhatsApp Business number as a companion device, so your agent can
read and reply to direct and group text messages from your own server.

This guide registers a dedicated number and links it to your agent. WhatsApp
decides which numbers can register, and it may change how linked devices work or
restrict an account. Use a number you can afford to lose access to, not your
personal one.

## What you need

- A local Voxos stack from the [Quick Start](../wiki/Quick-Start.md).
- A Twilio or Telnyx number connected to Voxos. Start with the
  [Twilio guide](Twilio-Phone-Agent.md) or the
  [Telnyx guide](Telnyx-Phone-Agent.md).
- A phone with the WhatsApp Business app. It stays the primary device and may
  occasionally need you to link again.

Each Voxos user can link one WhatsApp number. Before buying a number, check
[which numbers work](../wiki/Phone-Integrations.md#choose-the-number). A UK
Telnyx mobile, for example, needs **Verify with a call**.

## 1. Choose how to get the code

Open **Phone**, find **WhatsApp linked device**, and select your number. Pick the
way Voxos should catch the code *before* you request it:

- **Verify with a call**, for a Telnyx number. Voxos answers WhatsApp's call,
  presses the digit it asks for, and shows you the spoken code.
- **Verify with SMS**, only if the number can receive WhatsApp's message route.

## 2. Register the number in WhatsApp Business

In WhatsApp Business, choose **Use a different number** if it offers your
personal one, then enter the Voxos number and request the code. Keep the Voxos
page open. Type the complete code it shows into the app, wait for the app to
confirm, and select **I entered this code** in Voxos.

If the number is already registered, choose **It is already registered** instead.

## 3. Link Voxos as a device

Choose **QR code** or **Pairing code** in Voxos. In WhatsApp Business, open
**Linked devices**, choose **Link a device**, and scan the code or use **Link
with phone number instead**. Keep the page open until it shows **Number verified
and connected**.

## 4. Send it a message

Message the number from another phone. The agent set on the phone integration
picks it up and replies in the chat. Replies run through the durable run worker,
which `make up` starts for you.

## If something goes wrong

Work through the WhatsApp entries in
[Troubleshooting](../wiki/Troubleshooting.md), and read
[WhatsApp linked device](../wiki/Phone-Integrations.md#whatsapp-linked-device)
for how setup and sessions work.

## Next steps

- Give the agent more to do with [MCP and tools](../wiki/MCP-And-Tools.md).
- Teach it routines with the [example skills](../../examples/skills/README.md).

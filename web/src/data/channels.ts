export type ChannelId = "voice" | "phone" | "sms" | "whatsapp";

export interface Channel {
  readonly id: ChannelId;
  readonly name: string;
  readonly title: string;
  readonly summary: string;
  readonly points: readonly string[];
  readonly href: string;
  readonly linkLabel: string;
}

/** The ways people reach a Voxos agent. Claims here must match the linked docs. */
export const channels: readonly Channel[] = [
  {
    id: "voice",
    name: "Voice",
    title: "Just talk to it",
    summary:
      "Realtime speech-to-speech over LiveKit, using OpenAI, Gemini or Grok voice models. Talk the way you would to a person.",
    points: [
      "Web console or the iOS and Android app",
      "Voice and model picked per agent",
      "Transcripts saved with your chat history",
    ],
    href: "/docs/voice-and-livekit/",
    linkLabel: "Voice docs",
  },
  {
    id: "phone",
    name: "Phone calls",
    title: "Give it a phone number",
    summary:
      "Connect a Twilio or Telnyx number. Your agent picks up when people call, and it can ring your saved contacts to get something done.",
    points: [
      "Inbound and outbound calls",
      "Caller access set per contact",
      "Outbound calls with a stated goal",
    ],
    href: "/docs/phone-integrations/",
    linkLabel: "Phone docs",
  },
  {
    id: "sms",
    name: "SMS",
    title: "Text it like a friend",
    summary:
      "Two-way SMS on the same number. Text a question from anywhere and the reply arrives when the work is done.",
    points: [
      "Replies run as durable background tasks",
      "Recent message and call activity",
      "Twilio and Telnyx numbers",
    ],
    href: "/docs/phone-integrations/#sms-processing-and-delivery",
    linkLabel: "SMS docs",
  },
  {
    id: "whatsapp",
    name: "WhatsApp",
    title: "Drop it into WhatsApp",
    summary:
      "Link a WhatsApp Business number as a companion device. Your agent reads direct and group chats and replies right there.",
    points: [
      "Direct and group text messages",
      "Approved messages to saved contacts",
      "An encrypted session for each user",
    ],
    href: "/docs/phone-integrations/#whatsapp-linked-device",
    linkLabel: "WhatsApp docs",
  },
];

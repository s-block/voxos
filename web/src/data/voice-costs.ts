/** List prices read from each provider's own page. US numbers and US dollars. */
/** The calculator page's details, shared by its page, structured data and llms.txt entry. */
export const costCalculatorPage = {
  path: "/voice-agent-cost/",
  name: "Voice AI agent cost calculator",
  description:
    "Estimate what a self-hosted voice AI phone agent costs a month with Twilio or Telnyx, and compare your server bill with Vapi and Retell platform fees.",
} as const;

export const PRICES_CHECKED = "6 November 2026";

export interface PhoneRates {
  readonly label: string;
  readonly inboundPerMinute: number;
  readonly outboundPerMinute: number;
  readonly mediaStreamPerMinute: number;
  readonly numberPerMonth: number;
  readonly note: string;
  readonly source: string;
}

export const phoneProviders = {
  twilio: {
    label: "Twilio",
    inboundPerMinute: 0.0085,
    outboundPerMinute: 0.014,
    mediaStreamPerMinute: 0.0044,
    numberPerMonth: 1.15,
    note: "Local US number.",
    source: "https://www.twilio.com/en-us/voice/pricing/us",
  },
  telnyx: {
    label: "Telnyx",
    // Voice API $0.002 plus the "from" SIP trunking rates: $0.0032 inbound, $0.005 outbound.
    inboundPerMinute: 0.0052,
    outboundPerMinute: 0.007,
    mediaStreamPerMinute: 0.0035,
    numberPerMonth: 1,
    note: "Voice API plus starting SIP trunking rates, local US number. SMS adds $0.10 a month per number.",
    source: "https://telnyx.com/pricing/call-control",
  },
} as const satisfies Record<string, PhoneRates>;

export type PhoneProviderId = keyof typeof phoneProviders;

export interface VoiceModelRate {
  readonly label: string;
  /** Undefined when the provider publishes no per-minute price. */
  readonly perMinute: number | undefined;
  readonly note: string;
  readonly source: string;
}

export const voiceModels: readonly VoiceModelRate[] = [
  {
    label: "Google Gemini Live",
    perMinute: 0.023,
    note: "$0.005/min audio in plus $0.018/min audio out, as if audio ran both ways for the whole call. Treat it as a high estimate.",
    source: "https://ai.google.dev/gemini-api/docs/pricing",
  },
  {
    label: "xAI Grok voice",
    perMinute: 0.08,
    note: "grok-voice-3.0 speech to speech.",
    source: "https://docs.x.ai/docs/models",
  },
  {
    label: "OpenAI Realtime (enter your own)",
    perMinute: undefined,
    note: "OpenAI prices its realtime models per token, with no per-minute figure. For example, gpt-realtime-2.1-mini is $10 per 1M audio tokens in and $20 out. Check your usage to estimate a rate.",
    source: "https://platform.openai.com/docs/pricing",
  },
];

/** Only the platform's own fee. Model and phone costs depend on what you pick there. */
export const platformFees = [
  { label: "Vapi hosting", perMinute: 0.05, source: "https://vapi.ai/pricing" },
  { label: "Retell voice infrastructure", perMinute: 0.055, source: "https://www.retellai.com/pricing" },
] as const;

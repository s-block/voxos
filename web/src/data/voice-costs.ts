import { openAiAudioPerMinute } from "../components/tools/voice-cost";

/** List prices read from each provider's own page. US numbers and US dollars. */
/** The calculator page's details, shared by its page, structured data and llms.txt entry. */
export const costCalculatorPage = {
  path: "/voice-agent-cost/",
  name: "Voice AI agent cost calculator",
  description:
    "Estimate what a self-hosted voice AI phone agent costs a month with Twilio or Telnyx and OpenAI, Gemini or xAI voice models, and compare your server bill with Vapi and Retell fees.",
} as const;

export const PRICES_CHECKED = "13 November 2026";

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
    source: "https://telnyx.com/pricing/voice-api",
  },
} as const satisfies Record<string, PhoneRates>;

export type PhoneProviderId = keyof typeof phoneProviders;

export interface VoiceModelRate {
  readonly label: string;
  readonly perMinute: number;
  readonly note: string;
  readonly source: string;
}

const OPENAI_PRICING = "https://developers.openai.com/api/docs/pricing";
const OPENAI_ESTIMATE_NOTE =
  "OpenAI counts 600 audio tokens for each minute the caller talks and 1,200 for each minute the agent talks. This assumes both talk for the whole call. Each reply also re-reads the call so far at the cached rate, which grows on long calls, so treat it as a rough guide.";

export const voiceModels: readonly VoiceModelRate[] = [
  {
    label: "Google Gemini Live",
    perMinute: 0.023,
    note: "$0.005/min audio in plus $0.018/min audio out, as if audio ran both ways for the whole call. Treat it as a high estimate.",
    source: "https://ai.google.dev/gemini-api/docs/pricing",
  },
  {
    label: "OpenAI gpt-realtime-2.1-mini",
    perMinute: openAiAudioPerMinute(10, 20),
    note: `$10 per 1M audio tokens in and $20 out. ${OPENAI_ESTIMATE_NOTE}`,
    source: OPENAI_PRICING,
  },
  {
    label: "OpenAI gpt-realtime-2.1",
    perMinute: openAiAudioPerMinute(32, 64),
    note: `$32 per 1M audio tokens in and $64 out. ${OPENAI_ESTIMATE_NOTE}`,
    source: OPENAI_PRICING,
  },
  {
    label: "xAI Grok voice",
    perMinute: 0.08,
    note: "grok-voice-3.0 speech to speech.",
    source: "https://docs.x.ai/developers/models",
  },
];

/** Only the platform's own fee. Model and phone costs depend on what you pick there. */
export const platformFees = [
  { label: "Vapi hosting", perMinute: 0.05, source: "https://vapi.ai/pricing" },
  { label: "Retell voice infrastructure", perMinute: 0.055, source: "https://www.retellai.com/pricing" },
] as const;

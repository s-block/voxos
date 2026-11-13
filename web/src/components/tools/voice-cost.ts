import type { PhoneRates } from "../../data/voice-costs";

export interface CostInput {
  readonly minutesPerMonth: number;
  /** Share of minutes that are outbound, from 0 to 1. */
  readonly outboundShare: number;
  readonly phoneNumbers: number;
  readonly voicePerMinute: number;
  readonly serverPerMonth: number;
}

export interface CostResult {
  readonly phone: number;
  readonly voice: number;
  readonly server: number;
  readonly total: number;
}

/** A self-hosted month: phone minutes, media streaming, numbers, the voice model and your server. */
export function selfHostedCost(input: CostInput, rates: PhoneRates): CostResult {
  const minuteRate =
    (1 - input.outboundShare) * rates.inboundPerMinute +
    input.outboundShare * rates.outboundPerMinute +
    rates.mediaStreamPerMinute;
  const phone = input.minutesPerMonth * minuteRate + input.phoneNumbers * rates.numberPerMonth;
  const voice = input.minutesPerMonth * input.voicePerMinute;
  return { phone, voice, server: input.serverPerMonth, total: phone + voice + input.serverPerMonth };
}

/**
 * OpenAI bills realtime audio as tokens: one per 100 ms of caller audio and one
 * per 50 ms of agent audio, so a minute of each is 600 and 1,200 tokens.
 */
export const OPENAI_CALLER_AUDIO_TOKENS_PER_MINUTE = 600;
export const OPENAI_AGENT_AUDIO_TOKENS_PER_MINUTE = 1200;

/** Fresh audio cost of one minute with both sides talking, from per-1M-token prices. */
export function openAiAudioPerMinute(inputPerMillion: number, outputPerMillion: number): number {
  return (
    (OPENAI_CALLER_AUDIO_TOKENS_PER_MINUTE * inputPerMillion + OPENAI_AGENT_AUDIO_TOKENS_PER_MINUTE * outputPerMillion) /
    1_000_000
  );
}

/** Monthly minutes at which a platform's per-minute fee equals your server bill. */
export function breakEvenMinutes(serverPerMonth: number, feePerMinute: number): number {
  return serverPerMonth / feePerMinute;
}

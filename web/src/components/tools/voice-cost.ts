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

/** Monthly minutes at which a platform's per-minute fee equals your server bill. */
export function breakEvenMinutes(serverPerMonth: number, feePerMinute: number): number {
  return serverPerMonth / feePerMinute;
}

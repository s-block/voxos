import { describe, expect, it } from "vitest";
import { phoneProviders } from "../../data/voice-costs";
import { breakEvenMinutes, openAiAudioPerMinute, selfHostedCost } from "./voice-cost";

describe("selfHostedCost", () => {
  it("adds inbound minutes, streaming, the number, the voice model and the server", () => {
    const cost = selfHostedCost(
      { minutesPerMonth: 1000, outboundShare: 0, phoneNumbers: 1, voicePerMinute: 0.08, serverPerMonth: 40 },
      phoneProviders.twilio,
    );
    expect(cost.phone).toBeCloseTo(1000 * (0.0085 + 0.0044) + 1.15);
    expect(cost.voice).toBeCloseTo(80);
    expect(cost.total).toBeCloseTo(cost.phone + 80 + 40);
  });

  it("weights phone minutes by the outbound share", () => {
    const cost = selfHostedCost(
      { minutesPerMonth: 100, outboundShare: 0.5, phoneNumbers: 0, voicePerMinute: 0, serverPerMonth: 0 },
      phoneProviders.telnyx,
    );
    expect(cost.total).toBeCloseTo(100 * ((0.0052 + 0.007) / 2 + 0.0035));
  });
});

describe("breakEvenMinutes", () => {
  it("is the server bill divided by the fee", () => {
    expect(breakEvenMinutes(40, 0.05)).toBeCloseTo(800);
    expect(breakEvenMinutes(0, 0.05)).toBe(0);
  });
});

describe("openAiAudioPerMinute", () => {
  it("prices 600 caller and 1,200 agent audio tokens a minute", () => {
    expect(openAiAudioPerMinute(10, 20)).toBeCloseTo(0.03);
    expect(openAiAudioPerMinute(32, 64)).toBeCloseTo(0.096);
  });
});

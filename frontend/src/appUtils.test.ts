import { describe, expect, it } from "vitest";
import type { ActiveConnectionSettings } from "./appTypes";
import {
  agentAudioStatusMessage,
  buildLiveKitSessionOptions,
} from "./appUtils";

describe("buildLiveKitSessionOptions", () => {
  it("omits undefined metadata fields for endpoint token sources", () => {
    const options = buildLiveKitSessionOptions({
      ...baseSettings,
      mode: "endpoint",
      userId: "",
    });

    expect(options).toMatchObject({
      agentConnectTimeoutMilliseconds: 45_000,
      agentName: "voxos-voice-agent",
      participantIdentity: "tester-123",
      participantName: "tester-123",
      roomName: "voice-room",
    });
    expect("agentMetadata" in options).toBe(false);
    expect("participantAttributes" in options).toBe(false);
    expect("participantMetadata" in options).toBe(false);
  });

  it("includes user metadata for non-endpoint token sources", () => {
    const options = buildLiveKitSessionOptions({
      ...baseSettings,
      mode: "manual",
      userId: " local-user-123 ",
    });

    expect(options.agentMetadata).toBe('{"user_id":"local-user-123"}');
    expect(options.participantAttributes).toEqual({
      voxos_user_id: "local-user-123",
    });
    expect(options.participantMetadata).toBe('{"user_id":"local-user-123"}');
  });

  it("passes a current location through endpoint participant metadata", () => {
    const currentLocation = {
      latitude: 42.4304,
      longitude: 19.2594,
      horizontal_accuracy_m: 25,
      captured_at: "2026-08-30T12:00:00.000Z",
    };
    const options = buildLiveKitSessionOptions({
      ...baseSettings,
      currentLocation,
    });

    expect(options.agentMetadata).toBeUndefined();
    expect(JSON.parse(options.participantMetadata ?? "{}")).toEqual({
      voxos_location: currentLocation,
    });
  });
});

describe("agentAudioStatusMessage", () => {
  it("distinguishes pending worker dispatch from missing audio output", () => {
    expect(agentAudioStatusMessage("pre-connect-buffering", false)).toBe(
      "Waiting for LiveKit worker dispatch.",
    );
    expect(agentAudioStatusMessage("speaking", false)).toBe(
      "Agent connected; waiting for audio output.",
    );
    expect(agentAudioStatusMessage("speaking", true)).toBeNull();
  });
});

const baseSettings: ActiveConnectionSettings = {
  agentName: " voxos-voice-agent ",
  firebaseIdToken: "firebase-token",
  livekitStreamingEnabled: true,
  livekitUrl: "wss://livekit.example.com",
  mode: "endpoint",
  participantIdentity: " tester-123 ",
  participantToken: "participant-token",
  roomName: " voice-room ",
  sandboxId: "sandbox-id",
  startMicrophone: false,
  tokenEndpoint: "http://localhost:7100/api/livekit/token",
  userId: "local-user-123",
  currentLocation: null,
};

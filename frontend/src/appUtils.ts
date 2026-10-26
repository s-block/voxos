import {
  TokenSource,
  type ConnectionState,
  type TokenSourceConfigurable,
  type TokenSourceResponseObject,
} from "livekit-client";
import type {
  ActiveConnectionSettings,
  ConnectionSettings,
} from "./appTypes";
import type {
  LiveKitSettings,
  TextSettings,
  VoiceProvider,
  VoiceProviderSettings,
  VoiceProviderSummary,
  VoiceSettings,
} from "./api";
import { type AgentLocationSnapshot } from "./location";

export function createId(prefix: string): string {
  const suffix = crypto.randomUUID().slice(0, 8);
  return `${prefix}-${suffix}`;
}

export function buildTokenSource(
  settings: ActiveConnectionSettings,
): TokenSourceConfigurable {
  if (settings.mode === "sandbox") {
    return TokenSource.sandboxTokenServer(settings.sandboxId.trim());
  }

  if (settings.mode === "manual") {
    return TokenSource.custom(async (): Promise<TokenSourceResponseObject> => ({
      serverUrl: settings.livekitUrl.trim(),
      participantToken: settings.participantToken.trim(),
    }));
  }

  return TokenSource.endpoint(settings.tokenEndpoint.trim(), {
    headers:
      settings.firebaseIdToken === null
        ? undefined
        : { Authorization: `Bearer ${settings.firebaseIdToken}` },
  });
}

export function metadataForUser(
  userId: string,
  currentLocation: AgentLocationSnapshot | null = null,
): string {
  return JSON.stringify({
    user_id: userId.trim(),
    ...(currentLocation === null ? {} : { voxos_location: currentLocation }),
  });
}

export type LiveKitSessionOptions = {
  agentConnectTimeoutMilliseconds: number;
  agentMetadata?: string;
  agentName?: string;
  participantAttributes?: Record<string, string>;
  participantIdentity: string;
  participantMetadata?: string;
  participantName: string;
  roomName: string;
};

export function buildLiveKitSessionOptions(
  settings: ActiveConnectionSettings,
): LiveKitSessionOptions {
  const userMetadata =
    settings.mode === "endpoint"
      ? settings.currentLocation === null
        ? undefined
        : JSON.stringify({ voxos_location: settings.currentLocation })
      : metadataForUser(settings.userId, settings.currentLocation);
  const participantAttributes =
    settings.mode === "endpoint"
      ? undefined
      : {
          voxos_user_id: settings.userId.trim(),
        };
  const agentName = settings.agentName.trim();

  return {
    agentConnectTimeoutMilliseconds: 45_000,
    ...(agentName === "" ? {} : { agentName }),
    ...(userMetadata === undefined
      ? {}
      : {
          ...(settings.mode === "endpoint" ? {} : { agentMetadata: userMetadata }),
          participantMetadata: userMetadata,
        }),
    ...(participantAttributes === undefined ? {} : { participantAttributes }),
    participantIdentity: settings.participantIdentity.trim(),
    participantName: settings.participantIdentity.trim(),
    roomName: settings.roomName.trim(),
  };
}

export function validateSettings(settings: ConnectionSettings): string | null {
  if (!settings.livekitStreamingEnabled) {
    return "Turn on LiveKit streaming before starting voice.";
  }

  if (!settings.roomName.trim()) {
    return "Room name is required.";
  }

  if (!settings.participantIdentity.trim()) {
    return "Participant identity is required.";
  }

  if (settings.mode !== "endpoint" && !settings.userId.trim()) {
    return "VoxOS user id is required.";
  }

  if (settings.mode === "endpoint" && !settings.tokenEndpoint.trim()) {
    return "Token endpoint URL is required.";
  }

  if (settings.mode === "sandbox" && !settings.sandboxId.trim()) {
    return "LiveKit sandbox token server ID is required.";
  }

  if (settings.mode === "manual") {
    if (!settings.livekitUrl.trim()) {
      return "LiveKit server URL is required for manual token mode.";
    }

    if (!settings.participantToken.trim()) {
      return "Participant token is required for manual token mode.";
    }
  }

  return null;
}

export function displayConnectionState(state: ConnectionState): string {
  return state.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}

export function authErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }

  return String(error);
}

export function activeProviderSummary(
  providerSettings: VoiceProviderSettings | null,
  voiceSettings: VoiceSettings | null,
): VoiceProviderSummary | null {
  if (providerSettings === null) {
    return null;
  }

  const activeProvider = voiceSettings?.provider ?? providerSettings.active_provider;
  return (
    providerSettings.providers.find(
      (provider) => provider.provider === activeProvider,
    ) ?? null
  );
}

export function providerLabel(
  providerSettings: VoiceProviderSettings,
  provider: VoiceProvider,
): string {
  return (
    providerSettings.providers.find((summary) => summary.provider === provider)
      ?.label ?? provider
  );
}

export function agentConfigKey(settings: VoiceSettings | null): string {
  if (settings === null) {
    return "default-agent-config";
  }

  return [
    settings.configured ? "configured" : "unconfigured",
    settings.provider,
    settings.model,
    settings.voice,
    settings.preferred_language,
    settings.agent_instructions,
    settings.initial_reply_instructions ?? "",
  ].join("|");
}

export function textConfigKey(settings: TextSettings | null): string {
  if (settings === null) {
    return "default-text-config";
  }

  return [
    settings.configured ? "configured" : "unconfigured",
    settings.provider,
    settings.model,
    settings.preferred_language,
    settings.agent_instructions,
    settings.temperature ?? "",
    settings.allowed_subagent_models.join(","),
    settings.subagent_model,
    settings.image_generation_provider,
    settings.image_generation_model,
  ].join("|");
}

export function liveKitSettingsKey(settings: LiveKitSettings | null): string {
  if (settings === null) {
    return "default-livekit-settings";
  }

  return [
    settings.configured ? "configured" : "unconfigured",
    settings.livekit_url ?? "",
  ].join("|");
}

export function sessionReadinessLabel(
  streamingEnabled: boolean,
  needsProviderKey: boolean,
  needsProviderAdapter: boolean,
  settingsError: string | null,
  isConnected: boolean,
  isConnecting: boolean,
): { kind: "ready" | "warning" | "neutral"; label: string } {
  if (isConnected) {
    return { kind: "ready", label: "Connected" };
  }
  if (isConnecting) {
    return { kind: "warning", label: "Connecting" };
  }
  if (!streamingEnabled) {
    return { kind: "neutral", label: "Streaming off" };
  }
  if (needsProviderKey) {
    return { kind: "warning", label: "Provider key required" };
  }
  if (needsProviderAdapter) {
    return { kind: "warning", label: "Adapter needed" };
  }
  if (settingsError !== null) {
    return { kind: "warning", label: "Setup needed" };
  }
  return { kind: "ready", label: "Ready" };
}

export function agentAudioStatusMessage(
  agentState: string,
  hasMicrophoneTrack: boolean,
): string | null {
  if (hasMicrophoneTrack) {
    return null;
  }

  if (agentState === "pre-connect-buffering") {
    return "Waiting for LiveKit worker dispatch.";
  }

  if (agentState === "failed") {
    return "Agent connection failed.";
  }

  if (agentState === "connecting") {
    return "Connecting agent audio.";
  }

  if (
    agentState === "listening" ||
    agentState === "thinking" ||
    agentState === "speaking"
  ) {
    return "Agent connected; waiting for audio output.";
  }

  return "No agent audio track yet.";
}

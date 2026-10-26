import type {
  Agent,
  TextProvider,
  ToolGrant,
  ToolGrantUpdate,
  VoiceProvider,
} from "./api";
import type { AgentLocationSnapshot } from "./location";

export type ConnectionMode = "endpoint" | "sandbox" | "manual";
export type AuthFormMode = "signin" | "create";
export type AppPage =
  | "chat"
  | "voice"
  | "agent"
  | "knowledge"
  | "operations"
  | "codex-cloud"
  | "providers"
  | "integrations"
  | "contacts"
  | "connection"
  | "tools"
  | "mcp"
  | "browser-accounts"
  | "account";

export type ConnectionSettings = {
  mode: ConnectionMode;
  tokenEndpoint: string;
  sandboxId: string;
  livekitUrl: string;
  participantToken: string;
  agentName: string;
  roomName: string;
  participantIdentity: string;
  userId: string;
  livekitStreamingEnabled: boolean;
  startMicrophone: boolean;
};

export type ActiveConnectionSettings = ConnectionSettings & {
  firebaseIdToken: string | null;
  currentLocation: AgentLocationSnapshot | null;
};

export type LogEntry = {
  id: string;
  level: "info" | "error";
  message: string;
};

export type VoiceProviderKeyFormValues = {
  apiKey: string;
  provider: VoiceProvider;
};

export type TextProviderKeyFormValues = {
  apiKey: string;
  provider: TextProvider;
};

export type OpenAICompatibleProviderFormValues = {
  apiKey: string;
  baseUrl: string;
  models: string[];
};

export type VoicePreferencesFormValues = {
  model: string;
  provider: VoiceProvider;
  voice: string;
  preferredLanguage: string;
  agentInstructions: string;
  initialReplyInstructions: string;
};

export type TextPreferencesFormValues = {
  model: string;
  provider: TextProvider;
  preferredLanguage: string;
  agentInstructions: string;
  temperature: number | null;
  allowedSubagentModels: string[];
  subagentModel: string;
};

export type ImageGenerationPreferencesFormValues = {
  model: string;
  provider: TextProvider;
};

export type LiveKitSettingsFormValues = {
  livekitUrl: string;
  livekitApiKey: string;
  livekitApiSecret: string;
};

export type AgentIdentityFormValues = {
  displayName: string;
  description: string;
};

export type ContactFormValues = {
  displayName: string;
  phoneNumber: string;
  email: string;
  notes: string;
  isAdmin: boolean;
};

export type ToolGrantState = {
  agent: Agent | null;
  grants: ToolGrant[];
  isLoading: boolean;
  isSaving: boolean;
  error: string | null;
  setGrant: (grant: ToolGrantUpdate) => Promise<void>;
};

export type ConnectionSettingsUpdater = <
  Key extends keyof ConnectionSettings,
>(
  key: Key,
  value: ConnectionSettings[Key],
) => void;

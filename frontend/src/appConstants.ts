import {
  Brain,
  Bot,
  Cable,
  CloudCog,
  ActivitySquare,
  ContactRound,
  GlobeLock,
  KeyRound,
  MessageSquareText,
  Mic2,
  PhoneCall,
  ServerCog,
  UserRound,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { AppPage, ConnectionMode } from "./appTypes";

export const DEFAULT_AGENT_NAME = "voxos-voice-agent";
export const DEFAULT_MODEL = "gpt-realtime-2.1-mini";
export const DEFAULT_VOICE = "marin";
export const DEFAULT_PREFERRED_LANGUAGE = "English";
export const DEFAULT_AGENT_INSTRUCTIONS = "You are a helpful voice assistant.";
export const DEFAULT_INITIAL_REPLY = "Greet the user and offer your assistance.";
export const DEFAULT_TEXT_MODEL = "gpt-5.4-mini";
export const DEFAULT_TEXT_PREFERRED_LANGUAGE = "English";
export const DEFAULT_TEXT_AGENT_INSTRUCTIONS = "You are a helpful assistant.";

export const modeLabels: Record<ConnectionMode, string> = {
  endpoint: "Token endpoint",
  sandbox: "Sandbox server",
  manual: "Manual token",
};

export type AppPageDefinition = {
  page: AppPage;
  label: string;
  description: string;
  icon: LucideIcon;
};

export const appPages: readonly AppPageDefinition[] = [
  {
    page: "chat",
    label: "Chat",
    description: "Stream text with tools and status events.",
    icon: MessageSquareText,
  },
  {
    page: "voice",
    label: "Voice",
    description: "Talk with the agent.",
    icon: Mic2,
  },
  {
    page: "agent",
    label: "Agent",
    description: "Model, voice, and instructions.",
    icon: Bot,
  },
  {
    page: "knowledge",
    label: "Knowledge",
    description: "Memories, skills, and task plans.",
    icon: Brain,
  },
  {
    page: "operations",
    label: "Operations",
    description: "Runs, schedules, and live events.",
    icon: ActivitySquare,
  },
  {
    page: "codex-cloud",
    label: "Codex Cloud",
    description: "Run durable cloud coding tasks with your ChatGPT subscription.",
    icon: CloudCog,
  },
  {
    page: "providers",
    label: "Providers",
    description: "Voice credentials and active provider.",
    icon: KeyRound,
  },
  {
    page: "integrations",
    label: "Phone",
    description: "Phone numbers and provider routing.",
    icon: PhoneCall,
  },
  {
    page: "contacts",
    label: "Contacts",
    description: "People, phone numbers, and caller access.",
    icon: ContactRound,
  },
  {
    page: "connection",
    label: "Connection",
    description: "LiveKit and token routing.",
    icon: Cable,
  },
  {
    page: "tools",
    label: "Tools",
    description: "Internal tool approvals.",
    icon: Wrench,
  },
  {
    page: "mcp",
    label: "MCP",
    description: "Remote MCP servers and tool discovery.",
    icon: ServerCog,
  },
  {
    page: "browser-accounts",
    label: "Browser Accounts",
    description: "Signed-in browser profiles and agent access.",
    icon: GlobeLock,
  },
  {
    page: "account",
    label: "Account",
    description: "Signed-in user and session access.",
    icon: UserRound,
  },
] as const;

export const pageLabels: Record<AppPage, string> = {
  chat: "Chat",
  voice: "Voice",
  agent: "Agent",
  knowledge: "Knowledge",
  operations: "Operations",
  "codex-cloud": "Codex Cloud",
  providers: "Providers",
  integrations: "Phone",
  contacts: "Contacts",
  connection: "Connection",
  tools: "Tools",
  mcp: "MCP",
  "browser-accounts": "Browser Accounts",
  account: "Account",
};

export const pageDescriptions: Record<AppPage, string> = {
  chat: "Streaming text chat, tool activity, provider selection, and history.",
  voice: "Live voice session, transcript, text fallback, and recent events.",
  agent: "Tune the model, voice, language, and instructions.",
  knowledge: "Manage durable memories, user skills, and task plans.",
  operations: "Active runs, schedules, live events, and execution history.",
  "codex-cloud":
    "Connect ChatGPT once, choose a cloud environment, and queue isolated coding tasks.",
  providers: "Save text and voice provider keys.",
  integrations: "Connect phone providers, numbers, and WhatsApp messaging.",
  contacts: "Match callers and choose who receives configured agent access.",
  connection: "Configure LiveKit credentials and token routing.",
  tools: "Review and approve built-in agent tools.",
  mcp: "Connect MCP servers and approve discovered remote tools.",
  "browser-accounts":
    "Create signed-in browser profiles and control which agents may request them.",
  account: "Review the signed-in Firebase user and sign out.",
};

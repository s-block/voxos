import {
  siBrave,
  siDocker,
  siDuckduckgo,
  siFirebase,
  siGithub,
  siGmail,
  siGooglecalendar,
  siGooglecloud,
  siGoogledrive,
  siGooglegemini,
  siHelm,
  siKubernetes,
  siLinear,
  siLivekit,
  siLmstudio,
  siMonzo,
  siNotion,
  siNvidia,
  siOllama,
  siPostgresql,
  siSentry,
  siVllm,
  siWhatsapp,
  type SimpleIcon,
} from "simple-icons";

export interface Integration {
  readonly name: string;
  /** Left out for brands whose logos the simple-icons package does not publish. */
  readonly icon?: SimpleIcon;
  readonly href: string;
}

export interface IntegrationGroup {
  readonly title: string;
  readonly summary: string;
  readonly items: readonly Integration[];
}

const voiceProviders = "/docs/voice-and-livekit/#runtime-supported-voice-providers";
const catalog = (anchor: string): string => `/docs/mcp-service-catalog/#${anchor}`;

/** Services Voxos works with today. Each links to the docs that cover its setup. */
export const integrationGroups: readonly IntegrationGroup[] = [
  {
    title: "Models",
    summary: "Realtime voice and text models, hosted or on your own hardware.",
    items: [
      { name: "OpenAI", href: voiceProviders },
      { name: "Google Gemini", icon: siGooglegemini, href: voiceProviders },
      { name: "xAI Grok", href: voiceProviders },
      { name: "NVIDIA API Catalog", icon: siNvidia, href: "/docs/chat-and-text/#supported-text-providers" },
      { name: "Ollama", icon: siOllama, href: "/docs/self-hosted-models/#ollama-recommended-cross-platform-start" },
      { name: "LM Studio", icon: siLmstudio, href: "/docs/self-hosted-models/#lm-studio-desktop-alternative" },
      { name: "vLLM", icon: siVllm, href: "/docs/self-hosted-models/#vllm-linux-gpu-serving" },
      { name: "llama.cpp", href: "/docs/self-hosted-models/#llamacpp-lightweight-manual-serving" },
    ],
  },
  {
    title: "Calls and messaging",
    summary: "Real phone numbers, realtime media and the chat app everyone already has.",
    items: [
      { name: "LiveKit", icon: siLivekit, href: "/docs/voice-and-livekit/#livekit-settings" },
      { name: "Twilio", href: "/docs/phone-integrations/#twilio-setup" },
      { name: "Telnyx", href: "/docs/phone-integrations/#telnyx-setup" },
      { name: "WhatsApp Business", icon: siWhatsapp, href: "/docs/phone-integrations/#whatsapp-linked-device" },
    ],
  },
  {
    title: "MCP catalog",
    summary: "Tools your agent can pick up, each with setup notes.",
    items: [
      { name: "GitHub", icon: siGithub, href: catalog("github-mcp") },
      { name: "Linear", icon: siLinear, href: catalog("linear-mcp") },
      { name: "Notion", icon: siNotion, href: catalog("notion-mcp") },
      { name: "Sentry", icon: siSentry, href: catalog("sentry-mcp") },
      { name: "Gmail", icon: siGmail, href: catalog("google-workspace") },
      { name: "Google Drive", icon: siGoogledrive, href: catalog("google-workspace") },
      { name: "Google Calendar", icon: siGooglecalendar, href: catalog("google-workspace") },
      { name: "Exa", href: catalog("exa") },
      { name: "Tavily", href: catalog("tavily") },
      { name: "Firecrawl", href: catalog("firecrawl") },
      { name: "Brave Search", icon: siBrave, href: catalog("brave-search-mcp") },
      { name: "DuckDuckGo", icon: siDuckduckgo, href: catalog("duckduckgo-mcp") },
      { name: "Browserless", href: catalog("browserless") },
      { name: "Browserbase", href: catalog("browserbase-mcp") },
      { name: "Browser Use", href: catalog("browser-use-cloud-mcp") },
      { name: "Playwright", href: catalog("playwright-mcp") },
      { name: "Apify", href: catalog("apify-mcp") },
      { name: "MotherDuck", href: catalog("motherduck-mcp") },
      { name: "Monzo", icon: siMonzo, href: catalog("monzo") },
      { name: "Flightradar24", href: catalog("flightradar24") },
      { name: "OpenBnB", href: catalog("openbnb") },
    ],
  },
  {
    title: "Runs on",
    summary: "Boring, dependable infrastructure you control.",
    items: [
      { name: "PostgreSQL + pgvector", icon: siPostgresql, href: "/docs/architecture-and-security/" },
      { name: "Docker Compose", icon: siDocker, href: "/docs/quick-start/#docker-compose-stack" },
      { name: "Kubernetes", icon: siKubernetes, href: "/docs/helm/" },
      { name: "Helm", icon: siHelm, href: "/docs/helm/" },
      { name: "Firebase Auth", icon: siFirebase, href: "/docs/account-and-auth/" },
      { name: "AWS KMS", href: "/docs/architecture-and-security/#secret-vault" },
      { name: "Google Cloud KMS", icon: siGooglecloud, href: "/docs/architecture-and-security/#secret-vault" },
    ],
  },
];

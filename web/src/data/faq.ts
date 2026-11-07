import type { FaqEntry } from "../seo/structured-data";
import { githubUrls } from "../site.config";

export interface Faq extends FaqEntry {
  readonly link: { readonly href: string; readonly label: string };
}

/** Short answers for people and answer engines. Each answer must match the linked docs. */
export const faqs: readonly Faq[] = [
  {
    question: "What is Voxos?",
    answer:
      "Voxos is an open-source, self-hosted backend for voice-first AI agents. You can talk to your agent in real time, call it or have it call people, text it over SMS, chat with it on WhatsApp, and give it skills, schedules, memory and MCP tools. It is MIT licensed.",
    link: { href: "/docs/", label: "Read the docs" },
  },
  {
    question: "Is Voxos free?",
    answer:
      "Yes. The code is MIT licensed and you run it on your own machine or cluster. You pay your own providers directly for things like model APIs, phone numbers and hosting.",
    link: { href: githubUrls.license, label: "MIT License" },
  },
  {
    question: "Is Voxos an open-source alternative to Vapi or Retell?",
    answer:
      "Yes, if you want a self-hosted agent rather than a hosted call platform. Voxos is MIT licensed, charges no platform fee, and covers voice, phone calls, SMS, WhatsApp, chat, memory and MCP tools. It is not built for bulk dialing campaigns, and realtime voice still runs on OpenAI, Gemini or xAI.",
    link: { href: "/guides/open-source-vapi-alternatives/", label: "Compare the options" },
  },
  {
    question: "How much does a self-hosted voice AI agent cost?",
    answer:
      "Voxos charges no platform fee. You pay your phone provider for numbers and minutes, your voice model provider for usage, and whoever hosts your server. The calculator estimates a month from your own call volume.",
    link: { href: "/voice-agent-cost/", label: "Voice agent cost calculator" },
  },
  {
    question: "How is Voxos different from OpenClaw?",
    answer:
      "Both are MIT-licensed, self-hosted agents with phone calls, SMS and WhatsApp. Voxos is built so several signed-in users can share one deployment, with row-level security and per-user encrypted keys. OpenClaw's security guide recommends separate gateways for people who do not trust each other.",
    link: { href: "/guides/voxos-vs-openclaw/", label: "Voxos vs OpenClaw" },
  },
  {
    question: "Can Voxos answer and make phone calls?",
    answer:
      "Yes. Connect a Twilio or Telnyx number and inbound calls reach your agent through LiveKit. Agents can also call your saved contacts with a stated goal, and caller access is set per contact.",
    link: { href: "/docs/phone-integrations/", label: "Phone integrations" },
  },
  {
    question: "Does Voxos work with WhatsApp?",
    answer:
      "Yes. Voxos links a WhatsApp Business number as a companion device, so your agent can receive direct and group text messages and reply in the chat. Each user can link one number.",
    link: { href: "/docs/phone-integrations/#whatsapp-linked-device", label: "WhatsApp setup" },
  },
  {
    question: "Which AI models does Voxos support?",
    answer:
      "Realtime voice runs on OpenAI, Google Gemini and xAI Grok voice models. Text chat supports OpenAI, xAI, the NVIDIA API Catalog and any OpenAI-compatible endpoint.",
    link: { href: "/docs/providers-and-keys/", label: "Providers and keys" },
  },
  {
    question: "Can I use local or self-hosted models?",
    answer:
      "Yes, for text chat. Point Voxos at any OpenAI-compatible endpoint, such as Ollama, LM Studio, vLLM or llama.cpp, on your machine or your network.",
    link: { href: "/docs/self-hosted-models/", label: "Self-hosted models" },
  },
  {
    question: "How does Voxos use MCP?",
    answer:
      "The Model Context Protocol lets an agent use external tools. Voxos connects to remote MCP servers over Streamable HTTP or SSE, handles OAuth and API keys, and only gives an agent the tools you explicitly allow.",
    link: { href: "/docs/mcp-and-tools/", label: "MCP and tools" },
  },
  {
    question: "How does Voxos keep keys and data safe?",
    answer:
      "Provider keys and tokens are encrypted per user, with AES-GCM for local development and AWS or Google Cloud KMS envelope encryption for deployments. Postgres row-level security keeps each user's data separate, and agents only use the tools they have been granted.",
    link: { href: "/docs/architecture-and-security/", label: "Architecture and security" },
  },
  {
    question: "What do I need to run Voxos?",
    answer:
      "Docker with Docker Compose, a Firebase project for sign-in, and API keys for the providers you want to use. Running make up starts the API, LiveKit, Postgres with pgvector and the workers. A Helm chart covers Kubernetes.",
    link: { href: "/docs/quick-start/", label: "Quick Start" },
  },
  {
    question: "Is there a mobile app?",
    answer:
      "Yes. A small Flutter app for iPhone and Android gives you chat and one-tap voice calls with your agent. It connects to your own Voxos server.",
    link: { href: "/docs/mobile-app/", label: "Mobile app" },
  },
];

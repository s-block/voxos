import { Brain, CalendarClock, Cpu, GitFork, Globe, Plug, Sparkles, Webhook } from "@lucide/astro";

export interface Feature {
  readonly name: string;
  readonly title: string;
  readonly summary: string;
  readonly href: string;
  readonly icon: typeof Sparkles;
}

/** What an agent can do beyond conversation. Claims here must match the linked docs. */
export const features: readonly Feature[] = [
  {
    name: "Skills",
    title: "Teach it a routine once",
    summary:
      "Skills are reusable playbooks your agent finds and follows. Edits it suggests wait for your approval.",
    href: "/docs/agent-knowledge-and-persistence/#skills",
    icon: Sparkles,
  },
  {
    name: "Scheduled tasks",
    title: "Put chores on a schedule",
    summary:
      "One-off or repeating schedules that post results back to their chat. Ask for a reminder in chat and it sets one up.",
    href: "/docs/operations-runs-and-schedules/#schedules",
    icon: CalendarClock,
  },
  {
    name: "MCP tools",
    title: "Plug in any MCP server",
    summary:
      "Remote MCP over Streamable HTTP or SSE, with OAuth, bearer or API-key auth. You pick exactly which tools each agent gets.",
    href: "/docs/mcp-and-tools/#remote-mcp-servers",
    icon: Plug,
  },
  {
    name: "Browser tasks",
    title: "Let it drive a browser",
    summary:
      "A built-in local browser, Browserless or Browser Use, with website logins kept encrypted in your vault.",
    href: "/docs/browser-accounts/",
    icon: Globe,
  },
  {
    name: "Memory",
    title: "It remembers stuff",
    summary:
      "Durable memories, persona rules and task plans, plus suggested improvements you review before they stick.",
    href: "/docs/agent-knowledge-and-persistence/#memories",
    icon: Brain,
  },
  {
    name: "Subagents",
    title: "It delegates the busywork",
    summary:
      "Agents can hand work to subagents, and a subagent never gets more tool access than its parent.",
    href: "/docs/agent-knowledge-and-persistence/#subagents",
    icon: GitFork,
  },
  {
    name: "Webhooks",
    title: "Start work from anywhere",
    summary:
      "Agent webhooks start a durable run from any service that can send an authenticated HTTP request.",
    href: "/docs/operations-runs-and-schedules/#agent-webhooks",
    icon: Webhook,
  },
  {
    name: "Local models",
    title: "Bring your own model",
    summary:
      "Point text chat at any OpenAI-compatible endpoint, including Ollama, LM Studio, vLLM and llama.cpp.",
    href: "/docs/self-hosted-models/",
    icon: Cpu,
  },
];

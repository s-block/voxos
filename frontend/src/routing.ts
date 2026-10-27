import { appPages } from "./appConstants";
import type { AppPage } from "./appTypes";

export const DEFAULT_APP_PAGE: AppPage = "chat";

export const pagePaths = {
  chat: "/chat",
  voice: "/voice",
  agent: "/agent",
  knowledge: "/knowledge",
  operations: "/operations",
  "codex-cloud": "/codex-cloud",
  providers: "/providers",
  integrations: "/integrations",
  contacts: "/contacts",
  connection: "/connection",
  tools: "/tools",
  mcp: "/mcp",
  "browser-accounts": "/browser-accounts",
  account: "/account",
} as const satisfies Record<AppPage, `/${string}`>;

export type PagePath = (typeof pagePaths)[AppPage];
export type KnowledgeTab = "memories" | "skills" | "tasks" | "improvements";
export type OperationsTab = "active" | "scheduled" | "webhooks" | "history" | "events";

export const DEFAULT_KNOWLEDGE_TAB: KnowledgeTab = "memories";
export const DEFAULT_OPERATIONS_TAB: OperationsTab = "active";

export const knowledgeTabPaths = {
  memories: pagePaths.knowledge,
  skills: `${pagePaths.knowledge}/skills`,
  tasks: `${pagePaths.knowledge}/tasks`,
  improvements: `${pagePaths.knowledge}/improvements`,
} as const satisfies Record<KnowledgeTab, `/${string}`>;

export const operationsTabPaths = {
  active: pagePaths.operations,
  scheduled: `${pagePaths.operations}/scheduled`,
  webhooks: `${pagePaths.operations}/webhooks`,
  history: `${pagePaths.operations}/history`,
  events: `${pagePaths.operations}/events`,
} as const satisfies Record<OperationsTab, `/${string}`>;

type ParsedRouteSegment<TTab extends string> = {
  canonicalPath: string;
  shouldRedirect: boolean;
  tab: TTab;
};

const appPageSet: ReadonlySet<string> = new Set(
  appPages.map((page) => page.page),
);
const knowledgeTabSet: ReadonlySet<string> = new Set([
  "memories",
  "skills",
  "tasks",
  "improvements",
]);
const operationsTabSet: ReadonlySet<string> = new Set([
  "active",
  "scheduled",
  "webhooks",
  "history",
  "events",
]);

export function pagePath(page: AppPage): PagePath {
  return pagePaths[page];
}

export function pageFromPathname(pathname: string): AppPage {
  const [segment] = pathname.split("/").filter(Boolean);
  return segment !== undefined && isAppPage(segment) ? segment : DEFAULT_APP_PAGE;
}

export function hasNestedPageRoutes(page: AppPage): boolean {
  return page === "knowledge" || page === "operations";
}

export function parseKnowledgePath(
  splat: string | undefined,
): ParsedRouteSegment<KnowledgeTab> | null {
  const segment = normalizeSplat(splat);

  if (segment === "") {
    return {
      canonicalPath: knowledgeTabPaths.memories,
      shouldRedirect: false,
      tab: DEFAULT_KNOWLEDGE_TAB,
    };
  }

  if (segment === "memories") {
    return {
      canonicalPath: knowledgeTabPaths.memories,
      shouldRedirect: true,
      tab: DEFAULT_KNOWLEDGE_TAB,
    };
  }

  if (isKnowledgeTab(segment)) {
    return {
      canonicalPath: knowledgeTabPaths[segment],
      shouldRedirect: false,
      tab: segment,
    };
  }

  return null;
}

export function parseOperationsPath(
  splat: string | undefined,
): ParsedRouteSegment<OperationsTab> | null {
  const segment = normalizeSplat(splat);

  if (segment === "") {
    return {
      canonicalPath: operationsTabPaths.active,
      shouldRedirect: false,
      tab: DEFAULT_OPERATIONS_TAB,
    };
  }

  if (segment === "active") {
    return {
      canonicalPath: operationsTabPaths.active,
      shouldRedirect: true,
      tab: DEFAULT_OPERATIONS_TAB,
    };
  }

  if (isOperationsTab(segment)) {
    return {
      canonicalPath: operationsTabPaths[segment],
      shouldRedirect: false,
      tab: segment,
    };
  }

  return null;
}

function isAppPage(value: string): value is AppPage {
  return appPageSet.has(value);
}

function isKnowledgeTab(value: string): value is KnowledgeTab {
  return knowledgeTabSet.has(value);
}

function isOperationsTab(value: string): value is OperationsTab {
  return operationsTabSet.has(value);
}

function normalizeSplat(splat: string | undefined): string {
  if (splat === undefined) {
    return "";
  }

  const trimmed = splat.replace(/^\/+|\/+$/g, "");
  return trimmed.includes("/") ? trimmed : trimmed.toLowerCase();
}

import { describe, expect, it } from "vitest";
import { appPages } from "./appConstants";
import {
  knowledgeTabPaths,
  operationsTabPaths,
  pageFromPathname,
  pagePath,
  pagePaths,
  parseKnowledgePath,
  parseOperationsPath,
} from "./routing";

describe("routing metadata", () => {
  it("maps every app page to one canonical path", () => {
    const pageNames = appPages.map((page) => page.page).sort();
    const pathPageNames = Object.keys(pagePaths).sort();

    expect(pathPageNames).toEqual(pageNames);
    expect(new Set(Object.values(pagePaths)).size).toBe(appPages.length);

    for (const page of appPages) {
      expect(pagePath(page.page)).toMatch(/^\/[a-z]+(?:-[a-z]+)*$/);
    }
  });

  it("derives the active app page from top-level URL segments", () => {
    expect(pageFromPathname("/")).toBe("chat");
    expect(pageFromPathname("/chat")).toBe("chat");
    expect(pageFromPathname("/knowledge/skills")).toBe("knowledge");
    expect(pageFromPathname("/operations/history")).toBe("operations");
    expect(pageFromPathname("/contacts")).toBe("contacts");
    expect(pageFromPathname("/missing")).toBe("chat");
  });

  it("parses knowledge tab paths with canonical redirects", () => {
    expect(parseKnowledgePath(undefined)).toEqual({
      canonicalPath: knowledgeTabPaths.memories,
      shouldRedirect: false,
      tab: "memories",
    });
    expect(parseKnowledgePath("memories")).toEqual({
      canonicalPath: knowledgeTabPaths.memories,
      shouldRedirect: true,
      tab: "memories",
    });
    expect(parseKnowledgePath("skills")).toEqual({
      canonicalPath: knowledgeTabPaths.skills,
      shouldRedirect: false,
      tab: "skills",
    });
    expect(parseKnowledgePath("unknown")).toBeNull();
  });

  it("parses operations tab paths with canonical redirects", () => {
    expect(parseOperationsPath(undefined)).toEqual({
      canonicalPath: operationsTabPaths.active,
      shouldRedirect: false,
      tab: "active",
    });
    expect(parseOperationsPath("active")).toEqual({
      canonicalPath: operationsTabPaths.active,
      shouldRedirect: true,
      tab: "active",
    });
    expect(parseOperationsPath("events")).toEqual({
      canonicalPath: operationsTabPaths.events,
      shouldRedirect: false,
      tab: "events",
    });
    expect(parseOperationsPath("webhooks")).toEqual({
      canonicalPath: operationsTabPaths.webhooks,
      shouldRedirect: false,
      tab: "webhooks",
    });
    expect(parseOperationsPath("history/deep")).toBeNull();
  });
});

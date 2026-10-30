import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseRepoDoc } from "./parse";
import { repoFileUrl } from "./repo";
import { parseSkillsCatalog } from "./skills-catalog";
import { buildDocsSite, GUIDES_INDEX, WIKI_SIDEBAR } from "./sources";
import { parseWikiSidebar } from "./wiki-sidebar";

const docs = buildDocsSite();
const read = (repoPath: string): string => readFileSync(repoFileUrl(repoPath), "utf8");

describe("buildDocsSite against this repository", () => {
  it("publishes every wiki sidebar link, with Home as the docs landing page", () => {
    const wikiLinks = parseWikiSidebar(read(WIKI_SIDEBAR)).flatMap((group) => group.links);

    expect(wikiLinks.length).toBeGreaterThan(0);
    expect(docs.routes.get("docs/wiki/Home.md")).toBe("/docs/");
    expect(docs.routes.get("docs/wiki/Quick-Start.md")).toBe("/docs/quick-start/");
    expect(docs.routes.get("docs/user-mcp-servers.md")).toBe("/docs/user-mcp-servers/");
    expect(docs.sidebar.slice(0, 2)).toEqual([
      { label: "Home", slug: "docs" },
      { label: "Quick Start", slug: "docs/quick-start" },
    ]);
  });

  it("publishes the guides index at /guides/ and each guide it lists below it", () => {
    const guideLinks = parseWikiSidebar(read(GUIDES_INDEX), GUIDES_INDEX).flatMap((group) => group.links);
    const guidesGroup = docs.sidebar.find(
      (item): item is { label: string; items: { label: string; slug: string }[] } =>
        typeof item === "object" && item.label === "Guides",
    );

    expect(docs.routes.get(GUIDES_INDEX)).toBe("/guides/");
    expect(docs.routes.get("docs/guides/Twilio-Phone-Agent.md")).toBe("/guides/twilio-phone-agent/");
    expect(guidesGroup?.items.map((item) => item.label)).toEqual([
      "All guides",
      ...guideLinks.map((link) => link.label),
    ]);
  });

  it("publishes each catalog skill, in catalog order, with its catalog description", () => {
    const catalog = parseSkillsCatalog(read("examples/skills/catalog.toml"));
    const skillsGroup = docs.sidebar.find(
      (item): item is { label: string; items: { label: string; slug: string }[] } =>
        typeof item === "object" && item.label === "Example skills",
    );

    expect(skillsGroup?.items.slice(1).map((item) => item.label)).toEqual(catalog.map((skill) => skill.name));
    for (const skill of catalog) {
      const source = docs.sources.find((candidate) => candidate.id === `docs/skills/${skill.slug}`);
      expect(source?.description).toBe(skill.description);
    }
  });

  it("gives every published page a title and a description", () => {
    for (const source of docs.sources) {
      const doc = parseRepoDoc(read(source.repoPath), source.repoPath);
      expect(doc.title, source.repoPath).not.toBe("");
      expect((source.description ?? doc.description).length, source.repoPath).toBeGreaterThanOrEqual(50);
    }
  });

  it("covers every published folder for Starlight's Markdown processing", () => {
    expect(docs.sourceDirs).toEqual(
      expect.arrayContaining(["docs/wiki", "docs", "docs/guides", "examples/skills", "helm", "app", "."]),
    );
  });
});

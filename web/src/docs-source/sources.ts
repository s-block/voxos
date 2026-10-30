import { existsSync, readFileSync } from "node:fs";
import { posix } from "node:path";
import type { StarlightUserConfig } from "@astrojs/starlight/types";
import { repoFileUrl } from "./repo";
import { parseSkillsCatalog } from "./skills-catalog";
import { parseWikiSidebar } from "./wiki-sidebar";

type SidebarItem = NonNullable<StarlightUserConfig["sidebar"]>[number];

export interface DocSource {
  /** Repo-relative POSIX path of the Markdown file. */
  readonly repoPath: string;
  /** Starlight entry id, which is also the page path: `docs/quick-start` serves `/docs/quick-start/`. */
  readonly id: string;
  /** Replaces the description taken from the first paragraph. */
  readonly description?: string;
}

export interface DocsSite {
  readonly sources: readonly DocSource[];
  /** Site path for each published repo file, for example `docs/wiki/Home.md` → `/docs/`. */
  readonly routes: ReadonlyMap<string, string>;
  readonly sidebar: SidebarItem[];
  /** Repo-relative folders that hold published docs. */
  readonly sourceDirs: readonly string[];
  /** Files that define which pages exist and how they are grouped. */
  readonly navigationFiles: readonly string[];
}

interface ExtraPage {
  readonly label: string;
  readonly repoPath: string;
  readonly id: string;
}

export const WIKI_SIDEBAR = "docs/wiki/_Sidebar.md";
export const GUIDES_INDEX = "docs/guides/README.md";
const WIKI_HOME = "docs/wiki/Home.md";
const SKILLS_CATALOG = "examples/skills/catalog.toml";
const SKILLS_OVERVIEW = "examples/skills/README.md";

/** Repository pages published beyond the wiki sidebar and the skills catalog. */
const MORE_PAGES: readonly ExtraPage[] = [
  { label: "Kubernetes with Helm", repoPath: "helm/README.md", id: "docs/helm" },
  { label: "Mobile app", repoPath: "app/README.md", id: "docs/mobile-app" },
  { label: "Security policy", repoPath: "SECURITY.md", id: "docs/security" },
];

/**
 * Builds the published page list and the docs sidebar from the repository:
 * the wiki's `_Sidebar.md`, the guides index, the curated skills catalog, and
 * `MORE_PAGES`.
 */
export function buildDocsSite(): DocsSite {
  const sources: DocSource[] = [];
  const sidebar: SidebarItem[] = [];
  const add = (source: DocSource, label: string): SidebarItem => {
    if (!existsSync(repoFileUrl(source.repoPath))) {
      throw new Error(`Published doc ${source.repoPath} does not exist`);
    }
    sources.push(source);
    return { label, slug: source.id };
  };

  for (const group of parseWikiSidebar(readRepoText(WIKI_SIDEBAR))) {
    const items = group.links.map((link) => {
      const repoPath = linkedPage(WIKI_SIDEBAR, link.target);
      return add({ repoPath, id: wikiPageId(repoPath) }, link.label);
    });
    if (group.label) sidebar.push({ label: group.label, items });
    else sidebar.push(...items);
  }

  const guides = parseWikiSidebar(readRepoText(GUIDES_INDEX), GUIDES_INDEX).flatMap((group) => group.links);
  sidebar.push({
    label: "Guides",
    items: [
      add({ repoPath: GUIDES_INDEX, id: "guides" }, "All guides"),
      ...guides.map((link) => {
        const repoPath = linkedPage(GUIDES_INDEX, link.target);
        return add({ repoPath, id: `guides/${pageName(repoPath)}` }, link.label);
      }),
    ],
  });

  const catalogDir = posix.dirname(SKILLS_CATALOG);
  const skills = parseSkillsCatalog(readRepoText(SKILLS_CATALOG));
  sidebar.push({
    label: "Example skills",
    items: [
      add({ repoPath: SKILLS_OVERVIEW, id: "docs/skills" }, "Overview"),
      ...skills.map((skill) =>
        add(
          {
            repoPath: posix.join(catalogDir, skill.bodyPath),
            id: `docs/skills/${skill.slug}`,
            description: skill.description,
          },
          skill.name,
        ),
      ),
    ],
  });

  sidebar.push({
    label: "More",
    items: MORE_PAGES.map((page) =>
      add({ repoPath: page.repoPath, id: page.id }, page.label),
    ),
  });

  assertUnique(sources.map((source) => source.id), "page id");
  assertUnique(sources.map((source) => source.repoPath), "source file");

  return {
    sources,
    routes: new Map(sources.map((source) => [source.repoPath, `/${source.id}/`])),
    sidebar,
    sourceDirs: [...new Set(sources.map((source) => posix.dirname(source.repoPath)))],
    navigationFiles: [WIKI_SIDEBAR, GUIDES_INDEX, SKILLS_CATALOG],
  };
}

/** The repository path of a Markdown page linked from a navigation file. */
function linkedPage(navigationFile: string, target: string): string {
  const repoPath = posix.normalize(posix.join(posix.dirname(navigationFile), target));
  if (!repoPath.endsWith(".md")) {
    throw new Error(`${navigationFile}: "${target}" is not a Markdown page`);
  }
  return repoPath;
}

/** `docs/wiki/Home.md` is the docs landing page; every other page is named after its file. */
function wikiPageId(repoPath: string): string {
  if (repoPath === WIKI_HOME) return "docs";
  return `docs/${pageName(repoPath)}`;
}

function pageName(repoPath: string): string {
  return posix.basename(repoPath, ".md").toLowerCase();
}

function assertUnique(values: readonly string[], kind: string): void {
  const seen = new Set<string>();
  for (const value of values) {
    if (seen.has(value)) throw new Error(`Duplicate ${kind}: ${value}`);
    seen.add(value);
  }
}

function readRepoText(repoPath: string): string {
  return readFileSync(repoFileUrl(repoPath), "utf8");
}

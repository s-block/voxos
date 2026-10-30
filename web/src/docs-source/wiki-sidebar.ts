const GROUP_HEADING = /^##\s+(.+?)\s*$/;
const LIST_ITEM = /^\s*[-*+]\s/;
const LINK_ITEM = /^\s*[-*+]\s+\[([^\]]+)\]\(([^)\s]+)\)\s*$/;

export interface WikiNavLink {
  readonly label: string;
  /** Link target exactly as written, relative to the sidebar file. */
  readonly target: string;
}

export interface WikiNavGroup {
  /** `undefined` for the links listed before the first `##` heading. */
  readonly label: string | undefined;
  readonly links: readonly WikiNavLink[];
}

/**
 * Reads a navigation page, such as the GitHub Wiki `_Sidebar.md` or the guides
 * index: link list items, grouped by `##` headings.
 */
export function parseWikiSidebar(markdown: string, fileName = "_Sidebar.md"): WikiNavGroup[] {
  const groups: { label: string | undefined; links: WikiNavLink[] }[] = [
    { label: undefined, links: [] },
  ];
  for (const [index, line] of markdown.split(/\r?\n/).entries()) {
    const heading = GROUP_HEADING.exec(line);
    if (heading?.[1]) {
      groups.push({ label: heading[1], links: [] });
      continue;
    }
    if (!LIST_ITEM.test(line)) continue;
    const link = LINK_ITEM.exec(line);
    if (!link?.[1] || !link[2]) {
      throw new Error(
        `${fileName}:${index + 1}: list items must be single Markdown links, found "${line.trim()}"`,
      );
    }
    groups.at(-1)?.links.push({ label: link[1], target: link[2] });
  }
  return groups.filter((group) => group.links.length > 0);
}

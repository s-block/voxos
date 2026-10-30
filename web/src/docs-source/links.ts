import { posix } from "node:path";

const SCHEME_PATTERN = /^[a-z][a-z\d+.-]*:/i;

export interface RepoLinkTargets {
  /** Site paths of published docs, keyed by repo-relative Markdown path. */
  readonly routes: ReadonlyMap<string, string>;
  readonly blobUrl: (repoPath: string) => string;
  readonly treeUrl: (repoPath: string) => string;
}

/**
 * Maps a link written for GitHub's Markdown rendering onto the site: links to
 * published docs become site routes, other repository paths open on GitHub,
 * and external or in-page links stay as they are.
 */
export function resolveRepoLink(
  href: string,
  fromRepoPath: string,
  targets: RepoLinkTargets,
): string {
  if (
    href === "" ||
    href.startsWith("#") ||
    href.startsWith("//") ||
    SCHEME_PATTERN.test(href)
  ) {
    return href;
  }
  const suffixStart = href.search(/[?#]/);
  const path = suffixStart === -1 ? href : href.slice(0, suffixStart);
  const suffix = suffixStart === -1 ? "" : href.slice(suffixStart);
  const target = posix.normalize(
    path.startsWith("/")
      ? path.slice(1) || "."
      : posix.join(posix.dirname(fromRepoPath), path),
  );
  if (target === ".." || target.startsWith("../")) {
    throw new Error(
      `${fromRepoPath}: link "${href}" points outside the repository`,
    );
  }

  const route = targets.routes.get(target);
  if (route) return route + suffix;

  const repoPath = target === "." || target === "./" ? "" : target.replace(/\/$/, "");
  const isDirectory = path.endsWith("/") || repoPath === "";
  return (
    (isDirectory ? targets.treeUrl(repoPath) : targets.blobUrl(repoPath)) +
    suffix
  );
}

const INLINE_LINK = /(\]\()(<[^>]*>|[^)\s]+)/g;
const FENCE = /^\s*(```|~~~)/;

/**
 * Rewrites inline Markdown link targets outside fenced code, making site
 * routes absolute so the raw Markdown works wherever it is read.
 */
export function rewriteMarkdownLinks(
  markdown: string,
  fromRepoPath: string,
  targets: RepoLinkTargets,
  siteUrl: string,
): string {
  let fence: string | undefined;
  return markdown
    .split("\n")
    .map((line) => {
      const fenceMatch = FENCE.exec(line)?.[1];
      if (fence) {
        if (fenceMatch === fence) fence = undefined;
        return line;
      }
      if (fenceMatch) {
        fence = fenceMatch;
        return line;
      }
      return line.replace(INLINE_LINK, (_match, open: string, href: string) => {
        const bare = href.startsWith("<") ? href.slice(1, -1) : href;
        const resolved = resolveRepoLink(bare, fromRepoPath, targets);
        const absolute = resolved.startsWith("/") ? new URL(resolved, siteUrl).href : resolved;
        return `${open}${href.startsWith("<") ? `<${absolute}>` : absolute}`;
      });
    })
    .join("\n");
}

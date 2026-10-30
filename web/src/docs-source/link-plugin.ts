import type { HastPluginEntry } from "satteri";
import { githubUrls } from "../site.config";
import { resolveRepoLink, type RepoLinkTargets } from "./links";
import { repoFileUrl } from "./repo";
import type { DocsSite } from "./sources";

/**
 * Sätteri HAST plugin that rewrites links in published repository docs so
 * they work on the site. Other Markdown renders untouched.
 */
export function repoLinksPlugin(docs: DocsSite): HastPluginEntry {
  const repoPathsByUrl = new Map(
    docs.sources.map((source) => [repoFileUrl(source.repoPath).href, source.repoPath]),
  );
  const targets: RepoLinkTargets = {
    routes: docs.routes,
    blobUrl: githubUrls.blob,
    treeUrl: githubUrls.tree,
  };
  return ({ fileURL }) => {
    const repoPath = fileURL && repoPathsByUrl.get(fileURL.href);
    if (!repoPath) return undefined;
    return {
      name: "voxos-repo-links",
      element: {
        filter: ["a"],
        visit(node, ctx) {
          const href = node.properties?.["href"];
          if (typeof href !== "string") return;
          const resolved = resolveRepoLink(href, repoPath, targets);
          if (resolved !== href) ctx.setProperty(node, "href", resolved);
        },
      },
    };
  };
}

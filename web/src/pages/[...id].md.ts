import type { APIRoute, GetStaticPaths } from "astro";
import { getCollection, type CollectionEntry } from "astro:content";
import { rewriteMarkdownLinks, type RepoLinkTargets } from "../docs-source/links";
import { configuredSite } from "../site-url";
import { githubUrls } from "../site.config";

/** Each docs page as Markdown (`/docs/quick-start.md`) for AI agents and other tools that read Markdown. */
export const getStaticPaths = (async () => {
  const entries = await getCollection("docs");
  const targets: RepoLinkTargets = {
    routes: new Map(entries.map((entry) => [entry.data.repoPath, `/${entry.id}/`])),
    blobUrl: githubUrls.blob,
    treeUrl: githubUrls.tree,
  };
  return entries.map((entry) => ({ params: { id: entry.id }, props: { entry, targets } }));
}) satisfies GetStaticPaths;

export const GET: APIRoute<{ entry: CollectionEntry<"docs">; targets: RepoLinkTargets }> = ({ props, site }) => {
  const { entry, targets } = props;
  const body = rewriteMarkdownLinks(entry.body ?? "", entry.data.repoPath, targets, configuredSite(site).href);
  return new Response(`# ${entry.data.title}\n\n${body.trimEnd()}\n`, {
    headers: { "Content-Type": "text/markdown; charset=utf-8" },
  });
};

import { readFileSync } from "node:fs";
import type { SitemapItem } from "@astrojs/sitemap";
import { parseRepoDoc } from "./parse";
import { repoFileUrl } from "./repo";
import type { DocsSite } from "./sources";

/**
 * A sitemap `serialize` hook that dates each published page with its source's
 * `Last updated` line, so search engines can tell which pages changed. Pages
 * without that line, such as the landing page, get no `lastmod`.
 */
export function sitemapLastmod(docs: DocsSite): (item: SitemapItem) => SitemapItem {
  const lastmodByPath = new Map<string, string>();
  for (const [repoPath, route] of docs.routes) {
    const { lastUpdated } = parseRepoDoc(readFileSync(repoFileUrl(repoPath), "utf8"), repoPath);
    if (lastUpdated) lastmodByPath.set(route, lastUpdated.toISOString());
  }
  return (item) => {
    const lastmod = lastmodByPath.get(new URL(item.url).pathname);
    return lastmod ? { ...item, lastmod } : item;
  };
}

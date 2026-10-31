import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseRepoDoc } from "./parse";
import { repoFileUrl } from "./repo";
import { sitemapLastmod } from "./sitemap";
import { buildDocsSite } from "./sources";

describe("sitemapLastmod against this repository", () => {
  const serialize = sitemapLastmod(buildDocsSite());

  it("dates a docs page with its Last updated line", () => {
    const repoPath = "docs/wiki/Quick-Start.md";
    const { lastUpdated } = parseRepoDoc(readFileSync(repoFileUrl(repoPath), "utf8"), repoPath);
    const url = "https://example.com/docs/quick-start/";

    expect(lastUpdated).toBeDefined();
    expect(serialize({ url })).toEqual({ url, lastmod: lastUpdated?.toISOString() });
  });

  it("leaves pages without a Last updated line undated", () => {
    for (const url of ["https://example.com/", "https://example.com/docs/security/"]) {
      expect(serialize({ url })).toEqual({ url });
    }
  });
});

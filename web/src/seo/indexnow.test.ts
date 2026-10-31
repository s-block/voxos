import { createHash } from "node:crypto";
import { describe, expect, it } from "vitest";
import { changedPageUrls, indexNowKeyFromEnv, pagePath, parseBucketListing } from "./indexnow";

const site = new URL("https://example.com");
const md5 = (html: string): string => createHash("md5").update(html).digest("hex");
/** A bucket listing as the AWS CLI prints it, with S3's quoted ETags. */
const listing = (objects: Record<string, string>): string =>
  JSON.stringify(Object.entries(objects).map(([key, html]) => [key, `"${md5(html)}"`]));

describe("indexNowKeyFromEnv", () => {
  it("is unset when the setting is missing or empty", () => {
    expect(indexNowKeyFromEnv(undefined)).toBeUndefined();
    expect(indexNowKeyFromEnv("")).toBeUndefined();
  });

  it.each(["a".repeat(8), "f0".repeat(64), "I-love-IndexNow-3000"])("accepts %s", (key) => {
    expect(indexNowKeyFromEnv(key)).toBe(key);
  });

  it.each(["a".repeat(7), "a".repeat(129), " padded-key ", "key_underscore", "../outside"])("rejects %s", (key) => {
    expect(() => indexNowKeyFromEnv(key)).toThrow("WEB_INDEXNOW_KEY");
  });
});

describe("pagePath", () => {
  it("serves each directory's index.html at the directory URL", () => {
    expect(pagePath("index.html")).toBe("/");
    expect(pagePath("docs/quick-start/index.html")).toBe("/docs/quick-start/");
  });

  it.each(["404.html", "docs/quick-start.md", "robots.txt", "_astro/page.Ab12.css", "docs/myindex.html"])(
    "skips %s, which is not a page",
    (file) => {
      expect(pagePath(file)).toBeUndefined();
    },
  );
});

describe("parseBucketListing", () => {
  it("reads each object's ETag without S3's quotes", () => {
    expect(parseBucketListing('[["index.html", "\\"abc123\\""]]\n')).toEqual(new Map([["index.html", "abc123"]]));
  });

  it("reads an empty bucket, which lists null", () => {
    expect(parseBucketListing("null\n").size).toBe(0);
  });

  it.each(['{"Contents": []}', '[["index.html"]]', '[["index.html", 42]]'])("rejects %s", (json) => {
    expect(() => parseBucketListing(json)).toThrow("bucket listing");
  });
});

describe("changedPageUrls", () => {
  const deployed = parseBucketListing(
    listing({
      "index.html": "<h1>Home</h1>",
      "docs/quick-start/index.html": "<h1>Quick start</h1>",
      "docs/old-page/index.html": "<h1>Old page</h1>",
      "404.html": "<h1>Not found</h1>",
      "docs/quick-start.md": "# Quick start",
    }),
  );

  it("lists the pages a deploy adds, changes or removes", () => {
    const built = new Map([
      ["index.html", md5("<h1>Home</h1>")],
      ["docs/quick-start/index.html", md5("<h1>Quick start, updated</h1>")],
      ["guides/new-guide/index.html", md5("<h1>New guide</h1>")],
      ["404.html", md5("<h1>Page not found</h1>")],
      ["docs/quick-start.md", md5("# Quick start, updated")],
    ]);

    expect(changedPageUrls(built, deployed, site)).toEqual([
      "https://example.com/docs/old-page/",
      "https://example.com/docs/quick-start/",
      "https://example.com/guides/new-guide/",
    ]);
  });

  it("lists nothing when no page changed", () => {
    const built = new Map([
      ["index.html", md5("<h1>Home</h1>")],
      ["docs/quick-start/index.html", md5("<h1>Quick start</h1>")],
      ["docs/old-page/index.html", md5("<h1>Old page</h1>")],
    ]);

    expect(changedPageUrls(built, deployed, site)).toEqual([]);
  });

  it("lists every page when the bucket is empty", () => {
    const built = new Map([
      ["index.html", md5("<h1>Home</h1>")],
      ["docs/quick-start/index.html", md5("<h1>Quick start</h1>")],
    ]);

    expect(changedPageUrls(built, parseBucketListing("null"), site)).toEqual([
      "https://example.com/",
      "https://example.com/docs/quick-start/",
    ]);
  });
});

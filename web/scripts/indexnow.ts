/**
 * Deploy step: submits the pages a deploy added, changed or removed to
 * IndexNow, so Bing and the other IndexNow search engines recrawl them.
 * deploy.sh runs it after the upload, with the bucket listing it saved before
 * the upload:
 *
 *   node scripts/indexnow.ts <bucket-listing.json> [--dry-run]
 *
 * It logs only page counts and IndexNow's response status, never the key. The
 * site is already live when it runs, so a failed submission is a warning.
 */
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { changedPageUrls, INDEXNOW_ENDPOINT, indexNowKeyFromEnv, pagePath, parseBucketListing } from "../src/seo/indexnow.ts";
import { siteUrlFromEnv } from "../src/site-url.ts";

const distDir = fileURLToPath(new URL("../dist/", import.meta.url));

const [listingFile, mode] = process.argv.slice(2);
if (listingFile === undefined || (mode !== undefined && mode !== "--dry-run")) {
  console.error("Usage: node scripts/indexnow.ts <bucket-listing.json> [--dry-run]");
  process.exit(2);
}
const key = indexNowKeyFromEnv(process.env.WEB_INDEXNOW_KEY);
if (key === undefined) {
  console.error("Set WEB_INDEXNOW_KEY to submit changed pages to IndexNow.");
  process.exit(2);
}
// deploy.sh checks that the build in dist/ was made for this WEB_SITE_URL.
const site = siteUrlFromEnv(process.env.WEB_SITE_URL);

/** The MD5 checksum of each page in `dist/`, by its key in the bucket. */
async function builtPages(): Promise<Map<string, string>> {
  const pages = new Map<string, string>();
  for (const entry of await readdir(distDir, { withFileTypes: true, recursive: true })) {
    const file = relative(distDir, join(entry.parentPath, entry.name)).split(sep).join("/");
    if (entry.isFile() && pagePath(file) !== undefined) {
      pages.set(file, createHash("md5").update(await readFile(join(distDir, file))).digest("hex"));
    }
  }
  return pages;
}

/** Prints a warning, as an annotation on GitHub Actions runs. */
function warn(message: string): void {
  if (process.env.GITHUB_ACTIONS === "true") console.log(`::warning title=IndexNow::${message}`);
  else console.warn(message);
}

const urls = changedPageUrls(await builtPages(), parseBucketListing(await readFile(listingFile, "utf8")), site);
const pages = `${urls.length} changed ${urls.length === 1 ? "page" : "pages"}`;

if (urls.length === 0) {
  console.log("IndexNow: no pages changed, so there is nothing to submit.");
} else if (mode === "--dry-run") {
  console.log(`IndexNow: would submit ${pages}.`);
} else {
  try {
    const response = await fetch(INDEXNOW_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ host: site.host, key, urlList: urls }),
      signal: AbortSignal.timeout(30_000),
    });
    await response.body?.cancel();
    if (response.ok) console.log(`IndexNow accepted ${pages} (HTTP ${response.status}).`);
    else warn(`IndexNow rejected ${pages} (HTTP ${response.status}).`);
  } catch (error) {
    warn(`IndexNow could not be reached to submit ${pages}: ${error instanceof Error ? error.message : String(error)}`);
  }
}

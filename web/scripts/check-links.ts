/**
 * Post-build check: every internal link, image and social card in `dist/`
 * points at a file that exists, and every `#fragment` at an element id on
 * the target page. Run with `node scripts/check-links.ts` after `astro build`.
 */
import { existsSync } from "node:fs";
import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { Parser } from "htmlparser2";
import { siteUrlFromEnv } from "../src/site-url.ts";

interface PageLinks {
  readonly ids: Set<string>;
  readonly links: string[];
}

const distDir = fileURLToPath(new URL("../dist/", import.meta.url));
// npm run build runs this check with the same WEB_SITE_URL as astro build.
const siteOrigin = siteUrlFromEnv(process.env.WEB_SITE_URL).origin;

async function htmlFiles(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true, recursive: true });
  return entries
    .filter((entry) => entry.isFile() && entry.name.endsWith(".html"))
    .map((entry) => join(entry.parentPath, entry.name));
}

function collect(html: string): PageLinks {
  const page: PageLinks = { ids: new Set(), links: [] };
  const parser = new Parser({
    onopentag(name, attributes) {
      if (attributes["id"]) page.ids.add(attributes["id"]);
      if (name === "a" && attributes["href"]) page.links.push(attributes["href"]);
      if (name === "img" && attributes["src"]) page.links.push(attributes["src"]);
      if (name === "link" && attributes["href"] && attributes["rel"] !== "preconnect") {
        page.links.push(attributes["href"]);
      }
      if (name === "meta" && ["og:image", "og:url"].includes(attributes["property"] ?? "")) {
        page.links.push(attributes["content"] ?? "");
      }
    },
  });
  parser.write(html);
  parser.end();
  return page;
}

/** The file in `dist/` a site path is served from, or `undefined` for paths a static host cannot serve. */
function distFileFor(pathname: string): string | undefined {
  const decoded = decodeURIComponent(pathname);
  if (decoded.endsWith("/")) return join(distDir, decoded, "index.html");
  if (/\.[a-z\d]+$/i.test(decoded)) return join(distDir, decoded);
  return undefined;
}

const pages = new Map<string, PageLinks>();
for (const file of await htmlFiles(distDir)) {
  pages.set(file, collect(await readFile(file, "utf8")));
}

const problems: string[] = [];
for (const [file, page] of pages) {
  const pagePath = `/${relative(distDir, file).replace(/index\.html$/, "")}`;
  for (const link of page.links) {
    if (link === "" || link.startsWith("//")) continue;
    const url = new URL(link, siteOrigin + pagePath);
    if (url.origin !== siteOrigin || url.protocol === "mailto:") continue;

    const target = distFileFor(url.pathname);
    const source = relative(distDir, file);
    if (!target) {
      problems.push(`${source}: "${link}" needs a trailing slash or a file extension`);
      continue;
    }
    if (!existsSync(target)) {
      problems.push(`${source}: "${link}" does not exist in dist/`);
      continue;
    }
    const fragment = decodeURIComponent(url.hash.slice(1));
    if (fragment && target.endsWith(".html") && !pages.get(target)?.ids.has(fragment)) {
      problems.push(`${source}: "${link}" has no element with id "${fragment}"`);
    }
  }
}

if (problems.length > 0) {
  console.error(`Found ${problems.length} broken internal links:\n${problems.join("\n")}`);
  process.exit(1);
}
console.log(`Checked internal links in ${pages.size} pages.`);

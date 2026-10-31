// IndexNow (https://www.indexnow.org) lets the site tell Bing and the other
// participating search engines which pages changed, so they recrawl them soon
// after a deploy. Deploys run it through scripts/indexnow.ts, which imports
// this file with Node, so it imports nothing.

/** IndexNow's shared endpoint, which passes each submission on to every participating search engine. */
export const INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow";

/**
 * The IndexNow key from the WEB_INDEXNOW_KEY setting, or `undefined` when it
 * is not set. The site serves it as `/<key>.txt`, which shows IndexNow that
 * submissions for the site's host come from its owner.
 */
export function indexNowKeyFromEnv(value: string | undefined): string | undefined {
  if (value === undefined || value === "") return undefined;
  if (!/^[A-Za-z0-9-]{8,128}$/.test(value)) {
    throw new Error(
      "WEB_INDEXNOW_KEY must be 8 to 128 letters, digits or dashes, such as the output of openssl rand -hex 16.",
    );
  }
  return value;
}

/** The URL path a built file serves as a page: `docs/quick-start/index.html` serves `/docs/quick-start/`. */
export function pagePath(file: string): string | undefined {
  if (file === "index.html") return "/";
  return file.endsWith("/index.html") ? `/${file.slice(0, -"index.html".length)}` : undefined;
}

function isKeyAndEtag(entry: unknown): entry is [string, string] {
  return Array.isArray(entry) && entry.length === 2 && entry.every((value) => typeof value === "string");
}

/**
 * The bucket's ETags by object key, from the JSON that
 * `aws s3api list-objects-v2 --query "Contents[].[Key, ETag]" --output json`
 * prints. The listing is `null` for an empty bucket.
 */
export function parseBucketListing(json: string): Map<string, string> {
  const listing: unknown = JSON.parse(json);
  if (listing === null) return new Map();
  if (!Array.isArray(listing) || !listing.every(isKeyAndEtag)) {
    throw new Error("The bucket listing must be a JSON array of [key, ETag] pairs.");
  }
  // S3 wraps each ETag in double quotes.
  return new Map(listing.map(([key, etag]) => [key, etag.replaceAll('"', "")]));
}

/**
 * The URLs of the pages a deploy adds, changes or removes, sorted. `built`
 * maps files in the new build to their MD5 checksums, and `deployed` maps the
 * objects already in the bucket to their ETags. An object uploaded in one part
 * with S3-managed encryption has its MD5 checksum as its ETag, so an unchanged
 * page has the same value in both.
 */
export function changedPageUrls(
  built: ReadonlyMap<string, string>,
  deployed: ReadonlyMap<string, string>,
  site: URL,
): string[] {
  const files = new Set([...built.keys(), ...deployed.keys()]);
  return [...files]
    .filter((file) => built.get(file) !== deployed.get(file))
    .map((file) => pagePath(file))
    .filter((path) => path !== undefined)
    .map((path) => new URL(path, site).href)
    .sort();
}

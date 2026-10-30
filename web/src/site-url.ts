/** Where pages point when WEB_SITE_URL is not set: the local dev server. */
export const LOCAL_SITE_URL = "http://localhost:4321";

/**
 * The site's public origin, such as `https://example.com`, from the
 * WEB_SITE_URL setting. Canonical links, the sitemap, structured data and
 * social cards are built from it, so it must be a bare origin.
 */
export function siteUrlFromEnv(value: string | undefined): URL {
  const url = URL.parse(value?.trim() || LOCAL_SITE_URL);
  if (
    url === null ||
    !["http:", "https:"].includes(url.protocol) ||
    url.username !== "" ||
    url.password !== "" ||
    url.pathname !== "/" ||
    url.search !== "" ||
    url.hash !== ""
  ) {
    throw new Error(
      "WEB_SITE_URL must be an http or https origin such as https://example.com, with no path, query or credentials.",
    );
  }
  return url;
}

/** Astro's `site`, which astro.config.ts always sets from WEB_SITE_URL. */
export function configuredSite(site: URL | undefined): URL {
  if (site === undefined) {
    throw new Error("Astro's site is not set; astro.config.ts sets it from WEB_SITE_URL.");
  }
  return site;
}

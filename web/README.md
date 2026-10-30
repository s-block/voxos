# Voxos website

The public website for Voxos: a landing page plus the full documentation,
built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build)
into static files served from an S3 bucket through Cloudflare.

The docs are rendered from the Markdown already in this repository. Nothing is
copied into `web/`, so editing a page under `docs/wiki/` updates the site on the
next build.

## Commands

Run these from the repository root. They need Node.js 22.23 or newer (Node 24
recommended).

| Command | What it does |
| --- | --- |
| `make web-dev` | Dev server on `http://localhost:4321`. Edits to the source Markdown reload the page. |
| `make web-check` | ESLint, `astro check` and the Vitest unit tests. |
| `make web-build` | `web-check`, then a static build into `web/dist/` and a check of every internal link and `#fragment`. |
| `make web-preview` | Serves the last build on `http://localhost:4322`. |
| `make web-deploy` | Builds, then uploads `web/dist/` to S3 and purges the site from the Cloudflare cache. |

## Settings

The site's address and deployment details are settings, not source. Copy
`web/.env.example` to `web/.env`, which git ignores, and fill in your values.
The `make web-*` targets read `web/.env`, and its values take precedence over
the environment.

| Setting | Used for |
| --- | --- |
| `WEB_SITE_URL` | The site's public URL, such as `https://example.com`. Canonical links, the sitemap, structured data and social cards are built from it. Without it, pages point at `http://localhost:4321`. |
| `WEB_S3_BUCKET` | The bucket that `make web-deploy` uploads to. |
| `WEB_CLOUDFLARE_ZONE_ID` | The Cloudflare zone that serves the site. |
| `WEB_CLOUDFLARE_API_TOKEN` | A Cloudflare API token that can purge the zone's cache. |
| `AWS_PROFILE` | Optional AWS CLI profile for the upload. |

## How the docs are built

`src/docs-source/` turns repository Markdown into the Starlight `docs`
collection:

- **Pages and navigation.** Every link in `docs/wiki/_Sidebar.md` becomes a page, with
  the sidebar's labels and grouping. `docs/wiki/Home.md` is served at `/docs/`
  and other wiki pages at `/docs/<file-name>/`. The curated skills in
  `examples/skills/catalog.toml` appear under **Example skills**, and
  `MORE_PAGES` in `sources.ts` adds a few other repository files, such as the
  Helm chart guide.
- **Titles and metadata.** The first `# Heading` is the page title. A
  `Last updated: YYYY-MM-DD` line becomes the page's last-updated date. The first
  prose paragraph of at least 50 characters is the search description; skills
  use their catalog description instead.
- **Links.** Links to published pages become site routes, links to other
  repository files open on GitHub, and external links are unchanged.
- **Machine-readable copies.** Each page is also served as Markdown at
  `/docs/<page>.md`, and `llms.txt`, `llms-full.txt` and `llms-small.txt` list
  and bundle the docs for AI tools.

To publish another repository file, link it from `docs/wiki/_Sidebar.md` or add
it to `MORE_PAGES`. The build fails if a published file is missing, has no
title heading or intro paragraph, or contains a link that leaves the
repository.

## Project layout

```text
web/
  .env.example           settings template; copy it to web/.env
  astro.config.ts        site, Starlight, fonts and Markdown pipeline
  src/site.config.ts     project name, description and repository links
  src/site-url.ts        the site's URL, from WEB_SITE_URL
  src/content.config.ts  the docs collection
  src/docs-source/       repository Markdown loader, link rewriting, sidebar
  src/data/              landing page copy: channels, features, integrations, FAQ
  src/components/        landing sections, shared UI, Starlight overrides
  src/pages/             landing page, 404, robots.txt, icons, social cards, .md copies
  src/seo/               structured data and social card rendering
  src/styles/            design tokens, landing styles, Starlight theme
  scripts/               post-build link check and deploy
```

Brand artwork is imported from `docs/design/`. Social cards (`/og/*.png`), the
favicon and the Apple touch icon are generated from it at build time.

## Deploying

The site is a set of static files in an S3 bucket, served through Cloudflare.
Cloudflare provides HTTPS, caches pages close to visitors, and redirects the
`www` hostname to the bare domain. Cloudflare's Free plan covers all of it, and
S3 storage and requests for a site this size cost a few cents a month at most.

The setup commands below use your settings, so load them into your shell
first:

```bash
set -a; . web/.env; set +a
```

### One-time S3 setup

1. Create a bucket named exactly like the site's domain (the host in
   `WEB_SITE_URL`), set `WEB_S3_BUCKET` to that name, and leave Block Public
   Access on. S3 website endpoints find the bucket from the request's `Host`
   header, which Cloudflare passes through, so the name must match the domain.
2. Turn on static website hosting:

   ```bash
   aws s3 website "s3://${WEB_S3_BUCKET}" --index-document index.html --error-document 404.html
   ```

   The website endpoint serves `/docs/` from `docs/index.html` and redirects
   `/docs` to `/docs/`.
3. Let only Cloudflare read the bucket. The policy below allows Cloudflare's
   published IPv4 ranges (S3 website endpoints do not use IPv6). S3 does not
   count a policy limited to those ranges as public, so Block Public Access
   stays on. `s3:ListBucket` makes missing pages return 404 instead of 403.
   Run this again if Cloudflare changes its ranges.

   ```bash
   policy="$(curl -fsS https://api.cloudflare.com/client/v4/ips | jq -c --arg bucket "${WEB_S3_BUCKET}" '{
     Version: "2012-10-17",
     Statement: [{
       Sid: "CloudflareReadsWebsite",
       Effect: "Allow",
       Principal: "*",
       Action: ["s3:GetObject", "s3:ListBucket"],
       Resource: ["arn:aws:s3:::\($bucket)", "arn:aws:s3:::\($bucket)/*"],
       Condition: {IpAddress: {"aws:SourceIp": .result.ipv4_cidrs}}
     }]
   }')"
   aws s3api put-bucket-policy --bucket "${WEB_S3_BUCKET}" --policy "${policy}"
   ```

4. Give the deploy credentials `s3:ListBucket` on the bucket, and
   `s3:PutObject` and `s3:DeleteObject` on its objects.

### One-time Cloudflare setup

1. **DNS.** Add a proxied `CNAME` record from the site's domain to the bucket's
   website endpoint, which the S3 console shows under static website hosting.
   Add a proxied `CNAME` from `www` to the site's domain.
2. **SSL/TLS.** S3 website endpoints only serve HTTP, so Cloudflare reaches the
   bucket in Flexible mode while visitors use HTTPS. Set the zone's encryption
   mode to Flexible, or, if other hostnames in the zone need Full, add a
   configuration rule that sets Flexible for the site's hostname. Turn on
   **Always Use HTTPS**.
3. **Redirect rule.** Send `www` to the bare domain with a wildcard pattern of
   `https://www.*`, target URL `https://${1}`, status code 301, and **Preserve
   query string** on.
4. **Cache rule.** For the site's hostname, choose **Eligible for cache**, set
   Edge TTL to **Use cache-control header if present, use default Cloudflare
   caching behavior if not**, and set Browser TTL to **Respect origin**.
   Without that Browser TTL, Cloudflare stretches the five-minute browser
   lifetime of pages to its four-hour default.
5. **API token.** Create a custom API token with the **Zone → Cache Purge →
   Purge** permission for this zone, and set it as `WEB_CLOUDFLARE_API_TOKEN`.
   Set `WEB_CLOUDFLARE_ZONE_ID` to the zone ID from the zone's overview page.
6. **AI crawlers.** Cloudflare can block some AI crawlers by default. In **AI
   Crawl Control**, set the search, assistant and training crawlers to
   **Allow** so AI tools can read the docs, `llms.txt` and the Markdown
   copies. The site's own `robots.txt` allows every crawler.

### Deploy

```bash
make web-deploy
```

The deploy builds the site for `WEB_SITE_URL`, checks that `web/dist/` was
built for that URL, uploads it to the bucket, then purges the site's hostname
from the Cloudflare cache. Set `WEB_DEPLOY_DRY_RUN=1` to list the changes
without uploading or purging anything.

| Files | Cache-Control |
| --- | --- |
| `_astro/*` (hashed assets) | `public, max-age=31536000, immutable`; older assets stay in the bucket |
| Pages and other files | `public, max-age=300, s-maxage=86400`: five minutes in browsers, and a day at Cloudflare until the next deploy purges them |
| `*.md` page copies | Same as pages, with `Content-Type: text/markdown; charset=utf-8` |

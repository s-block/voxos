# Voxos website

The public website for Voxos: a landing page plus the full documentation,
built with [Astro](https://astro.build) and [Starlight](https://starlight.astro.build)
into static files served from a private S3 bucket through CloudFront.

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
| `make web-deploy` | Builds, then uploads `web/dist/` to S3 and invalidates the CloudFront cache. |

## Settings

The site's address and deployment details are settings, not source. Copy
`web/.env.example` to `web/.env`, which git ignores, and fill in your values.
The `make web-*` targets read `web/.env`, and its values take precedence over
the environment.

| Setting | Used for |
| --- | --- |
| `WEB_SITE_URL` | The site's public URL, such as `https://example.com`. Canonical links, the sitemap, structured data and social cards are built from it. Without it, pages point at `http://localhost:4321`. |
| `WEB_S3_BUCKET` | The private bucket that `make web-deploy` uploads to. |
| `WEB_CLOUDFRONT_DISTRIBUTION_ID` | The CloudFront distribution that serves the bucket. |
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
  infra/                 CloudFront Function for directory URLs
  scripts/               post-build link check and deploy
```

Brand artwork is imported from `docs/design/`. Social cards (`/og/*.png`), the
favicon and the Apple touch icon are generated from it at build time.

## Deploying

The site is a set of static files in a private S3 bucket, served by CloudFront
over HTTPS. CloudFront reads the bucket over HTTPS through Origin Access
Control, so every hop is encrypted and the bucket never needs public access.
Cloudflare, or another DNS provider, points the domain at CloudFront and
redirects `www` to the bare domain. At this traffic, CloudFront's always-free
allowance (1 TB of transfer, 10 million requests and 2 million function
invocations a month) covers the site, and S3 costs a few cents a month at most.

The setup commands below use your settings, so load them into your shell
first:

```bash
set -a; . web/.env; set +a
```

### One-time AWS setup

1. Create the bucket named in `WEB_S3_BUCKET`, with Block Public Access on and
   static website hosting off.
2. Request an ACM certificate in `us-east-1` for the site's domain, with DNS
   validation, and add the validation record it shows to your DNS.
3. Create and publish the CloudFront Function that serves directory URLs from
   their `index.html` and adds missing trailing slashes:

   ```bash
   aws cloudfront create-function --name website-paths \
     --function-config 'Comment=Directory URLs and trailing slashes,Runtime=cloudfront-js-2.0' \
     --function-code fileb://web/infra/cloudfront-function.js
   aws cloudfront publish-function --name website-paths \
     --if-match "$(aws cloudfront describe-function --name website-paths --query ETag --output text)"
   ```

   After changing `infra/cloudfront-function.js`, update the function with
   `aws cloudfront update-function` and publish it again.
4. Create a CloudFront distribution:
   - **Origin:** the bucket's REST endpoint (not its website endpoint) with
     Origin Access Control, and the bucket policy CloudFront offers for it.
   - **Behavior:** redirect HTTP to HTTPS, with the `CachingOptimized` cache
     policy and the `SecurityHeadersPolicy` response headers policy.
   - **Domain:** the site's domain as the alternate domain name, with the
     certificate.
   - **Default root object:** `index.html`.
   - **Function association:** the published function on the viewer-request
     event.
   - **Error pages:** map 403 and 404 to `/404.html` with response code 404.

   Set `WEB_CLOUDFRONT_DISTRIBUTION_ID` to the distribution's ID.
5. Give the deploy credentials `s3:ListBucket` on the bucket, `s3:PutObject`
   and `s3:DeleteObject` on its objects, and `cloudfront:CreateInvalidation` on
   the distribution.

### DNS and the www redirect with Cloudflare

1. Add a `CNAME` record from the site's domain to the distribution's domain
   name (`….cloudfront.net`), set to **DNS only**. Cloudflare flattens it at the
   apex. Keep the certificate's validation record DNS only as well.
2. Add a proxied `AAAA` record for `www` pointing to `100::`. Cloudflare answers
   for `www` itself, so the address is never contacted.
3. Add a redirect rule with a wildcard pattern of `https://www.*`, target URL
   `https://${1}`, status code 301, and **Preserve query string** on.

With another DNS provider, point the domain at the distribution with alias `A`
and `AAAA` records, and redirect `www` there.

### Deploy

```bash
make web-deploy
```

The deploy builds the site for `WEB_SITE_URL`, checks that `web/dist/` was
built for that URL, uploads it to the bucket, then invalidates the
distribution's cache. Set `WEB_DEPLOY_DRY_RUN=1` to list the changes without
uploading or invalidating anything.

| Files | Cache-Control |
| --- | --- |
| `_astro/*` (hashed assets) | `public, max-age=31536000, immutable`; older assets stay in the bucket |
| Pages and other files | `public, max-age=300, s-maxage=86400`: five minutes in browsers, and a day at CloudFront until the next deploy invalidates them |
| `*.md` page copies | Same as pages, with `Content-Type: text/markdown; charset=utf-8` |

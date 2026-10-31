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
| `WEB_GA_MEASUREMENT_ID` | Optional Google Analytics 4 measurement ID, such as `G-ABC123`. Without it, the site loads no analytics. With it, Google Analytics loads only after a visitor allows it in the consent banner, and only on the host in `WEB_SITE_URL`. |
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
- **Guides.** `docs/guides/README.md` is the guides index at `/guides/`, and
  every guide it links becomes a page at `/guides/<file-name>/`, listed under
  **Guides** in the sidebar.
- **Titles and metadata.** The first `# Heading` is the page title. A
  `Last updated: YYYY-MM-DD` line becomes the page's last-updated date. The first
  prose paragraph of at least 50 characters is the search description; skills
  use their catalog description instead.
- **Links.** Links to published pages become site routes, links to other
  repository files open on GitHub, and external links are unchanged.
- **Machine-readable copies.** Each page is also served as Markdown at
  `/docs/<page>.md`, and `llms.txt`, `llms-full.txt` and `llms-small.txt` list
  and bundle the docs for AI tools.

To publish another repository file, link it from `docs/wiki/_Sidebar.md` or
`docs/guides/README.md`, or add it to `MORE_PAGES`. The build fails if a published file is missing, has no
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
  infra/                 CloudFront Function for directory URLs and the www redirect
  scripts/               post-build link check and deploy
```

Brand artwork is imported from `docs/design/`. Social cards (`/og/*.png`), the
favicon and the Apple touch icon are generated from it at build time.

## Deploying

The site is a set of static files in a private S3 bucket, served by CloudFront
over HTTPS. CloudFront reads the bucket over HTTPS through Origin Access
Control, so every hop is encrypted and the bucket never needs public access.
Route 53 points the domain and its `www` name at CloudFront, and a CloudFront
Function redirects `www` to the bare domain. At this traffic, CloudFront's
always-free allowance (1 TB of transfer, 10 million requests and 2 million
function invocations a month) covers the site, and S3 costs a few cents a month
at most.

The setup commands below use your settings, so load them into your shell
first:

```bash
set -a; . web/.env; set +a
```

### One-time AWS setup

1. Create the bucket named in `WEB_S3_BUCKET`, with Block Public Access on and
   static website hosting off.
2. Request an ACM certificate in `us-east-1` for the site's domain and its `www`
   name, with DNS validation. On the certificate's page, **Create records in
   Route 53** adds the validation records to the domain's hosted zone.
3. Create and publish the CloudFront Function that serves directory URLs from
   their `index.html`, adds missing trailing slashes, and redirects `www` to the
   bare domain:

   ```bash
   aws cloudfront create-function --name website-paths \
     --function-config 'Comment=Directory URLs and the www redirect,Runtime=cloudfront-js-2.0' \
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
   - **Domains:** the site's domain and its `www` name as alternate domain
     names, with the certificate.
   - **Default root object:** `index.html`.
   - **Function association:** the published function on the viewer-request
     event.
   - **Error pages:** map 403 and 404 to `/404.html` with response code 404.

   Set `WEB_CLOUDFRONT_DISTRIBUTION_ID` to the distribution's ID.
5. Give the deploy credentials `s3:ListBucket` on the bucket, `s3:PutObject`
   and `s3:DeleteObject` on its objects, and `cloudfront:CreateInvalidation` on
   the distribution.

### DNS in Route 53

In the domain's hosted zone, add alias `A` and `AAAA` records for the domain
and for `www`, each pointing to the distribution. Requests for `www` reach the
CloudFront Function, which answers with a 301 to the same path on the bare
domain.

With another DNS provider, point both names at the distribution's domain name
(`….cloudfront.net`) with `CNAME` records, using the provider's `ALIAS` record
or CNAME flattening for the bare domain. If the provider can proxy traffic, as
Cloudflare does, set these records to DNS only.

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

### Deploy on merge

`.github/workflows/website.yml` deploys `main` on every push. It runs the same
checks and build as CI, then `web/scripts/deploy.sh`, and signs in to AWS
through GitHub's OpenID Connect provider, so no AWS keys are stored. Forks skip
it.

The job runs in a GitHub environment named `website`, limited to the `main`
branch, which holds the deploy settings:

| Name | Kind | Value |
| --- | --- | --- |
| `WEB_SITE_URL` | variable | The site's public URL |
| `WEB_GA_MEASUREMENT_ID` | variable | Optional Google Analytics 4 measurement ID |
| `WEB_S3_BUCKET` | variable | The site's bucket |
| `WEB_CLOUDFRONT_DISTRIBUTION_ID` | variable | The distribution that serves it |
| `WEB_AWS_REGION` | variable | The bucket's region |
| `WEB_AWS_ROLE_ARN` | secret | The deploy role; a secret keeps its account ID out of the public run logs |

Give the deploy role the permissions from step 5, and a trust policy for
`token.actions.githubusercontent.com` that accepts only the audience
`sts.amazonaws.com` and the subject `<prefix>:environment:website`. This
command shows the repository's subject settings:

```bash
gh api repos/<owner>/<repository>/actions/oidc/customization/sub
```

Repositories that use GitHub's immutable subjects take the response's
`sub_claim_prefix`, such as `repo:octo-org@123/octo-repo@456`, which includes
the owner and repository IDs. That covers every repository created, renamed or
transferred since 15 July 2026, whatever `use_immutable_subject` says, and
older repositories where `use_immutable_subject` is `true`. Other repositories
use `repo:<owner>/<repository>`. If the sign-in step fails with
`Not authorized to perform sts:AssumeRoleWithWebIdentity`, CloudTrail's
`AssumeRoleWithWebIdentity` event in the `WEB_AWS_REGION` region shows the
subject GitHub sent.

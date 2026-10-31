# Voxos Website Agent Guide

These rules apply inside `web/`, in addition to the repository root
`AGENTS.md`. `README.md` explains how the site is built and deployed.

## Content

- Never copy repository Markdown into `web/`. Docs pages are rendered from the
  source files, so fix wording in the source (for example `docs/wiki/`), and
  fix loading or link problems in `src/docs-source/`.
- Never mention or link to a hosted or commercial Voxos service. The site links
  only to its own pages, the GitHub repository, and third-party docs.
- Every claim on the landing page must be backed by the docs. Copy lives in
  `src/data/`, and each item links to the docs section that supports it. Check
  the source docs before adding or changing a capability. The post-build link
  check fails when a linked page or `#fragment` does not exist.
- Guides in `docs/guides/` are task-first walkthroughs for a job people
  actually search for. Check every step against the docs and code, link to the
  reference docs instead of repeating them, and write one guide per task, never
  one page per keyword variation. List each guide in `docs/guides/README.md`.
- Do not state goals about stars or traffic on the site.
- Use "Voxos" in prose. The VoxOS lockup is only the logo.
- Keep the tone casual and plain. Use short sentences, and no marketing
  superlatives or invented numbers.

## Code

- Keep pages static. The landing page uses no client framework; scripts are
  small progressive enhancements (copy button, mobile menu).
- Keep the site's domain and deployment details out of the source. Pages get
  the site's URL from Astro's `site` through `configuredSite`, which comes from
  the `WEB_SITE_URL` setting. The bucket, CloudFront distribution and AWS
  profile live in the ignored `web/.env`. Use `example.com` in docs and
  examples.
- Analytics is opt-in. `src/components/site/analytics.ts` loads Google
  Analytics only after a visitor allows it, and only on the configured site's
  host. Track a click by adding `data-analytics-event="<event_name>"` to the
  element; do not load other tracking scripts.
- IndexNow is opt-in through `WEB_INDEXNOW_KEY`. `src/pages/[indexNowKey].txt.ts`
  serves the key file, and `scripts/indexnow.ts` submits changed pages after
  the deploy's upload. Keep page selection in the tested functions in
  `src/seo/indexnow.ts`. Log only counts and response statuses, never the key,
  which is also the key file's name.
- `infra/cloudfront-function.js` runs in CloudFront's `cloudfront-js-2.0`
  runtime. Keep it a plain script with no imports, and update its tests with
  it.
- Put each landing section in its own component under
  `src/components/landing/`, and its data in `src/data/`.
- Style with scoped component CSS and the tokens in `src/styles/tokens.css`.
  The bright brand gradient is decorative: on white, text uses the ink colours
  or `--vx-violet`.
- Code that runs while pages render (components, `src/pages/` endpoints) must
  not build repository paths from `import.meta.url`, because the build bundles
  it elsewhere. Import repository files with Vite (`?raw`) or read them through
  the content collection. `src/docs-source/` builds paths from
  `import.meta.url` safely, because it runs while Astro loads the config and
  syncs content.
- Do not create a `lib/` directory: the root `.gitignore` ignores `lib/`.
- Brand artwork comes from `docs/design/`; do not copy it into `web/`.
- Astro 7 renders Markdown with Sätteri. The link rewriting is a Sätteri HAST
  plugin (`src/docs-source/link-plugin.ts`), and the path logic it uses lives
  in pure, tested functions in `links.ts`.
- `compressHTML: true` keeps the space between inline elements and text that
  wrap across lines in templates.

## Validation

- Run `make web-check` after changes, and `make web-build` when content,
  routing or links change. Finish with `make pre-commit` from the repository
  root.
- For visual changes, preview with `make web-dev` or `make web-preview`, and
  check desktop, tablet and 375px mobile widths without horizontal scrolling.
- Merging to `main` deploys the site through `.github/workflows/website.yml`.
  Never run `make web-deploy` unless the user explicitly asks for a deploy.

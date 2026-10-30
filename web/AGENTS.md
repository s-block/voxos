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
- Never run `make web-deploy` unless the user explicitly asks for a deploy.

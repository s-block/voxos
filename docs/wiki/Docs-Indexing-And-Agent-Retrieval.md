# Docs Indexing And Agent Retrieval

Last updated: 2026-08-08

The wiki is both human documentation and agent-readable product knowledge.
Voxos validates the Markdown, records a manifest, chunks the pages, embeds each
chunk, and stores the chunks in product-doc tables.

## Source Of Truth

Canonical docs live under `docs/wiki/`. They are GitHub Wiki-compatible Markdown
files. Required files are:

- `Home.md`
- `_Sidebar.md`

`_Footer.md` is optional but present in this repository.

Every wiki page should include:

```text
Last updated: YYYY-MM-DD
```

## Validation

Run:

```bash
make docs-validate
```

Validation checks required files, duplicate titles, missing last-updated
metadata, broken relative Markdown links, sidebar links, and manifest freshness.

## Manifest

`docs/wiki/docs-manifest.json` stores page title, path, a short source
fingerprint, last-updated date, and generation time. It lets humans and the docs-refresh
skill detect stale docs without re-indexing first.

Regenerate it with:

```bash
uv run python -m voxos.product_docs.validation --docs-dir docs/wiki --write-manifest
```

## Chunking

Chunking is heading-aware. It preserves code fences and Markdown tables as whole
blocks, records heading breadcrumbs, creates deterministic chunk ids, and targets
medium chunks suitable for RAG. The default target is roughly 500-900 tokens
with bounded overlap.

## Embeddings

The default embedding provider is the official OpenAI Python client with
`text-embedding-3-small` and 1536 dimensions. Product-doc indexing and query
embedding both use the platform OpenAI key from `PLATFORM_OPENAI_API_KEY`.
This setting is an operator/admin credential and is not a user vault credential.

User-owned text or voice provider keys are still used for the chat or voice model
call that consumes retrieved docs. They are not used to create product-doc
embeddings, because retrieval query vectors must be generated with the same
embedding model and platform credential policy as the indexed vectors.

The index schema is fixed to 1536 dimensions, so
`PRODUCT_DOCS_EMBEDDING_DIMENSION` must stay at the default unless the database
schema is migrated at the same time.

## Indexing

Run:

```bash
make docs-index
```

The command validates docs, loads Markdown pages, chunks them, embeds chunks in
batches, upserts sources and chunks, and prunes stale pages and chunks when
configured. The indexer is idempotent for unchanged docs.

## Agent Retrieval Tools

Agents get these read-only internal tools by default:

- `search_voxos_docs`
- `read_voxos_doc_page`

`search_voxos_docs` accepts a top-level `query` plus optional `limit` and
`source_path` filters. It is described to models as product-only retrieval so
general or current external-information requests use the appropriate external
capability instead.

Results include title, source path, heading breadcrumbs, excerpt, and score so
agents can cite the page they used. These tools do not expose hidden metadata,
credentials, memories, transcripts, or user-owned data.

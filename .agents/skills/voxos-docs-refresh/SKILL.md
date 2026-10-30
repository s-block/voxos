---
name: voxos-docs-refresh
description: Refresh the Voxos GitHub Wiki-style docs and product-doc index after backend, frontend, agent, MCP, provider, phone, setup, security, or workflow changes. Use when Codex is asked to update, audit, validate, or keep `docs/wiki/` accurate against git history, current working-tree changes, UI routes, API routers, Make targets, migrations, or agent persistence behavior.
---

# Voxos Docs Refresh

Use this skill to keep `docs/wiki/` and the agent-readable product docs index in
sync with the Voxos codebase.

## Workflow

1. Read `AGENTS.md` and follow the repo validation rules.
2. Inspect docs freshness:
   - Read `docs/wiki/docs-manifest.json`.
   - Find all `Last updated: YYYY-MM-DD` lines in `docs/wiki/*.md`.
   - Use the newest docs date as the baseline for git-history review.
3. Inspect source changes:
   - Run `git status --short`.
   - Run `git log --name-only --since=<newest-docs-date> -- src/voxos frontend docs Makefile pyproject.toml docker-compose.yml alembic`.
   - If docs dates look stale or missing, broaden to recent history with `git log --name-only -n 20`.
4. Map changed areas to wiki pages:
   - `frontend/src/pages`, `frontend/src/routing.ts`, `frontend/src/appConstants.ts` -> `Frontend-Guide.md`.
   - `src/voxos/api/chats.py`, `src/voxos/text/` -> `Chat-And-Text.md`.
   - `src/voxos/api/voice_settings.py`, `src/voxos/voice/`, `src/voxos/providers/` -> `Voice-And-LiveKit.md` and `Providers-And-Keys.md`.
   - `src/voxos/api/integrations.py`, `src/voxos/integrations/` -> `Phone-Integrations.md`.
   - `src/voxos/mcp/`, `docs/user-mcp-servers.md`, `docs/mcp-service-catalog.md` -> `MCP-And-Tools.md`.
   - `src/voxos/agent_*`, `src/voxos/persona/` -> `Agent-Knowledge-And-Persistence.md` and `Operations-Runs-And-Schedules.md`.
   - `src/voxos/auth/`, `src/voxos/db/`, `src/voxos/credentials/`, `src/voxos/security/` -> `Account-And-Auth.md` and `Architecture-And-Security.md`.
   - `Makefile`, `pyproject.toml`, `docker-compose.yml`, `alembic/` -> `Quick-Start.md`, `Developer-Setup.md`, and `Docs-Indexing-And-Agent-Retrieval.md`.
5. Verify behavior from source before editing docs. For UI changes, use
   `voxos-local-login` plus browser verification when local services can run.
6. Edit only affected docs pages, update their `Last updated` date, and avoid
   adding secrets, tokens, private user data, or speculative provider claims.
   Also check the walkthroughs in `docs/guides/` whose steps use a changed
   page, setting or screen, and update them the same way.
7. Regenerate and validate docs:
   - `make docs-manifest`
   - `make docs-validate`
   - `make docs-refresh-check`
   - `make web-build` (the project website renders these pages and checks every
     internal link and anchor)
8. If the request includes refreshing the live product-doc index and the local
   database plus embedding key are configured, run `make docs-index`.
9. Report changed pages, commands run, unresolved verification gaps, and whether
   the index was refreshed.

## Rules

- Treat `docs/wiki/` as the canonical source for user and developer docs.
- Keep GitHub Wiki page filenames stable unless a rename is necessary.
- Prefer correcting stale docs over duplicating the same explanation elsewhere.
- Link focused legacy docs from the wiki when they remain the deeper source.
- Do not claim end-to-end provider, OAuth, phone, or LiveKit behavior unless it
  was verified with real credentials or clearly code-verified.
- Do not run Terraform apply/destroy or deployment targets.

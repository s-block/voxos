# Voxos Agent Guide

Keep changes safe, typed, minimal, and easy to review.

## Project Snapshot

- Python package: `voxos`
- Main code: `src/voxos/`
- Tests: `tests/`
- Frontend test app: `frontend/` (`make frontend`, `make frontend-lint`).
- Tooling: `uv`, Ruff, mypy, pytest, Alembic, and Docker.
- Portable Kubernetes deployment: root `helm/` chart, with separate database
  and application releases. Create referenced Secrets and the database before
  application migration hooks run. Run `make helm-check` with Helm and
  kubeconform after chart changes. Keep installation values and infrastructure
  under ignored `terraform/`; public chart defaults and artifacts stay generic.
- Use root `docker-compose.yml` for the API, migration job, LiveKit, and
  pgvector. Its pgvector init script creates a non-superuser app role
  for RLS testing; do not make `DB_USER` the bootstrap `POSTGRES_USER`.
  `docker-compose.livekit.yml` is only a minimal helper and does not bootstrap
  the app role.

## Current App Shape

- `src/voxos/api/main.py` composes the FastAPI app. Current authenticated API areas include agents, durable runs, schedules, LiveKit tokens/settings, voice settings, text settings, text chats, and user MCP servers.
- Firebase bearer auth maps Firebase identities to durable local Voxos user IDs in `auth_firebase_users`. Use the local `AuthenticatedUser.user_id` for ownership checks, preferences, credentials, LiveKit metadata, MCP context, and persisted agent data.
- The LiveKit worker uses durable user voice preferences and encrypted provider credentials. Runtime voice support currently exists for the LiveKit OpenAI, Google Gemini, and xAI/Grok realtime plugins; other listed providers may have credential storage before worker support.
- LiveKit credentials are tenant-owned runtime settings stored in Postgres and
  the user secret vault. Never configure tenant `LIVEKIT_URL`,
  `LIVEKIT_API_KEY`, or `LIVEKIT_API_SECRET` values in the application or
  supervisor deployment environment. <!-- pragma: allowlist secret -->
  Call setup writes tenant-owned demand; the supervisor resolves only demanded
  settings and sends exact credentials over a pipe to one isolated, idle-TTL
  registration child per fingerprint. That child clears SDK environment copies
  after registration; `ProjectJobEnvironment` installs the same project's
  values only inside each isolated job process so SDK APIs target the correct
  project. API and supervisor replicas must share `VOICE_ROUTE_SIGNING_SECRET`,
  `VOICE_AGENT_NAME`, and worker limits. <!-- pragma: allowlist secret -->
  Production defaults to `VOICE_LIVEKIT_ENDPOINT_POLICY=public_wss`;
  `trusted_private` is only for controlled local/private deployments and never
  replaces network egress controls. Compose has an opt-in `voice` profile.
  `VOICE_WORKER_LEASE_TTL_SECONDS` must exceed the worker shutdown timeout plus
  twice `VOICE_WORKER_POLL_INTERVAL_SECONDS`.
- Text chat supports OpenAI-compatible streaming providers through typed text preferences, credential resolution, persisted chats, runtime events, tool calls, and usage records.
- User-owned MCP servers are remote-only (`streamable_http` or `sse`). Supported auth modes include none, static bearer, API-key header, OAuth authorization code, OAuth client credentials, and private-key JWT. Only enabled, trusted, tested servers with explicit allowed tools should reach agent sessions.
- Durable agent work is Postgres-backed. `agent_runs`, `agent_run_events`, and `agent_schedules` are the queue, history, and scheduling source of truth; `make worker-runs` runs the async run worker and `make scheduler` enqueues due schedules.
- Subagents are child `AgentRunRecord` rows under the same local user, parent/root/session lineage, and an effective tool policy intersected from the parent run and target agent. Do not spawn subagents outside `SubAgentService` or bypass its depth, fanout, ownership, and policy checks.
- Agent data is durable and user-scoped: identities, memories, persona rules, skills, task plans, runtime sessions, transcripts, tool events, usage, runs, schedules, tool grants/audits, reflection jobs, and reflection proposals live in their owning packages under `src/voxos/`.
- Tool access is policy-driven. Respect per-agent tool grants, effective policy snapshots, and the internal/MCP tool authorization services instead of bypassing them in runtime or API code.

## Tenant Isolation And RLS

- Treat `AuthenticatedUser.user_id` as the only canonical app user ID. Request bodies, LLM/tool arguments, LiveKit room metadata, MCP tool input, and frontend-supplied IDs must never be trusted as authority for user scope.
- Use the tenant helpers in `src/voxos/db/tenant.py` for all user-owned database access. API routes should wrap the base session factory with `tenant_scoped_session_factory(current_user.user_id)`. Worker execution should set `tenant_user_context(run.user_id)` and use `context_tenant_session_factory`. Queue-wide run operations use `agent_worker_session_factory`; voice demand/lease coordination uses `voice_worker_session_factory`. After selecting an RLS-protected voice demand, re-enter the persisted demand user's tenant scope before reading LiveKit settings or vault records.
- Do not call `set_tenant_user_id` or set `app.user_id` from request payloads, tool calls, LLM output, unsigned metadata, or manually parsed tokens. The user ID must come from verified Firebase auth, a signed internal route token, or a previously persisted run, schedule, or voice-demand record already protected by RLS.
- Postgres RLS policies use transaction-local `current_setting('app.user_id', true)` and forced RLS on tenant tables. The application database role must be `NOSUPERUSER` and `NOBYPASSRLS`; superuser or bypass-RLS roles invalidate the database isolation guarantee.
- When adding a user-owned table, add an explicit `user_id` or `owner_user_id`, useful indexes for tenant access patterns, Alembic RLS policy coverage, forced RLS coverage, and focused tests that prove wrong-user reads and writes fail.

## Security And Key Store Model

- User secrets are stored through `UserSecretVaultService` in `src/voxos/credentials/`, backed by the `user_secret_vault` table.
- Secret lookup is scoped by `SecretReference`: owner user ID, provider, credential ID, and `SecretPurpose`. These fields also form authenticated encryption context, so decryption must use the same context that sealed the value.
- Supported encryption backends are local AES-GCM for development and AWS or
  Google Cloud KMS envelope encryption for deployed environments. Store
  ciphertext and encryption metadata in Postgres; do not add one cloud secret
  per user.
- Route provider API keys, MCP tokens/client secrets/private keys, OAuth state verifiers, and LiveKit credentials through the vault. Do not return plaintext secrets from APIs, persist them outside the vault, or write them to logs.
- Treat tenant-configured URLs as potentially sensitive. Log only a sanitized origin or project label; never log URL userinfo, query strings, fragments, signed URLs, or embedded credentials.
- When adding a new secret type, add or reuse a precise `SecretPurpose`, keep ownership checks near the service boundary, and add focused tests that prove wrong-user or wrong-context reads fail.
- Agent tool calls must derive authority from `AgentRunContext` via `ToolExecutionContext`. Keep `validate_llm_tool_arguments` in the execution path so LLM-supplied arguments cannot smuggle `user_id`, tokens, credentials, server IDs, headers, URLs, or other authority-bearing fields.
- Exposed MCP transports must be built with `McpTransportAuthConfig` and a verifier such as `FirebaseMcpTokenVerifier`. MCP tool implementations should resolve the local user through `AuthenticatedMcpUserResolver(require_local_user_claim=True)` and then enter `tenant_user_context`; do not expose HTTP MCP servers with a static resolver or without token verification.

## Core Rules

- Read the existing flow and contracts before changing behavior.
- Prefer reuse over duplicate business logic.
- Prefer class-based, modular, reusable code for cohesive services, clients, agents, and workflows. Keep simple stateless helpers as functions when a class would add no value.
- Follow PEP8; `make format` must pass after code changes.
- Use full typing on all new or changed functions; `make mypy` must pass after code changes.
- Prefer precise types over `Any`, raw `dict`, or loose objects.
- Use absolute imports only.
- Keep imports at module top level; no imports inside functions.
- Do not add imports to `__init__.py` files.
- Do not add mock or fake data to production code.
- Do not change public APIs unless required.
- Prefer async, non-blocking code first. Default to async implementations unless the existing interface or library constraints clearly require sync code.
- Before writing a custom client or adding a dependency, search existing project code and official package docs for an official or mature existing client. Prefer an official client with native async support when it fits the API, typing, performance, and maintenance needs.
- Use generic HTTP clients such as `httpx.AsyncClient` for direct HTTP integrations only when no suitable official or existing async client exists, or when the service has no client SDK. In those cases, prefer `httpx.AsyncClient` over `requests` and reuse module-level clients where appropriate.
- For cloud and provider SDKs, prefer official async-capable SDKs or established async wrappers such as `aiobotocore` over synchronous SDKs like `boto3`; do not hand-roll REST calls around an available async SDK without a clear contract reason.
- Keep logging actionable and safe; do not log secrets or sensitive user data.
- Keep product copy and end-user docs focused on current capabilities, prerequisites, actions, states, limits, and recovery. Do not narrate implementation history, rejected alternatives, or unused technologies unless needed to prevent a concrete setup, security, or operational error; keep architecture and tradeoff context in developer docs.

## Review Instructions

- For Codex web review tasks, act as a senior maintainer reviewing the diff for correctness, security, tenant isolation, async behavior, typing, tests, and maintainability.
- Lead with findings ordered by severity. Each finding should identify the concrete bug or risk, explain why it matters, and reference the affected file and line.
- Do not spend review space on praise, summaries, style preferences, or speculative concerns unless they point to a real maintainability or correctness problem.
- Call out missing or inadequate tests when behavior changes, especially for auth, RLS, credential handling, agent/tool policy, migrations, async workers, and API contracts.
- Treat user identity, secrets, tool authorization, and database tenancy as high-risk areas. Flag any path that trusts frontend input, LLM/tool arguments, unsigned metadata, or unscoped database access for authority.
- If no actionable issues are found, say so directly and mention the main residual risks or checks that were not run.

## Engineering Quality Bar

- Optimize for the best durable outcome, not the least amount of work. Do not choose a superficial fix when the request needs design work, tests, refactoring, or deeper investigation.
- Do not cut corners by hardcoding behavior, weakening validation, deleting checks, hiding failures, overfitting tests, adding fake production data, or leaving placeholders unless explicitly requested and clearly documented.
- Treat passing tests as necessary but not sufficient. Review changes for maintainability, security, performance, async behavior, typing, failure modes, and consistency with existing module boundaries.
- Prefer the implementation a senior maintainer would accept in review over the smallest patch that appears to satisfy the prompt.
- If a shortcut is genuinely necessary, make the tradeoff explicit in code comments where appropriate and in the final response. Include the follow-up needed to remove it.
- Before finishing, check for hidden incompleteness: TODOs, stubs, dead code, broad exception swallowing, blocking calls in async paths, duplicated logic, missing tests, and unverified assumptions.
- Do not lower the quality bar because the task seems small. Small changes should still preserve design, correctness, and future maintainability.

## Codebase Stewardship

- Keep the codebase lean. When changing behavior, improve the surrounding code quality instead of only adding another layer on top of existing logic.
- Prefer replacing or simplifying old code over adding parallel new paths, defensive conditionals, compatibility branches, or feature flags. Keep old and new behavior side by side only when there is a clear, current compatibility requirement.
- When a feature, path, setting, API, dependency, or abstraction is no longer needed, remove the related code, tests, docs, config, imports, and dependency entries in the same change where practical.
- Do not keep commented-out code, unused functions, obsolete classes, dead branches, stale flags, or placeholder adapters for future use. Git history is the archive.
- Use YAGNI: do not add hooks, abstractions, options, fallbacks, or generalized frameworks for hypothetical future requirements.
- Refactor continuously in the area being touched. Small cleanup that makes the current change clearer is part of the work, not optional polish.
- If compatibility code or a feature flag is necessary, make its purpose, owner, and removal condition clear. Do not let temporary paths become permanent.
- Removing code must preserve required behavior. Use references, tests, type checks, and targeted runtime checks to distinguish dead code from live behavior before deleting it.
- When asked to remove a feature, delete the implementation rather than disabling it with conditions unless the user explicitly asks for a soft-disable or staged rollout.

## Application Structure

- Structure code under `src/voxos/` by purpose, capability, or product area. Prefer cohesive packages such as `src/voxos/<capability>/...` over broad top-level buckets that collect unrelated code.
- Keep module-specific models, schemas, services, clients, repositories, routers, tools, prompts, resources, and workflow code inside the package for that module when they are not genuinely shared.
- Keep entrypoints thin. FastAPI app factories, LiveKit worker entrypoints, MCP server registration, and command scripts should compose modules rather than contain business logic.
- For FastAPI code, use module-owned `APIRouter` instances and include them from the app composition layer. Route handlers should validate input, call services, and return typed schemas.
- For MCP code, separate server setup from capability implementations. Keep tools, resources, prompts, and their schemas close to the domain they expose.
- For LiveKit and agent code, isolate agent classes, tasks, handoffs, toolsets, prompts, and session orchestration by workflow or capability. Avoid one shared agent file that grows unrelated behavior.
- Prefer module-local service classes and typed request/response models over passing loose dictionaries between layers.
- Keep abstractions simple and purposeful. Do not overengineer patterns, base classes, registries, or frameworks before the code has a real reuse or complexity problem.
- Put truly cross-cutting concerns in clearly named shared packages such as `src/voxos/core/`, `src/voxos/config/`, or `src/voxos/shared/`. Do not move code there just to avoid thinking about ownership.
- Keep dependencies flowing inward: shared utilities must not import feature modules, and feature modules should not reach into each other's internals. If behavior is reused, extract a small shared abstraction.
- Mirror important module boundaries in tests so feature behavior can be tested without booting the whole application.
- Do not use `__init__.py` re-exports to hide module structure; import from explicit modules.

## Design Principles

- Follow SOLID principles where they improve clarity, testability, and maintainability. Apply them pragmatically; do not force abstractions, inheritance, or patterns where simple code is clearer.
- Single Responsibility: keep each class, service, module, and function focused on one reason to change.
- Open/Closed: make common variation points extensible with small strategies, protocols, or configuration instead of repeatedly editing fragile conditionals.
- Liskov Substitution: subclasses and protocol implementations must preserve the behavior promised by their base type or interface.
- Interface Segregation: prefer small, purpose-specific protocols or interfaces over large base classes that force callers to depend on methods they do not use.
- Dependency Inversion: high-level services should depend on abstractions for external systems such as LLMs, HTTP APIs, AWS, storage, and MCP/LiveKit integrations. Inject those dependencies where it improves tests and avoids hidden global state.

## Performance And Dependency Choices

- Treat performance as a design constraint, especially for serialization, validation, network calls, startup time, hot loops, and large data processing.
- Prefer mature, well-maintained high-performance packages when they fit the existing contracts. Benchmark or clearly justify dependency changes in hot paths.
- Prefer `orjson` over the standard `json` module for application JSON serialization and deserialization. Use `json` only when compatibility, simplicity, or a third-party API specifically requires it.
- Prefer Pydantic v2 and `pydantic-core` for typed validation and parsing instead of ad hoc validation logic.
- Keep using Rust-backed project tooling such as `uv` and Ruff for dependency management, linting, and formatting.
- For tabular or columnar data processing, consider `polars` before `pandas` or custom Python loops.
- For tokenizer-heavy NLP or LLM work, prefer Hugging Face fast tokenizers or the `tokenizers` package over pure-Python tokenization.
- For file watching and reload workflows, prefer `watchfiles` over polling or custom watcher loops.
- For ASGI serving performance, consider `granian` only after benchmarking and checking deployment compatibility. Do not change the current server stack just because a faster server exists.
- Do not add Rust-backed packages just because they are fast. Check API fit, async compatibility, Python 3.12 support, Linux amd64 wheels, maintenance quality, and operational impact first.

## Useful Makefile Debug Targets

- Local server: `make run`, `make run-prod`.
- Setup and dependencies: `make install`, `make install-dev`, `make update-requirements`, `make check-outdated`.
- Quality checks: `make lint`, `make format`, `make mypy`, `make type-check`, `make validate`.
- Workers: `make worker` for the demand-driven LiveKit project supervisor, `make worker-runs` for durable agent runs, and `make scheduler` for due schedule enqueueing. Never add or ask for LiveKit project credentials as worker environment variables.
- Targeted tests: `uv run pytest <targeted tests only>`.

Before running a Makefile target you have not used before, read its recipe. Some targets mutate real infrastructure or production data.

## Production Safety

- Do not deploy, sync production data, or run one-off production jobs unless explicitly requested.
- Prefer dry-run, status, log, and plan variants where they exist.

## Feature And Bug Work

- Reproduce bugs first with a targeted test or a clear failing scenario.
- Fix root causes, not symptoms.
- Add or update focused tests close to the changed behavior.
- Keep changes surgical; avoid unrelated refactors.
- For best-effort exception paths, preserve traceback context with `logger.exception(...)`.
- Preserve existing domain conventions by reusing canonical helpers and services for the area you are touching.

## Database Migrations

- Use Alembic autogenerate to create schema migrations from model changes. Do not hand-write migrations from scratch unless custom operations are required.
- Always review autogenerated migrations before committing them. Check for missing renames, unsafe drops, incorrect nullable/default changes, index changes, and data migration needs.
- If custom migration code is needed, start from the autogenerated revision, make the minimal manual edits, and ensure the migration still reflects the current model metadata.
- After editing a migration manually, verify autogenerate is not left out of sync with the models. A follow-up autogenerate check should produce no unexpected schema diff.

## Validation

After every change set, run the relevant focused checks, then run the complete
repository gate before handing work back:

```bash
make pre-commit
```

`make pre-commit` is the canonical final gate. It runs pre-commit across tracked
files and then runs the repository secret scan across both tracked and
untracked files. Do not replace it with `pre-commit run --all-files`, do not set
`SKIP=detect-secrets` yourself, and do not assume an untracked file was checked
unless the final `make pre-commit` passed. If a hook changes files, review the
changes and rerun `make pre-commit` until it exits cleanly.

For code changes, run the focused checks you need before that final gate:

```bash
make format
make mypy
uv run pytest <targeted tests only>
```

For docs-only changes, read the edited file back and run applicable docs checks
before the same final `make pre-commit` gate.

## Zen Of Python

```text
Beautiful is better than ugly.
Explicit is better than implicit.
Simple is better than complex.
Complex is better than complicated.
Flat is better than nested.
Sparse is better than dense.
Readability counts.
Special cases aren't special enough to break the rules.
Although practicality beats purity.
Errors should never pass silently.
Unless explicitly silenced.
In the face of ambiguity, refuse the temptation to guess.
There should be one-- and preferably only one --obvious way to do it.
Although that way may not be obvious at first unless you're Dutch.
Now is better than never.
Although never is often better than *right* now.
If the implementation is hard to explain, it's a bad idea.
If the implementation is easy to explain, it may be a good idea.
Namespaces are one honking great idea -- let's do more of those!
```

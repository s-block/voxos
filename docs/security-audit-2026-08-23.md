# Voxos Pre-Deployment Security Audit

Date: 2026-08-23
Repository: `voxos`
Branch: `main`
Reviewed commit: `67ccac812984`
Status: pre-deployment security review; remediation is in progress

Archived deployment note (2026-08-26): the Google Cloud Run and Terraform
deployment reviewed in this document has been removed from the repository.
References to those deleted files describe the historical reviewed revision,
not the current application or deployment instructions.

## Remediation update — 2026-08-24

The first outbound-endpoint hardening change is implemented and verified. MCP
and OpenAI-compatible requests now share a policy that requires unregistered
destinations to use HTTPS on port 443 and globally routable DNS, rejects URL
credentials and fragments, disables redirects, and permits local/private
destinations only through exact deployment-owned registrations. Text-provider
base URLs also reject queries. Separate private Cloud Run targets use a
configured `run.app` audience, Google identity tokens in
`X-Serverless-Authorization`, and optional target-level `roles/run.invoker`
Terraform bindings. Local Docker, host, and same-instance sidecar URLs remain
available through explicit exact registrations.

This closes the demonstrated redirect path in SEC-02 and SEC-03 and removes
hostname-wide local trust. SEC-03 remains partially open because application
DNS validation and the HTTP client's connection lookup are not cryptographically
pinned to one resolution. SEC-05 also remains open until deployment-level
egress and metadata/internal-network containment are enforced. The overall
**do not deploy** decision remains unchanged while the other blockers are open.

## Database remediation update — 2026-08-24

The repository now separates the pooled runtime database URL from a direct
migration-only URL. Terraform grants the migration service account access only
to the migration secret, requires numeric Secret Manager versions, and forces
Cloud Run database clients to use certificate and hostname verification. The
Alembic path creates the required `vector` extension before schema migrations.
Local Docker retains driver-default transport behavior. A read-only runtime
role gate now rejects superuser/admin/RLS-bypass flags, owner-role membership,
application-object ownership, schema creation, Alembic mutation, and disabled
row security before a credential is approved for deployment.

The current Neon target is now migrated to the repository head with 62
application tables, 58 forced-RLS tenant tables, 75 policies, and pgvector.
Its pooled runtime login is separate from the direct migration/admin login and
passes the runtime role gate: it is non-admin, `NOBYPASSRLS`, owns no tables,
cannot create in the application schema, cannot mutate Alembic state, and
cannot disable RLS. Eighteen tenant/worker RLS integration tests passed through
the pooler. The backend-lock observation test passed through the direct
endpoint because a transaction pooler cannot provide a stable backend PID for
that test's `pg_stat_activity` assertion. Both disposable test databases were
removed.

SEC-01 is closed for this Neon target, and the TLS-enforcement portion of
SEC-11 is closed. SEC-11 remains a deployment blocker until Neon network,
backup/PITR, restore, monitoring, and credential-rotation controls are
confirmed in the provider console.

## Executive decision

**Do not deploy the current configuration to production yet.**

The review did not find a direct unauthenticated bypass of an ordinary product API, a demonstrated cross-tenant object reference vulnerability, plaintext secret storage, or a Terraform resource that directly makes the database or Secret Manager public. The application has unusually strong foundations in several high-risk areas: Firebase token validation, tenant-scoped database helpers, forced row-level security (RLS), encrypted user secrets, signed OAuth state, provider-signed carrier webhooks, signed LiveKit routing, and recursive tool-authority validation.

The release is nevertheless blocked by several composable control failures:

1. Application URL validation now blocks the demonstrated redirect/private-address paths, but DNS validation is not pinned to the HTTP connection and deployment-level egress does not independently block metadata/private destinations.
2. The API and two worker types share a service account with access to every configured runtime secret, KMS decrypt, FCM administration, and job invocation.
3. Request, webhook-body, WebSocket-frame, per-user work, and provider-spend limits are incomplete. A public, self-service identity can therefore turn expensive features into an availability or cost attack.
4. The optional browser service describes `read_only` in a model prompt but does not enforce it at the action boundary. Its origin guard covers top-level document navigation, not all subframes and resource traffic.
5. Neon network restrictions, backups/PITR, restore behavior, monitoring, and credential-rotation controls remain unverified in the provider console.

These are not merely scanner findings. Together they create plausible compromise chains across tenant-controlled networking, cloud workload authority, and database ownership. The first five deployment blockers in this report should be closed and retested before production data is introduced.

No review can establish that software is “fully secure.” This report states what was inspected, what was exercised, what was found, and what still requires validation in the real Google Cloud, database, Firebase, DNS, and frontend-hosting environments.

## Risk summary

| ID | Severity | Confidence | Finding | Deployment status |
| --- | --- | --- | --- | --- |
| SEC-01 | High | High | Runtime database role owns the database, schema, and tables | Blocker |
| SEC-02 | High | High | OpenAI-compatible endpoints permit redirect-based SSRF | Blocker |
| SEC-03 | High | High | MCP redirects and DNS rebinding bypass the initial public-host check | Blocker |
| SEC-04 | High | High | Shared runtime service account and secret fan-out create a large blast radius | Blocker |
| SEC-05 | High | High | No deployment-level egress or metadata/internal-network containment | Blocker |
| SEC-06 | Medium; High when enabled | High | Browser `read_only` and origin isolation are not complete enforcement controls | Keep disabled |
| SEC-07 | Medium | High | Rate, body, stream, concurrency, and spend controls are incomplete | Blocker before public launch |
| SEC-08 | Medium | High | Firebase token revocation checking is disabled by default | Fix before sensitive production use |
| SEC-09 | Medium | High | Production dependency advisories are present | Upgrade before deployment |
| SEC-10 | Medium | High | Security-critical subprojects and Terraform policy are missing from CI coverage | Fix before deployment |
| SEC-11 | Medium / unverified | High | Production database TLS, network, backup, and privilege controls are external to Terraform | Prove before deployment |
| SEC-12 | Medium / unverified | Medium | Frontend hosting headers are not defined; local token persistence raises XSS impact | Prove/harden before launch |
| SEC-13 | Medium | High | Image immutability is advisory only and CI builds a different, root-running image | Fix supply-chain path |
| SEC-14 | Low / control-dependent | High | State logging/retention and Artifact Registry CMEK are not expressed | Decide and document |
| SEC-15 | Low | High | Local attachment storage is an unsafe production setting if image input is enabled | Keep disabled or replace |
| SEC-16 | Low | High | API schema and interactive documentation are public | Explicit production decision |
| SEC-17 | Low | High | Android retains legacy external-storage access | Remove if no longer required |
| SEC-18 | Informational / known | High | Codex runtime remains a high-trust Docker execution feature | Keep disabled pending separate review |

## Scope and threat model

The review covered:

- FastAPI composition, all API routers, route dependencies, callbacks, public webhooks, media WebSockets, client bootstrap, and OpenAPI exposure.
- Firebase verification, local-user mapping, tenant-session construction, RLS migrations, worker service scopes, and a running local PostgreSQL role/ownership inspection.
- User secret vault, encryption context, OAuth state and PKCE, MCP credential delegation, provider credentials, attachment handling, LiveKit routing, Twilio, Telnyx, WhatsApp, push notifications, agent tools, subagents, schedules, and durable workers.
- React frontend, Flutter client, local browser-agent service, WhatsApp bridge, Markdown/link rendering, token persistence, Android permissions, and browser-origin policy.
- Cloud Run services and jobs, Cloud Scheduler, service accounts, IAM, Secret Manager, KMS, Terraform state, Artifact Registry, container builds, GitHub Actions, Dependabot, and Terraform apply workflow.
- Locked Python and JavaScript dependencies, static security analysis, IaC analysis, secret scanning, type checks, builds, and test suites.

Primary protected assets are cross-tenant conversations and memories, attachments, contacts and integration data, provider API keys, MCP OAuth and private-key material, LiveKit/Twilio/Telnyx credentials, browser credentials, WhatsApp session state, push tokens, signing keys, and the cloud/database control plane.

Threat actors considered are an unauthenticated internet client, a malicious or compromised authenticated tenant, a stolen Firebase token, a malicious MCP/model endpoint, a compromised carrier or browser page, prompt injection in untrusted web content, a vulnerable dependency, compromised application code, and a CI/CD or image-supply-chain attacker.

The review did not assume that an authenticated tenant is trusted. This is important because Voxos accepts tenant-selected network destinations and executes expensive or authority-bearing workflows for that tenant.

## Authentication and ingress review

The instantiated FastAPI application contains 143 HTTP operations and three WebSocket routes. Of the HTTP operations, 132 have a direct Firebase dependency. The remaining routes were reviewed individually.

| Ingress class | Authentication/authorization result |
| --- | --- |
| Ordinary agents, chats, settings, runs, schedules, contacts, integrations, MCP configuration, voice, attachments, and browser-account APIs | Firebase bearer authentication is attached; tenant authority derives from the durable local `AuthenticatedUser.user_id` |
| `GET /health` | Intentionally public; returns only a static status |
| `GET /api/client-config` | Intentionally public; contains client bootstrap configuration rather than secrets |
| MCP OAuth callbacks | Public by protocol, but state is signed, persisted, tenant-bound, expires, is single-use, and binds the PKCE verifier stored in the vault |
| Internal MCP credential broker | Uses a short-lived signed delegation assertion with audience, subject, server, provider, JTI, issued-at and expiry checks; body is capped and response is `no-store` |
| Twilio and Telnyx HTTP webhooks | Public by protocol; route keys are stored hashed and processing verifies provider signatures plus tenant/integration/resource/call bindings |
| Carrier media WebSockets | Public by protocol; route context, stream token/provider data, tenant, integration, resource, and call session are bound before media bridging |
| Scheduler service | No application token, but Terraform grants Cloud Run invocation only to the scheduler service account |
| Browser service | No application bearer token, but Terraform grants Cloud Run invocation only to the Voxos runtime service account; the browser service account has no secret grants |
| `/docs`, `/redoc`, `/openapi.json` | Public because FastAPI production documentation is not disabled |

Read-only unauthenticated probes against the local API checked 47 GET operations. All ordinary operations returned `401`; health/client configuration returned `200`; unsigned MCP callbacks failed. A mutation sweep checked 90 POST/PUT/PATCH/DELETE operations with empty or dummy bodies. All ordinary operations returned `401`. Dummy carrier webhook requests did not bypass their route/signature controls, and the internal broker rejected an unsigned request.

This is strong evidence that there is no simple missing Firebase dependency in the current route composition. It is not a substitute for black-box testing after Cloud Run, the load balancer, custom domains, Firebase, and carrier callback URLs are deployed.

## Detailed findings

### SEC-01 — Runtime database role owns the database, schema, and tables

Severity: **High**
Confidence: **High**
Standards: OWASP ASVS V4 access control, CWE-250 (unnecessary privileges), CWE-269 (improper privilege management), CIS Control 6, NIST SSDF PW.7

Remediation status: **Closed for the current Neon deployment target on
2026-08-24.** The evidence below records the pre-remediation state.

Evidence:

- `docker/pgvector/init/000-create-app-role.sh:35-51` correctly removes superuser, role-creation, database-creation, replication, and `BYPASSRLS`, but then makes the application role the database owner and public-schema owner and grants schema `CREATE`.
- `terraform/cloud-run/locals.tf:42-45` selects the same `DATABASE_URL` reference used by the runtime for migrations.
- `terraform/cloud-run/jobs.tf:31-50`, `jobs.tf:92-111`, and `jobs.tf:152-162` inject the runtime secret map into agent/voice jobs and the same database secret into the migration job.
- `terraform/cloud-run/services.tf:48-59` injects every runtime secret into the API.
- A read-only inspection of the running local Postgres instance confirmed that the application role is non-superuser and `NOBYPASSRLS`, but owns the database and all 62 application tables and can create in the application schema. Fifty-eight tenant tables have RLS enabled and forced.

Why this matters:

Forced RLS is effective for normal table-owner queries, but ownership still carries DDL authority. PostgreSQL documents that table owners control whether row security is enabled and define its policies. An SQL injection capable of arbitrary statements, unsafe migration execution, or application process compromise can therefore disable or replace RLS and read every tenant’s data. The current design also prevents the migration service account from being meaningfully safer than the runtime service account because both receive the same database principal.

No SQL injection was found in this review. The finding concerns the containment boundary after a runtime compromise—the exact boundary RLS is expected to protect in a sensitive multi-tenant service.

Required remediation:

1. Create a dedicated migration/owner role and a separate runtime login role. The migration role should own schema objects and be used only by the migration job.
2. Revoke schema `CREATE`, table ownership, policy/DDL privileges, and migration-table mutation from the runtime role. Grant only the required DML and sequence/function privileges.
3. Consider separate API, agent-worker, voice-worker, scheduler, and webhook database logins if their access patterns differ. Keep the existing transaction-local tenant/service context and forced RLS.
4. Use a separate Secret Manager reference for the migration database URL. Do not inject it into application services.
5. Add an automated deployment test proving the runtime role cannot `ALTER TABLE`, disable RLS, create/drop policies, create tables in the application schema, or update `alembic_version`.

Verification:

- Assert no application table is owned by a runtime login.
- Assert runtime roles are `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEROLE`, `NOCREATEDB`, have no schema `CREATE`, and cannot execute representative DDL.
- Rerun all wrong-tenant RLS tests using each production-equivalent runtime role, not the migration owner.

Reference: [PostgreSQL row security policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html).

### SEC-02 — OpenAI-compatible endpoints permit redirect-based SSRF

Severity: **High**
Confidence: **High**
Standards: OWASP API7:2023 SSRF, OWASP ASVS V5 input validation, CWE-918

Evidence:

- `src/voxos/api/text_settings.py:115-127` accepts an arbitrary non-empty `base_url` without scheme, IP, hostname, port, credential, or network policy.
- `src/voxos/api/text_settings.py:374-445` lets any authenticated tenant persist the endpoint.
- `src/voxos/text/providers.py:205-216` and `providers.py:243-254` make server-side POST requests and enable redirects for the OpenAI-compatible provider.
- `tests/text/test_providers.py:537-601` intentionally permits HTTP/custom hosts and verifies that a redirect to a different host is followed.

Exploit scenario:

An authenticated tenant configures a public endpoint that returns a redirect to a localhost, metadata, VPC, database-adjacent, or other internal URL. The backend validates neither the initial destination nor the redirect target. Even where the forced OpenAI request body and streaming parser prevent convenient response exfiltration, the tenant gains a blind network/action oracle and can target internal services that accept compatible POSTs. DNS rebinding can also change a hostname’s address after configuration.

The absence of repository-defined egress controls and the runtime’s broad service-account/database authority significantly raises the impact of any SSRF escalation.

Required remediation:

1. Decide whether production truly needs arbitrary compatible endpoints. Prefer a curated provider allowlist or an administrator-approved endpoint registry.
2. If arbitrary public endpoints remain, require HTTPS, reject URL credentials/fragments, normalize ports, resolve every hostname, reject non-global and cloud-metadata destinations, and pin the connected address.
3. Disable redirects. If redirects are required, reapply the full policy on every hop, cap the hop count, prevent HTTPS downgrade, and strip sensitive headers cross-origin.
4. Enforce an outbound proxy/firewall policy independently of application validation.
5. Add tests for IPv4/IPv6 private ranges, link-local, loopback, encoded IP forms, localhost suffixes, redirects, DNS rebinding, multiple A/AAAA answers, scheme downgrade, and metadata endpoints.

Reference: [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html).

### SEC-03 — MCP redirects and DNS rebinding bypass the initial public-host check

Severity: **High**
Confidence: **High**
Standards: OWASP API7:2023 SSRF, CWE-918, CWE-441

Evidence:

- `src/voxos/mcp/url_policy.py:19-43` validates the configured URL and resolves its hostname once, rejecting non-global addresses at that moment.
- `src/voxos/mcp/client.py:93-132` and `client.py:162-217` validate first and then pass the original hostname to the MCP SDK, which resolves it again.
- The installed MCP SDK’s `mcp/shared/_httpx_utils.py:68-81` creates HTTPX clients with `follow_redirects=True`.
- `src/voxos/mcp/models.py:98-127` permits any syntactically valid HTTP header name for API-key authentication, including authority-bearing cloud headers.
- `src/voxos/mcp/auth_strategies.py:179-196` sends the configured header on the MCP request.

Exploit scenario:

A tenant-controlled public MCP server redirects to an internal destination after passing the initial check, or a hostname returns public addresses during validation and a private address during the client connection. The SDK does not revalidate the redirect. This enables blind probing and potentially state-changing requests against internal services. Custom non-authorization headers increase the set of internal protocols that can be reached.

Direct cloud-token extraction was not demonstrated: MCP response parsing and generic error handling limit convenient disclosure of arbitrary internal responses. This limitation reduces confidence in the most severe exploit outcome but does not remove the SSRF primitive or its action-oriented impact.

Required remediation:

1. Provide the MCP SDK with a hardened HTTP client/transport that disables redirects or validates every hop.
2. Eliminate the DNS time-of-check/time-of-use gap through connection-time address enforcement, DNS pinning, or a controlled egress proxy.
3. Deny metadata-specific, hop-by-hop, host/routing, forwarding, and other authority-bearing API-key header names. Prefer known provider-specific headers.
4. Block metadata and private/VPC destinations at the network layer.
5. Apply the same connect-time policy to OAuth authorization, token, and dynamic registration endpoints.

Verification should use an attacker-controlled test server capable of redirecting, changing DNS answers, returning mixed public/private records, and attempting requests to a fake metadata service.

### SEC-04 — Shared runtime service account and secret fan-out create a large blast radius

Severity: **High**
Confidence: **High**
Standards: OWASP ASVS V14 configuration, CWE-250, CIS Controls 5 and 6, NIST CSF PR.AA

Evidence:

- `terraform/cloud-run/iam.tf:1-5` defines one runtime service account.
- `terraform/cloud-run/services.tf:9-10` attaches it to the API; `terraform/cloud-run/jobs.tf:12-14` and `jobs.tf:73-75` attach it to agent and voice jobs.
- `terraform/cloud-run/iam.tf:31-38` grants that identity access to every secret listed in the runtime map.
- `terraform/cloud-run/iam.tf:64-74` grants KMS encrypt/decrypt and project-level FCM administration. The FCM role is created even when push is disabled.
- `terraform/cloud-run/services.tf:48-59` and `terraform/cloud-run/jobs.tf:39-50,100-111` inject every runtime secret into multiple workloads whether or not each workload needs it.

Impact:

A vulnerability in the public API, an agent tool path, or the long-running voice worker can expose all signing secrets and the database credential, decrypt tenant vault ciphertext when combined with database access, administer FCM, and invoke jobs. Shared identity also makes audit attribution weaker.

Required remediation:

1. Create single-purpose service accounts for API, agent worker, voice worker, scheduler, notification delivery, browser invocation, and migration.
2. Replace the single secret map with per-workload secret maps and resource-level IAM grants.
3. Grant KMS decrypt only to workloads that must resolve tenant secrets.
4. Replace FCM Admin with the narrowest send-only/custom role and create it only when push is enabled.
5. Separate job-launch permissions from data-plane services unless they genuinely launch that job.
6. Enable and retain Data Access audit logs for Secret Manager, KMS, IAM Credentials, and sensitive storage/database services.

References: [Google service-account security best practices](https://docs.cloud.google.com/iam/docs/best-practices-service-accounts) and [Secret Manager best practices](https://docs.cloud.google.com/secret-manager/docs/best-practices).

### SEC-05 — No deployment-level egress or metadata/internal-network containment

Severity: **High**
Confidence: **High**
Standards: OWASP API7:2023 SSRF, CIS Control 13, NIST CSF PR.IR

Evidence:

- The Cloud Run Terraform has no VPC connector/direct-VPC egress policy, firewall policy, secure web proxy, NAT allowlist, DNS policy, or equivalent repository-defined egress boundary.
- MCP, OpenAI-compatible providers, OAuth endpoints, browser automation, carrier providers, and other integrations intentionally make outbound network requests using tenant-influenced configuration.
- `README.md:409` already describes blocking metadata and private/VPC destinations as an operational requirement, but Terraform does not implement or assert it.

Impact:

Application URL validation remains the only barrier between tenant-controlled destinations and internal/cloud network targets. It cannot reliably stop redirects, DNS rebinding, parser differences, or future outbound clients. Cloud Run service identity makes metadata/internal service reachability security-sensitive.

Required remediation:

1. Route applicable service egress through a controlled VPC/proxy path with explicit allow/deny policy.
2. Block metadata, link-local, RFC1918, unique-local IPv6, loopback, and internal DNS zones except narrowly documented integrations.
3. Separate browser egress from API/worker egress. Browser sessions need a different policy from model/MCP API calls.
4. Monitor denied egress and destination anomalies without logging tenant credentials or signed URLs.
5. Verify the effective policy from each Cloud Run service and job using production service identities.

References: [Cloud Run security overview](https://docs.cloud.google.com/run/docs/securing/security) and [Cloud Run service identity](https://docs.cloud.google.com/run/docs/configuring/services/service-identity).

### SEC-06 — Browser `read_only` and origin isolation are not complete enforcement controls

Severity: **Medium in the default Terraform configuration; High if the browser service is enabled**
Confidence: **High**
Standards: OWASP ASVS V4 access control, CWE-602, CWE-693

Evidence:

- `terraform/cloud-run/variables.tf:109-112` disables the browser service by default, which currently prevents production exposure through this module.
- `browser-agent/src/task_policy.ts:3-26` expresses read-only behavior only in the model system prompt.
- `browser-agent/src/server.ts:136-150` passes the task to the Stagehand agent without an action interceptor that rejects state-changing DOM/tool operations.
- `browser-agent/src/navigation_guard.ts:173-220` intercepts only top-level `Document` navigation. Subframes, scripts, images, fetch/XHR, WebSockets, and other resource traffic are not origin-contained.

Impact:

Prompt injection in webpage content, model error, or an ambiguous action can submit forms, send messages, change account state, or disclose browser-account data despite `read_only`. An approved top-level page can load or communicate with unapproved origins, weakening the claimed origin boundary.

Positive containment is present: the browser Cloud Run service is IAM-private, uses a separate service account with no secret grants, has concurrency one and bounded task sizes, and is disabled by default.

Required remediation before enabling:

1. Enforce read-only at the browser action/tool layer. Reject click/type/submit/download/upload/navigation actions according to a concrete policy, not model intent alone.
2. Add confirmation/transaction boundaries for consequential actions in write mode.
3. Apply origin/egress restrictions to frames and network requests, with an explicit policy for required CDNs and authentication origins.
4. Prevent secrets from being entered into origins other than the credential’s declared target.
5. Add adversarial prompt-injection, hidden-form, cross-origin iframe, service-worker, WebSocket, download/upload, and login-redirect tests.

### SEC-07 — Rate, body, stream, concurrency, and spend controls are incomplete

Severity: **Medium**
Confidence: **High**
Standards: OWASP API4:2023 Unrestricted Resource Consumption, CWE-400, CWE-770

Evidence:

- `src/voxos/api/main.py:153-160` installs CORS but no global/per-route rate limiter or request-size middleware.
- `src/voxos/api/integrations.py:955-959` reads the entire Telnyx body before signature processing; `integrations.py:1057-1060` does the same for Twilio form bodies.
- `src/voxos/integrations/providers/twilio/media_streams.py:400-431` and `telnyx/media_streams.py:544-581` repeatedly receive and parse text frames without an application frame-size, byte-rate, frame-rate, or aggregate-duration budget.
- `terraform/cloud-run/services.tf:9-17` allows one public API instance, 40 concurrent requests, and one-hour requests. The low default max-instance count limits cost but also makes exhaustion of the entire service easier.
- Expensive authenticated features include LLM streaming, images, agents, browser tasks, carrier workflows, and job launches. Self-service Firebase identity means authentication alone is not an abuse-control boundary.

Positive controls include bounded Pydantic fields, a 25 MB attachment-source cap plus image pixel/decompression budgets, a 32 KB browser request cap, a 1 KB credential-broker body cap, worker concurrency settings, and browser task limits.

Required remediation:

1. Enforce request-byte limits at the edge and in application code before buffering or parsing.
2. Add per-IP unauthenticated limits and per-user/per-tenant authenticated quotas for expensive endpoints.
3. Add concurrent-run, token, image, browser-step, carrier-duration, and monetary spend budgets with atomic enforcement.
4. Bound WebSocket frame size, frames/second, bytes/second, idle time, call duration, and aggregate bytes.
5. Bound provider response sizes and stream durations.
6. Add abuse alerts and cost-budget alerts; return consistent `429`/`413` responses without revealing tenant existence.

Reference: [OWASP API4:2023 Unrestricted Resource Consumption](https://owasp.org/API-Security/editions/2023/en/0xa4-unrestricted-resource-consumption/).

### SEC-08 — Firebase token revocation checking is disabled by default

Severity: **Medium**
Confidence: **High**
Standards: OWASP ASVS V3 session management, CWE-613

Evidence:

- `src/voxos/auth/settings.py:41-44` defaults `FIREBASE_AUTH_CHECK_REVOKED` to false.
- `src/voxos/auth/firebase.py:83-91` performs the revocation check only when the checker is configured.
- The Cloud Run Terraform does not set the option to true.

The base verifier is strong: RS256, required claims, key ID, issuer, audience, issued-at, auth-time, expiry, subject, and bounded clock skew are validated. The gap is that a stolen token or a token for a disabled/revoked user remains accepted until normal token expiry.

Required remediation:

1. Enable revocation checking for sensitive production routes or all requests, with a carefully bounded/cache-aware implementation.
2. Define account disable, incident-response revocation, and reauthentication behavior.
3. If the service is not intended for unrestricted public sign-up, enforce the product’s admission policy (allowlist/organization/verified email as appropriate). This is a product decision, not an assumed requirement.
4. Require recent authentication or step-up/MFA for exporting data, changing credentials, enabling external integrations, and other high-impact operations.

Reference: [Firebase session and token revocation](https://firebase.google.com/docs/auth/admin/manage-sessions).

### SEC-09 — Production dependency advisories are present

Severity: **Medium**
Confidence: **High for package presence; Medium for exploit reachability**
Standards: OWASP A06:2021 Vulnerable and Outdated Components, CWE-1104, NIST SSDF RV.1

Python environment audit found seven advisories in three installed production packages:

| Package | Installed | Advisory | Fixed version |
| --- | --- | --- | --- |
| `aiohttp` | 3.14.1 | PYSEC-2026-3545 | 3.14.3 |
| `aiohttp` | 3.14.1 | PYSEC-2026-3546 | 3.14.2 |
| `aiohttp` | 3.14.1 | PYSEC-2026-3547 | 3.14.2 |
| `cryptography` | 48.0.1 | PYSEC-2026-3552 | 50.0.0 |
| `cryptography` | 48.0.1 | PYSEC-2026-3553 | 49.0.0 |
| `cryptography` | 48.0.1 | PYSEC-2026-3554 | 49.0.0 |
| `h2` | 4.3.0 | PYSEC-2026-3628 | 4.4.1 |

Reachability review did not show `aiohttp` or `h2` serving the public FastAPI API; Cloud Run terminates inbound HTTP and Uvicorn serves the app. The vulnerable `cryptography` features identified by the advisories are not the main AES-GCM/Ed25519 operations used by the vault and Telnyx code. These facts lower immediate exploit confidence but do not justify shipping known-vulnerable versions in a sensitive service.

The browser-agent production audit reports 17 low-severity dependency paths rooted in `GHSA-866g-f22w-33x8` (`@ai-sdk/provider-utils` uncontrolled resource consumption). The available automatic resolution is a breaking major upgrade and needs targeted browser-agent regression tests.

Frontend production dependencies and WhatsApp production dependencies report zero advisories. The frontend’s full development audit reports three build/test-only packages with advisories (`nanoid`, `postcss`, and `undici`); `npm explain` confirms they are dev dependencies through Vite/jsdom. Flutter static analysis passed, but no authoritative Dart/Flutter advisory audit was available in the local toolchain; several direct and transitive packages have newer versions.

Required remediation:

1. Upgrade and lock `aiohttp`, `cryptography`, and `h2` to fixed compatible versions; rerun the full Python suite and provider/integration tests.
2. Plan the browser AI SDK major upgrade; add adversarial resource-limit tests before merging it.
3. Upgrade the frontend build/test chain so development advisories do not remain in CI tooling.
4. Add an always-on, keyless locked-production dependency audit to CI. Do not rely only on the optional Safety job.

### SEC-10 — Security-critical subprojects and Terraform policy are missing from CI coverage

Severity: **Medium**
Confidence: **High**
Standards: NIST SSDF PO.3, PW.7 and RV.1; OWASP SAMM Verification

Evidence:

- `.github/workflows/ci.yml` validates Python and WhatsApp, but has no frontend, browser-agent, or Flutter lint/type/build/test job.
- `.github/dependabot.yml` includes root Python, frontend, WhatsApp, Actions, Docker, Compose, and pre-commit, but omits browser-agent and Flutter.
- `.github/workflows/ci.yml:301-302` explicitly excludes Terraform from Semgrep; `ci.yml:311-320` skips Terraform in Trivy. There is format/validate coverage but no Checkov/tfsec-equivalent policy gate.
- `.github/workflows/ci.yml:252-284` silently skips Safety if an API key is missing. The independent audit in this review found current Python advisories.
- `.github/workflows/ci.yml:17-20` grants `packages: write` at workflow scope rather than only to the image-publish job.
- `.github/workflows/terraform-apply.yml:76-90` creates and applies a plan in the same job after a single environment gate. It does not require a separately retained/reviewed plan or run an IaC security policy check in the apply path.
- CI builds the root `Dockerfile`, not `Dockerfile.cloud-run`; see SEC-13.

One frontend full-suite run failed because `OperationsPage.test.tsx` could not find a browser-account option. The same file passed in isolation. A complete rerun result is recorded in the validation section. This indicates test-order or shared-state contamination even if the rerun passes.

Required remediation:

1. Add locked-install lint, typecheck/analyze, unit-test, production-build, and production dependency-audit jobs for frontend, browser-agent, and Flutter.
2. Add Dependabot or an equivalent update process for browser-agent and Flutter.
3. Add Checkov/tfsec policy scanning and secret-sensitive Terraform plan review to PR and apply workflows.
4. Make a keyless `pip-audit`/OSV scan mandatory; keep Safety as an additional source if desired.
5. Restrict `packages: write` to the publish job and apply least-privilege permissions per job.
6. Split plan and apply so the exact saved plan, commit, images, variables, and policy results receive approval.
7. Verify the external Workload Identity Federation trust condition restricts repository, owner, branch/environment, and workflow identity; that policy is not in this repository.

### SEC-11 — Production database TLS, network, backup, and privilege controls are external to Terraform

Severity: **Medium / unverified deployment blocker**
Confidence: **High that controls are not represented; unknown effective cloud posture**

Remediation status: verified TLS, role separation, runtime denial checks, and
production-equivalent RLS tests now pass. Provider-side network, backup/PITR,
restore, monitoring, and rotation evidence remains open.

Evidence:

- This Terraform does not provision the production PostgreSQL instance, private network, firewall, TLS certificate policy, backups, point-in-time recovery, database audit logging, or database IAM.
- `src/voxos/db/settings.py:14-44` accepts `DATABASE_URL` and does not enforce TLS or certificate verification parameters.
- The secret payload is intentionally outside Terraform, so the actual host, network route, TLS mode, role grants, and provider settings were not inspectable.

Before deployment, produce evidence for all of the following:

- Private connectivity or a tightly scoped IP allowlist; no public database exposure.
- TLS with hostname and certificate verification, not opportunistic encryption.
- Separate migration-owner and runtime roles as required by SEC-01.
- Encryption at rest, encrypted backups, point-in-time recovery, tested restoration, retention, deletion protection, and regional/availability design appropriate to the data.
- Connection limits, statement/idle timeouts, monitoring, slow-query/audit strategy, credential rotation, and incident response.
- A production-equivalent RLS test run with each runtime role.

### SEC-12 — Frontend hosting headers are not defined; local token persistence raises XSS impact

Severity: **Medium / unverified**
Confidence: **Medium**
Standards: OWASP ASVS V3/V14, CWE-922, CWE-1021

Evidence:

- Frontend hosting is not represented in the repository Terraform, so production TLS redirect/HSTS, CSP, `frame-ancestors`, `X-Content-Type-Options`, Referrer Policy, Permissions Policy, cache behavior, and domain configuration cannot be verified.
- `frontend/src/firebase.ts:60-64` uses Firebase `browserLocalPersistence`, making the browser session durable and increasing the impact of any same-origin XSS or malicious extension.
- Positive control: `frontend/src/components/MarkdownMessage.tsx:19-28` disables raw HTML, replaces images with inert placeholders, and routes anchors through a safe HTTP(S)-only link component with `noopener noreferrer`.
- No `dangerouslySetInnerHTML` usage was found in the frontend source.

Required remediation:

1. Define or document the actual hosting layer and test effective response headers from the production custom domain.
2. Deploy a restrictive nonce/hash-based CSP appropriate to Vite/Firebase; include `frame-ancestors`, tight `connect-src`, `object-src 'none'`, and explicit image/media origins.
3. Enable HSTS after HTTPS/custom-domain validation, `nosniff`, a deliberate Referrer Policy, and a minimal Permissions Policy.
4. Review whether local Firebase persistence is appropriate for the sensitivity. A backend-managed `Secure`, `HttpOnly`, `SameSite` session provides stronger XSS token protection but is an architectural change.
5. Add browser security-header and DOM-XSS regression checks.

### SEC-13 — Image immutability is advisory only and CI builds a different, root-running image

Severity: **Medium**
Confidence: **High**
Standards: NIST SSDF PS.2/PW.4, SLSA build integrity, CWE-494

Evidence:

- `terraform/cloud-run/variables.tf:23-26` describes the API image as immutable but does not require an `@sha256:` digest. Browser and Steel image variables at `variables.tf:115-127` have no digest validation either.
- `.github/workflows/ci.yml:368-399` builds and scans the root `Dockerfile`.
- The root `Dockerfile:14-80` retains compilers, development libraries, `curl`, and root execution in the final image.
- `Dockerfile.cloud-run:32-52` is a cleaner multi-stage, non-root image, but it is not the image built by the main CI publish job and omits the WhatsApp bridge.
- Base images and the uv tool image use mutable tags rather than source digests. Steel is pinned in the example path, but Terraform does not enforce it.

Required remediation:

1. Define one production image strategy per workload and make CI build, scan, attest, and publish exactly those Dockerfiles.
2. Run every production container as a numeric non-root user with a minimal final stage and no build tools.
3. Deploy only Artifact Registry digest references. Add Terraform validation requiring `@sha256:<64 hex>` for all enabled images.
4. Generate SBOMs and build provenance, sign images, and enforce admission verification where available.
5. Pin base/tool images by digest and automate review of digest updates.

### SEC-14 — State logging/retention and Artifact Registry CMEK are not expressed

Severity: **Low / policy-dependent**
Confidence: **High**
Standards: CIS Controls 3 and 8; NIST CSF PR.DS and DE.CM

Checkov reported two bootstrap findings:

- `CKV_GCP_62`: `terraform/bootstrap/main.tf:53-70` does not configure Cloud Storage access logging for the Terraform state bucket.
- `CKV_GCP_84`: `terraform/bootstrap/main.tf:24-33` does not configure a customer-managed encryption key for Artifact Registry.

Positive controls are present on the state bucket: uniform bucket-level access, public-access prevention, versioning, `force_destroy = false`, and Terraform `prevent_destroy`. KMS keys and runtime secrets also use `prevent_destroy`, and the user-vault key rotates.

Required decision/remediation:

1. Enable Cloud Storage Data Access audit logs and retain them in a separately protected logging project/bucket. If usage logging is required by policy, configure it to a separate log bucket. Document which mechanism closes `CKV_GCP_62`.
2. Define a state-object retention/soft-delete/lifecycle policy appropriate to recovery and compliance. Locking a retention policy is irreversible and should require explicit governance approval.
3. Decide whether Artifact Registry must use CMEK. If required, add a dedicated key, least-privilege service-agent grants, rotation/recovery procedures, and `prevent_destroy` safeguards.

References: [Cloud Storage access logging](https://docs.cloud.google.com/storage/docs/access-logs) and [Cloud Storage retention policies](https://docs.cloud.google.com/storage/docs/bucket-lock).

### SEC-15 — Local attachment storage is unsafe for production if image input is enabled

Severity: **Low in the default configuration**
Confidence: **High**

Evidence:

- `terraform/cloud-run/locals.tf:10-13` configures attachments under `/tmp`, selects the local backend, and disables chat image input.
- Cloud Run local files are ephemeral and not shared across API instances or jobs.

If image input is enabled without replacing this backend, attachments can disappear, be unavailable to a worker, or produce inconsistent cleanup. This is primarily a durability/availability issue, but sensitive data lifecycle and deletion guarantees also become unclear.

Keep image input disabled until a durable, tenant-safe object store is configured with encryption, private access, retention/deletion policy, malware/content handling as required, and end-to-end ownership tests. The attachment service’s current size, path, image-decompression, re-encoding, and tenant checks are strong.

### SEC-16 — API schema and interactive documentation are public

Severity: **Low**
Confidence: **High**

`src/voxos/api/main.py:153` creates FastAPI with default documentation settings, making `/docs`, `/redoc`, and `/openapi.json` public when the API is public. This does not expose credentials and is not an authentication bypass, but it gives attackers an exact operation and schema inventory.

Disable production docs/OpenAPI or make the exposure an explicit accepted decision. Keep a protected internal schema publication path for client generation and operations.

### SEC-17 — Android retains legacy external-storage access

Severity: **Low**
Confidence: **High**

`app/android/app/src/main/AndroidManifest.xml:8-22` requests legacy write access through Android 10 and sets `requestLegacyExternalStorage=true`. If the app now uses the system photo picker/media APIs, remove these settings. If they remain necessary for Android 10 image export, document the exact versioned requirement and verify that no sensitive application files are written to shared storage.

### SEC-18 — Codex runtime remains a high-trust Docker execution feature

Severity: **Informational / known risk**
Confidence: **High**

The user identified Codex integration risk as already known. Terraform sets `CODEX_CLOUD_ENABLED=false` in `terraform/cloud-run/locals.tf:14`, and it should remain disabled for the initial deployment. The code-level default is enabled outside this Terraform path, so every non-Terraform production environment must set it explicitly.

The local Docker execution path has useful hardening—non-root execution, read-only filesystem, dropped capabilities, no-new-privileges, bounded CPU/memory/PIDs/output, and hardened temporary filesystems—but Docker daemon access and arbitrary networked code execution remain high-trust boundaries. Treat deployment of this feature as a separate threat model and approval event.

## Positive security controls verified

The following controls materially reduce risk and should be preserved during remediation:

- All ordinary API routes use Firebase bearer authentication and derive tenant authority from the verified durable local user ID, not request/LLM metadata.
- Firebase verification pins RS256 and validates key ID, issuer, audience, subject, issued-at, authentication time, expiry, and clock skew.
- Tenant-owned database access consistently uses scoped session helpers. RLS migrations enable and force policies, and the suite includes wrong-tenant and worker-scope tests.
- The local running database role is `NOSUPERUSER` and `NOBYPASSRLS`; SEC-01 is about ownership/DDL separation, not absence of RLS.
- User secrets use AES-256-GCM/envelope encryption with authenticated context binding owner, provider, credential ID, and purpose. Plaintext secrets are not returned by APIs.
- MCP OAuth state is HMAC-signed, persisted, expiring, tenant/server-bound, single-use, and paired with a vaulted PKCE verifier. Callback HTML is escaped.
- Internal MCP credential delegation binds audience, subject, tenant/server/provider, JTI, issued-at, and expiry and has a small body cap.
- Tool arguments are recursively checked for authority-bearing fields, schema validated, and filtered through per-agent grants/effective snapshots. Subagents retain tenant lineage and intersected policy.
- Twilio and Telnyx webhooks verify provider signatures and bind route, tenant, resource, account, call, and stream context. Route keys have high entropy and are stored hashed.
- LiveKit tokens replace client-controlled identity/tenant/session metadata with server-owned values and use signed internal routing.
- Attachments are streamed with source-size limits; images have format, pixel, animation, decompression, and re-encoding controls; downloads enforce ownership and `nosniff`.
- Image-provider download URLs are constrained, bounded, and do not follow arbitrary redirects.
- WhatsApp subprocess framing, capacity, environment, logging, encrypted persistence, and tenant multiplexing have focused tests; its production dependency audit is clean.
- The optional browser service is IAM-private, separately identified, receives no cloud secret grants, is concurrency-limited, and is disabled by default.
- `Dockerfile.cloud-run` uses a multi-stage build and numeric non-root runtime.
- Terraform state prevents public access, uses uniform bucket-level access and versioning, and protects destructive lifecycle changes.
- GitHub Actions are pinned to commit SHAs.
- React Markdown disables raw HTML, blocks embedded remote images, validates web links, and uses `noopener noreferrer`.

## Deployment gate

### P0 — must close before production data

- [x] Split migration ownership from all runtime database roles and prove runtime DDL/RLS-policy denial.
- [ ] Remove or harden arbitrary OpenAI-compatible outbound destinations; disable/revalidate redirects and close DNS rebinding.
- [ ] Replace MCP networking with connect-time/redirect-safe SSRF enforcement and restrict authority-bearing headers.
- [ ] Implement network egress controls that block metadata/private destinations independently of application validation.
- [ ] Split service accounts, database credentials, secret maps, KMS permissions, FCM, and job-launch authority by workload.
- [ ] Establish the real production database’s private connectivity, verified TLS, backups/PITR, restore test, restricted roles, and monitoring.
- [ ] Upgrade all production packages with known advisories and rerun full tests/scans.
- [ ] Add edge/application request limits, per-user work/spend limits, and WebSocket budgets before opening self-service access.

### P1 — must close or explicitly accept before launch

- [ ] Keep browser and Codex features disabled until their separate enforcement/threat-model work is complete.
- [ ] Enable Firebase revocation handling and decide account admission, MFA, and step-up policy.
- [ ] Add frontend, browser-agent, Flutter, and Terraform security gates to CI and dependency automation.
- [ ] Enforce immutable image digests and align CI with the actual non-root production images.
- [ ] Define frontend hosting, security headers, custom-domain/TLS configuration, and token persistence policy.
- [ ] Disable public API docs or formally accept them.
- [ ] Define logging, audit retention, alerts, incident response, and external Workload Identity Federation restrictions.

### P2 — hardening and governance

- [ ] Decide state access logging/retention and Artifact Registry CMEK requirements.
- [ ] Remove Android legacy storage if unnecessary.
- [ ] Add periodic external penetration testing, tenant-isolation testing, dependency review, restore exercises, and cloud IAM drift review.

## Validation evidence

### Passed

- Full Python suite: **1,073 passed, 20 skipped, 6 warnings** in 93.36 seconds. Skips are Postgres/external-integration tests whose prerequisites were not enabled for that invocation.
- Browser agent: typecheck, production build, and five tests passed.
- WhatsApp bridge: lint, typecheck, production build, 27 tests, and production dependency audit passed.
- Flutter: `flutter analyze` and 69 tests passed.
- Frontend: lint and production build passed. Production dependency audit reported zero advisories.
- Terraform: `fmt -check` and `validate` passed for bootstrap and Cloud Run roots.
- Checkov Cloud Run root: 11 passed, zero failed.
- Semgrep `p/ci`: 57 applicable rules across 620 files, zero findings. This ruleset did not provide complete Terraform coverage.
- Secret scan across tracked and untracked files: passed.
- Unauthenticated API GET/mutation sweeps and route dependency inventory: passed as described in the ingress section.
- Local database read-only inspection: confirmed non-superuser/NOBYPASSRLS, RLS status, ownership, and the SEC-01 privilege problem.

### Findings/failures requiring action

- `pip-audit`: seven advisories in `aiohttp`, `cryptography`, and `h2`.
- Browser-agent production `npm audit`: 17 low-severity dependency paths rooted in one uncontrolled-resource-consumption advisory.
- Frontend full development `npm audit`: three dev-only vulnerable packages; production audit is clean.
- Checkov bootstrap root: 18 passed and two failed (`CKV_GCP_62`, `CKV_GCP_84`).
- Bandit scanned 73,186 lines and reported 25 items: 23 low and two medium. Manual review found the two medium results non-exploitable in their current context: a fixed `/tmp` path used inside the hardened Codex container, and a SQL statement composed from a static optional clause with bound values.
- The first full frontend test run had one failure in `OperationsPage.test.tsx`; the file’s three tests passed in isolation. A complete rerun then passed **18 files / 94 tests**. This remains evidence of order-dependent or transient test contamination that should be investigated even though the rerun was clean.
- `flutter pub outdated` identified two direct constraints that block latest releases and 27 locked transitive upgrades. This is not itself proof of vulnerability.
- Final repository `make pre-commit` gate: **passed**, including the repository-wide tracked-file hooks and tracked/untracked secret scan. The new report also passed an explicit untracked-file pre-commit run.

### Not run / unavailable

- No `terraform plan` against production variables or remote state. The required ignored backend/variable inputs were not present, and no cloud mutation was authorized.
- No deploy, migration, state operation, carrier call, real provider request, or production job was executed.
- No black-box test against deployed Cloud Run/custom domains, Firebase tenant configuration, frontend hosting, database provider, DNS, VPC, WIF, Secret Manager payloads, organization policies, or cloud audit settings.
- No external dynamic application scanner, mobile binary scanner, container-registry signature verification, or third-party penetration test.
- No authoritative Flutter/Dart vulnerability database tool was available locally.

## Recommended security tests after remediation

1. **Database containment test:** run arbitrary representative SQL as every runtime principal and prove DDL/RLS-policy/other-tenant access fails while required DML succeeds.
2. **Tenant matrix:** for every user-owned endpoint and worker operation, create tenant A/B records and prove read, update, delete, relationship, attachment, run, schedule, tool, webhook, and secret cross-access fails.
3. **SSRF harness:** exercise redirects, DNS rebinding, mixed answers, IPv4/IPv6 encodings, localhost aliases, metadata, private/VPC ranges, scheme downgrades, OAuth endpoints, MCP transports, model endpoints, downloads, and browser traffic.
4. **Abuse/load tests:** enforce and verify `413`, `429`, concurrency, duration, token/image/browser/carrier/spend, WebSocket, retry, and queue limits without cross-tenant starvation.
5. **Browser adversarial suite:** prompt injection, cross-origin frames/resources, login redirects, malicious downloads/uploads, hidden forms, payment/message submission, service workers, WebSockets, and secret-target binding.
6. **Cloud identity test:** from each workload, enumerate allowed and denied Secret Manager, KMS, FCM, job, storage, and metadata operations; alert on any permission outside the workload contract.
7. **Deployment configuration test:** retrieve effective headers, IAM policies, ingress, egress, image digests, TLS posture, database grants, audit logs, backups, retention, and restore evidence from the deployed environment.
8. **Supply-chain test:** reproduce images from the reviewed commit, verify SBOM/provenance/signature/digest, and ensure Terraform deploys that exact digest.

## Standards/control interpretation

This review used OWASP ASVS 5.0, OWASP API Security Top 10 2023, OWASP SSRF guidance, CWE, NIST SSDF, NIST CSF 2.0, CIS Controls v8, and common Google Cloud security guidance as classification and verification aids. The mapping does not constitute certification or a formal compliance attestation.

The highest control priorities are:

- Identity and least privilege: SEC-01, SEC-04, SEC-08.
- Tenant and data isolation: SEC-01 plus the existing RLS/tenant tests.
- Network and untrusted destinations: SEC-02, SEC-03, SEC-05, SEC-06.
- Resilience and abuse prevention: SEC-07.
- Secure software/supply chain: SEC-09, SEC-10, SEC-13.
- Data protection and recovery: SEC-11, SEC-12, SEC-14, SEC-15.

## Limitations and residual risk

The repository is only one layer of the system. The most important unverified controls are external: Google Cloud organization policies, WIF attribute conditions, actual IAM bindings and inherited roles, Cloud Run revisions, frontend hosting/CDN headers, Firebase sign-up/MFA/authorized domains, production database networking/TLS/grants/backups, DNS, provider dashboards, carrier callback configuration, secret rotation, log retention, alerting, and human operational access.

Static analysis and tests can miss business-logic abuse, dependency zero-days, provider behavior changes, race conditions, browser/model prompt injection, and exploit chains that appear only in a deployed network. After the P0 items are fixed, a production-like isolated environment should receive a black-box tenant-isolation and SSRF penetration test before real sensitive data is stored.

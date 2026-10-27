# Demand-Driven Multi-Tenant LiveKit Workers

Status: implemented in application code on 2026-07-09; deployment wiring is
tracked separately.

The supervisor entrypoint and opt-in Docker Compose `voice` profile are
implemented. The tracked deployment workflow does not provision it as a
long-running production workload; production voice requires that external
operational wiring before this architecture is available in a deployed
environment.

## Decision

Voxos runs a database-backed `VoiceWorkerSupervisor`. It does not enumerate
saved LiveKit settings or register one permanent LiveKit worker per configured
tenant. A verified call route creates a short-lived `voice_call_demands` row,
and the supervisor starts or reuses one isolated child process for that demand's
exact LiveKit credential fingerprint.

Tenants with different LiveKit projects use different child processes. Calls
whose saved URL, API key, and API secret are identical share one project child
while they overlap. A child drains and exits after the last demand and the
configured idle TTL.

## LiveKit Constraints

1. **A normal dispatched job needs a persistent registration.** An
   `AgentServer` started with `unregistered=True` is intentionally invisible to
   the LiveKit dispatcher. It is not an on-demand production job receiver. See
   [agent server lifecycle](https://docs.livekit.io/agents/server/lifecycle/)
   and [agent dispatch](https://docs.livekit.io/agents/server/agent-dispatch/).
2. **There is no documented cold-start SLA.** LiveKit's dispatch-latency claims
   assume an available worker; they do not include Python startup, imports,
   process-pool initialization, TLS/WebSocket connection, or registration.
   Voxos treats the SDK's `worker_registered` event as readiness and applies a
   bounded startup timeout.
3. **Worker readiness must precede dispatch.** Self-hosted LiveKit does not keep
   an unassigned agent launch pending when no worker is available. Voxos waits
   for registration before minting the dispatch-bearing participant token or
   connecting a phone media bridge.
4. **A generic pre-warmed registration pool is not useful.** Registration is
   project-specific. Voxos instead uses on-demand startup plus an idle TTL to
   amortize bursts. Active project workers set `num_idle_processes=0` and
   disable the SDK load threshold; the durable
   `VOICE_WORKER_MAX_CALLS_PER_PROJECT` admission limit is authoritative.
5. **Different projects must not share an interpreter.** LiveKit Agents 1.6
   copies explicit project credentials into process-global `LIVEKIT_*`
   variables for its job runtime. Multiple `AgentServer` instances with
   different projects in one interpreter can therefore contaminate each other.
   Voxos uses one non-daemon OS child per active project fingerprint and
   `JobExecutorType.PROCESS` for per-call job isolation. The registration child
   clears SDK environment copies after each registration. A picklable
   `ProjectJobEnvironment` setup callback restores that project's exact values
   only inside each job process, preserving `JobContext.api` and SDK RoomIO
   behavior such as `delete_room_on_close`. These variables are isolated
   runtime state, never application or supervisor deployment configuration.
   See the LiveKit Agents 1.6
   [credential export](https://github.com/livekit/agents/blob/livekit-agents%401.6.0/livekit-agents/livekit/agents/worker.py#L718-L723)
   and [job API construction](https://github.com/livekit/agents/blob/livekit-agents%401.6.0/livekit-agents/livekit/agents/job.py#L396-L405).

## Answers To The Architecture Questions

1. LiveKit Agents cannot receive normal dispatched jobs without a persistent,
   project-specific worker registration. `unregistered=True` is deliberately
   invisible to dispatch.
2. LiveKit does not publish a cold-registration SLA. Voxos therefore uses a
   configurable bounded wait and does not assume the child will register before
   a call arrives.
3. Yes. Call setup durably admits demand and waits for `worker_registered`
   before returning dispatch-bearing TwiML/token metadata, answering Telnyx, or
   placing an outbound provider call.
4. On-demand startup with an idle TTL is the selected default. A generic warm
   pool cannot be reassigned across tenant projects; targeted pre-warming would
   be an operational policy only if measured cold-start latency requires it.
5. Different LiveKit projects use child processes. LiveKit Agents 1.6 writes
   project credentials to process-global environment state, so multiple
   projects are not hosted in one interpreter.

## Call Flow

1. Firebase authentication or a verified phone webhook resolves the canonical
   local Voxos user. Frontend IDs and unsigned metadata are never authority.
2. Call setup resolves that user's LiveKit settings through a tenant-scoped
   session and `UserSecretVaultService`. Outbound Telnyx/Twilio setup creates a
   durable reservation and demand, commits, and waits for worker readiness
   before invoking the provider. Verified inbound webhooks commit their call
   session and demand, then wait before returning streaming TwiML or answering
   Telnyx.
   Twilio outbound callback URLs carry the server-created call-session ID;
   signed callbacks atomically bind the provider Call SID back to that original
   reservation, preserving its agent, runtime session, room, and demand even if
   the callback arrives before the REST response.
3. Voxos derives an opaque HMAC project fingerprint with
   `VOICE_ROUTE_SIGNING_SECRET`. No URL, API key, or secret is stored in the
   demand row.
4. Call setup inserts or renews its tenant-owned demand in the same tenant
   transaction as the durable call session. It commits before waiting so the
   supervisor can see the demand. The browser token endpoint and phone media
   bridge also recheck readiness before minting a dispatch-bearing token.
5. The supervisor reads live demands through the narrow `voice_worker` service
   scope and claims a project lease. It then re-enters a tenant-scoped session
   using the user ID from that RLS-protected demand row before decrypting that
   one tenant's settings.
6. Credentials are sent once to a spawned child over an anonymous process pipe,
   never through argv or parent environment variables. The child removes any
   inherited `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET`, creates
   its `AgentServer` with explicit values, scrubs the SDK's temporary outer-child
   copies after registration, and reports only non-secret lifecycle events to
   the supervisor. The child retains explicit credential objects for reconnect
   until it exits. Each isolated job process receives the same project's values
   through `ProjectJobEnvironment` before job code starts.
7. After `worker_registered`, the supervisor admits pending demands in request
   order up to `VOICE_WORKER_MAX_CALLS_PER_PROJECT`. Excess pending calls fail
   with `project_call_capacity_exhausted`. The API can then issue the signed
   route token and LiveKit dispatch metadata.
8. The job entrypoint verifies the server-minted `voxos_route` signature with
   `VOICE_ROUTE_SIGNING_SECRET`, enters the call tenant context, atomically
   changes a ready demand to active only when its stored worker ID matches the
   `JobContext.worker_id`, and heartbeats it while the session runs. A stale,
   missing, expired, failed, or replayed demand rejects the job. Plain `user_id`
   metadata is not authority.
9. Completed or expired demands release their per-project call slot. When no
   demand or active job remains, the child and cluster lease stay through the
   idle TTL for burst reuse, then call `drain()` and `aclose()` and exit.

## Persistence And RLS

- `voice_call_demands` is tenant-owned and has forced RLS. Tenant sessions can
  create and read only their own rows. The `voice_worker` scope has only the
  cross-tenant select/update access needed to route demand.
- `voice_project_worker_leases` contains only opaque project keys and non-secret
  worker state. Forced RLS permits only the `voice_worker` service scope.
- The supervisor has no cross-tenant service policy on
  `user_livekit_settings` or `user_secret_vault`. It resolves credentials only
  after switching to the persisted demand's tenant scope.
- Project leases prevent two supervisor replicas from starting duplicate
  registrations for the same credential fingerprint. A crashed owner's lease
  expires and can be reclaimed.
- PostgreSQL transaction advisory locks serialize per-tenant quota decisions
  and cluster-wide project admission. Lease timestamps use database time, and
  owner-fenced updates prevent a stale supervisor from publishing readiness or
  failure after another replica takes over.

## Failure Isolation And Logging

Invalid or rotated settings fail the affected demand. A child registration
failure marks only that project fingerprint failed; other project children and
calls continue. Supervisor refresh failures are retried without discarding
healthy children. Child startup has a bounded deadline because the SDK's
connection task can otherwise exhaust retries without terminating
`AgentServer.run()`.

Saved LiveKit endpoints are canonicalized as root `ws` or `wss` origins.
Userinfo, non-root paths, queries, fragments, invalid ports, whitespace, and
control characters are rejected before persistence. The default `public_wss`
worker policy also requires TLS and globally routable DNS results immediately
before child launch. Controlled local or private-network LiveKit deployments
must opt into `trusted_private`; the root Compose `voice` profile does so for
its bundled `ws://livekit:7880` server.

Endpoint validation is defense in depth, not a complete SSRF boundary. DNS can
change after validation and the SDK resolves again when connecting or
reconnecting. Production deployments must therefore constrain supervisor
egress at the network boundary and deny metadata, VPC, link-local, loopback,
and other internal destinations regardless of application policy.

Logs may include user ID, runtime session ID, source, a sanitized LiveKit origin
(scheme, host, and port only), opaque project fingerprint, worker ID, status,
and failure type/code. A tenant-configured URL can contain sensitive userinfo,
query parameters, or fragments, so logs must not emit the unfiltered value.
They must never include API keys, API secrets, vault plaintext, participant
tokens, or signed route tokens.

## Operational Tradeoffs

- The first call to a cold project pays child-process and LiveKit registration
  latency. Measure P50/P95 readiness in each environment and tune the startup
  timeout and idle TTL from evidence.
- Project settings resolve concurrently up to
  `VOICE_WORKER_START_CONCURRENCY`. Each resolution is bounded by
  `VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS`, which must be shorter
  than the caller-facing worker start timeout. Completed healthy starts publish
  readiness immediately instead of waiting for every concurrent project.
- Voxos currently uses token-carried room configuration for dispatch, but only
  after `worker_registered`. Explicit dispatch plus assignment-state polling is
  a possible follow-up if production evidence shows a registration-propagation
  race.
- A dispatch-bearing token keeps its demand live for at least the token TTL so a
  delayed but still-valid join cannot outlive its project registration. Once a
  job starts, the shorter active-demand TTL is renewed by session heartbeats.
- Direct LiveKit SIP calls that arrive without a prior Voxos-controlled API or
  webhook admission point cannot cold-start this architecture safely. Such a
  route needs an always-ready project registration or a separate admission
  design.
- Twilio's Create Call API has no outbound request idempotency key. Callback
  correlation prevents the reservation/webhook routing race, but an ambiguous
  network failure cannot prove whether Twilio created a call. Callers must not
  automatically retry that uncertain operation; a durable outbound submission
  state machine is a separate follow-up if retry-safe public call creation is
  required. Telnyx supports stronger command-level deduplication, but Voxos
  applies the same conservative retry rule today.
- When the SDK starts reconnecting, the child reports `NOT_READY`; the
  supervisor moves still-ready demands back to pending and changes the lease to
  starting. A fresh `worker_registered` event is required before readiness is
  republished. This currently observes LiveKit Agents 1.6 private
  `_connecting` and `_connection_failed` state, so SDK upgrades require the
  lifecycle tests to pass before adoption.
- The supervisor stores durable demand and leases in Postgres, but project child
  state is intentionally ephemeral. A restart reconstructs pending or ready
  demand. Lease reclamation fails active demands tied to the lost worker;
  in-process calls are not resumed.
- Every API and supervisor replica in one environment must use the same
  `VOICE_ROUTE_SIGNING_SECRET` and `VOICE_AGENT_NAME`. The signing secret derives
  both project fingerprints and route-token signatures; rotation invalidates
  pending demands and outstanding route tokens. The agent name must match the
  name used in dispatch-bearing tokens.
- `VOICE_WORKER_LEASE_TTL_SECONDS` must exceed
  `VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS` plus twice
  `VOICE_WORKER_POLL_INTERVAL_SECONDS`; the supervisor rejects startup when this
  ownership-safety invariant is not met. A separate TTL/3 heartbeat renews live
  project leases even while discovery or shutdown work is slow.
- Admission is bounded by `VOICE_WORKER_MAX_ACTIVE_PROJECTS` across the cluster
  (including starting and idle-retained leases),
  `VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_SUPERVISOR` per supervisor process,
  `VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_TENANT` per tenant (including recently
  completed registrations during their idle TTL), and
  `VOICE_WORKER_MAX_CALLS_PER_PROJECT` per fingerprint. Requests receive
  backpressure instead of spawning unbounded children or jobs. Supervisor
  replicas must use the same limit and lifecycle settings.
- Lease renewal is bounded-parallel and deadline-limited. One contended project
  cannot serialize renewal for every other project, and renewal updates only
  READY routes whose readiness deadline must move; it does not churn every live
  demand row.
- `VOICE_LIVEKIT_ENDPOINT_POLICY=public_wss` is the default: the supervisor
  requires WSS and globally routable DNS results immediately before child
  launch. `trusted_private` is an explicit local/self-hosted escape hatch. This
  application check is subject to DNS rebinding/TOCTOU, so a production network
  boundary must also deny metadata, VPC, link-local, and other internal egress.
- Run the supervisor as a separate long-lived workload using
  `python -m voxos.voice.worker`. It needs the same database and vault encryption
  configuration as the API, KMS decrypt access when KMS is enabled, and enough
  process and memory capacity for isolated project children. Do not inject
  tenant LiveKit credentials into that workload's environment.

Relevant settings are `VOICE_WORKER_POLL_INTERVAL_SECONDS`,
`VOICE_WORKER_START_TIMEOUT_SECONDS`, `VOICE_WORKER_DEMAND_TTL_SECONDS`,
`VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS`,
`VOICE_WORKER_START_CONCURRENCY`,
`VOICE_WORKER_LEASE_RENEWAL_CONCURRENCY`,
`VOICE_WORKER_IDLE_TTL_SECONDS`,
`VOICE_WORKER_LEASE_TTL_SECONDS`, and
`VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS`, plus the active-project and
per-project call limits and demand retention/maintenance settings. None is a
tenant LiveKit credential.

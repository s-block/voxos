# Voxos WhatsApp bridge

This private child process multiplexes Baileys linked-device sockets for the
Python WhatsApp supervisor. It has no HTTP listener, database access, vault
credentials, tenant identities, or filesystem auth store. Stdout is reserved
for protocol frames; diagnostics on stderr are fixed strings and never include
JIDs, numbers, challenges, message bodies, or upstream errors.

## Runtime

- Node.js 20 or newer.
- Exactly pinned `baileys@7.0.0-rc14`.
- `npm ci` and `npm run build` happen when the image is built, never when a
  worker starts.
- Start the compiled bridge with `npm start` or `node dist/index.js`.

Baileys's package-bundled WhatsApp protocol version is used. The bridge never
fetches a "latest" protocol or dependency version at runtime.

## IPC

The bridge reads and writes newline-delimited JSON. Every frame is at most
1 MiB and carries:

```json
{
  "protocol_version": 1,
  "frame_type": "command",
  "request_id": "opaque-request-id",
  "session_handle": "opaque-session-handle",
  "generation": 1,
  "command": "session_status",
  "payload": {}
}
```

`request_id`, `session_handle`, and the lease-fencing `generation` are required
on every command, response, and event. The reserved bridge handle is `bridge`
with generation `0`.

The handshake advertises `linked_state`, `durable_events`,
`ordered_durable_events`, `logout_if_linked`, and `challenge_revisions`
capabilities and reports `rpc_timeout_ms`. Python must require these
capabilities and configure the same RPC timeout so neither side abandons a
persistence request early.

Commands are:

- `handshake`
- `start_session` with optional tagged `credentials`
- `session_status`
- `request_challenge` using `qr` or `pairing_code` plus a positive
  `request_revision`
- `send_text`
- `stop_session`
- `logout_if_linked`
- `rpc_result`
- `shutdown`

The bridge emits `auth_get`, `auth_set`, `link_challenge`, `session_status`,
and `inbound_text`. Each event must receive a correlated `rpc_result`.
Challenges, statuses, and inbound messages enter one bounded FIFO per session.
The head is retried with the same correlation ID and capped backoff until
Python acknowledges its durable transaction or the session stops. A
`persistence_failed` negative acknowledgement is retryable;
`session_unavailable` and other permanent negative acknowledgements terminate
delivery for that session. Auth persistence uses its own RPC lane so an auth
response cannot deadlock behind a durable event.

Whole `messages.upsert` handlers are also bounded. Notifications received
before the connected `session_status` transaction is acknowledged are retained
and delivered in order afterward. One overloaded session is closed without
terminating the shared bridge process.

The bridge may also emit:

- `protocol_error`

Fresh credentials are persisted through `auth_set` before a socket is created.
Signal key writes are serialized per session and block on the matching Python
acknowledgement. Python remains responsible for tenant scope, encryption,
durability, leases, and deletion after a confirmed logout.
Key reads use bounded batches of at most 512 values. Each Baileys key mutation
is sent as one `auth_set` containing at most 2,048 writes so Python can commit
the complete mutation in one fenced database transaction; it is never split
into independently committed writes.

Example auth RPC payloads are:

```json
{"category":"session","logical_ids":["peer-id"]}
{"writes":[{"category":"session","logical_id":"peer-id","value":{}}]}
```

Python answers either request with `rpc_result` and
`{"rpc_request_id":"event-request-id","success":true,"result":...}`. An
`auth_get` result is `{"values":{"peer-id":...}}`.

Successful `send_text` returns `provider_message_id`. Session snapshots and
status events expose both Baileys's `registered` flag and `linked`.
`linked` is true when `registered` is true or both the signed account identity
and local identity are present; this covers QR pairing, where Baileys can leave
`registered` false. A connected `session_status` event includes an `identity`
with `pn_jid`, `lid_jid`, and a proof of `pn`, `pn_and_lid`, `lid_only`, or
`unresolved`. Only a PN-bearing proof is suitable for backend number
validation.

Every challenge event and ready or pending challenge result echoes
`request_revision` and `requested_method`; `method` remains the actual
challenge type. Python must positively acknowledge an obsolete revision after
ignoring it, while persisting only a revision and method that still match its
locked session record.

`stop_session` is a local-only stop used for lease loss, worker drain, and
restart handoff; it deliberately preserves a linked companion.
`logout_if_linked` decides from the controller's current auth state whether a
remote logout is required. It reports `logout_uncertain` when linked auth has
no live socket or remote revocation otherwise cannot be confirmed. In that
case the local socket is closed and flushed, but Python must retain the
encrypted auth state so it can restart the controller and retry; only a
confirmed logout, or a controller-confirmed unlinked state, permits credential
deletion.
Bounded lifecycle tombstones make retries with a new command request ID
idempotent for the same session generation. A completed local stop is never
treated as proof that logout occurred.

Binary data uses explicit tagged values:

```json
{"$voxos_type":"uint8array","base64":"AQID"}
```

Buffers, typed byte arrays, big integers, dates, and `undefined` round-trip
without relying on implicit `JSON.stringify` behavior.

Only live one-to-one text notifications are emitted. Groups, broadcasts,
status/newsletter traffic, media, history appends, and messages from the linked
account are ignored. A send that begins but does not return an authoritative
message identifier is reported as ambiguous and is never retried by the
bridge.

## Checks

```bash
npm ci
npm run lint
npm run typecheck
npm test
```

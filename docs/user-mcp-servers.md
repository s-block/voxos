# User MCP Servers

Voxos supports user-owned remote MCP servers over Streamable HTTP and SSE. Local
stdio MCP commands are intentionally not accepted from the frontend because they
would let a web user register arbitrary commands for the worker host to execute.

For common providers, use the MCP settings catalog or
[MCP Service Catalog For VoxOS](mcp-service-catalog.md). The catalog pre-fills
vetted remote URLs, transport, auth mode, provider setup steps, and starter tool
guidance. Custom MCP servers can still be added manually.

Supported auth modes:

- `none`: no upstream MCP authentication.
- `static_bearer`: encrypted bearer token, sent as `Authorization: Bearer ...`.
- `api_key_header`: encrypted API key, sent in the configured header name.
- `oauth_authorization_code`: browser OAuth with PKCE, encrypted tokens, and
  encrypted dynamic client credentials when registration is used.
- `oauth_client_credentials`: encrypted client secret and encrypted access token.
- `private_key_jwt`: encrypted private key plus encrypted access token.

Sensitive values are sealed and unsealed through the internal credentials vault.
Plaintext tokens, API keys, refresh tokens, client secrets, private keys, and
PKCE verifiers must not be returned by API responses or written to logs.

OAuth callback URLs use:

```text
<api-base-url>/api/mcp/auth/callback
```

The callback endpoint is not Firebase-authenticated because OAuth providers
redirect browsers without a Voxos bearer token. It verifies a short-lived state
signed by the deployment-level `MCP_OAUTH_STATE_SIGNING_SECRET`; the signed
state resolves the owning user and server before tenant-scoped database access.
The corresponding one-time state and PKCE verifier are stored through the user
secret vault. Keep the signing secret stable, private, and identical across API
replicas.

Providers that advertise OAuth metadata and dynamic client registration do not
need a manually entered client ID or client secret. For example, the hosted
OpenBnB catalog entry discovers its authorization and token endpoints,
registers the Voxos callback, and uses PKCE. The user only needs to choose
**Start sign-in** and approve the provider page.

Examples:

- GitHub MCP: create a remote HTTP server entry and use `static_bearer` with a
  PAT unless a host OAuth app is configured.
- GitLab MCP: use `oauth_authorization_code`; GitLab supports dynamic client
  registration for direct HTTP MCP.
- Sentry MCP: use `oauth_authorization_code` for the remote Sentry MCP endpoint,
  or `static_bearer` only for token-based deployments.
- Custom MCP: use `static_bearer`, `api_key_header`, or OAuth endpoints supplied
  during auth start when discovery is not available.

Gateway-required catalog services are stdio-first or local-first MCP servers.
They must be hosted behind a public HTTPS MCP gateway before Voxos can use them;
`localhost`, private hosts, local commands, and credential-bearing URLs are
rejected by the same outbound URL policy as manual server setup.

The local Flightradar24 and Monzo catalog variants are narrow development
exceptions. Each uses a fixed Compose URL and is returned by the catalog only
when its complete URL is registered for the `mcp` purpose in
`OUTBOUND_TRUSTED_ENDPOINTS_JSON`. Trust applies only to that normalized URL;
other paths, ports, hosts, and endpoint purposes remain subject to the public
HTTPS policy. A separately deployed Flightradar24 instance can instead use the
catalog's editable public HTTPS endpoint variant.

Monzo has two separate authentication boundaries. Voxos stores the MCP endpoint
bearer and each user's Monzo OAuth client information and rotating tokens in the
tenant secret vault. The Monzo container validates the endpoint bearer, but it
does not store provider credentials. Voxos also sends a short-lived,
user-and-server-bound delegation header. The container exchanges that assertion
at the private Voxos credential broker for one usable access token and forwards
that token only to Monzo.

Refreshes and refresh locking remain in Voxos. The supported local preset can
serve multiple Voxos users statelessly, grants no tools automatically, and
keeps financial writes disabled. Balance and transaction tool results can be
retained in Voxos chats, runs, transcripts, and tool events.

For an existing OAuth-backed server, open **Auth** and enter both OAuth client
fields to replace incorrect or rotated provider credentials before selecting
**Save client & start sign-in**. This stores the replacement in the encrypted
vault before opening the provider. Leave both blank to reconnect using the
stored client. The same panel has **Delete server** for removing the entry and
all of its stored MCP credentials. Monzo rejects API Playground user IDs and
access tokens in these fields; use the client ID and client secret from a
confidential Monzo OAuth client.

Only servers that are enabled, trusted, successfully tested, and configured with
explicit allowed tools are exposed to voice sessions.

# MCP Service Catalog For VoxOS

Last researched: 2026-07-28.

This catalog complements [User MCP Servers](user-mcp-servers.md). Use that
guide for the general VoxOS flow: create the server, authenticate it, test it,
then set explicit `allowed_tools`. The notes below only cover service-specific
URLs, auth, prerequisites, and tool choices.

VoxOS also exposes direct services in the MCP settings catalog. Most local,
stdio-only, or self-hosted MCP servers use the single MCP Gateway catalog
preset. Flightradar24 and the personal Monzo integration have dedicated,
fixed-host local Compose variants because Voxos ships their Compose definitions.
The manual server form remains available for public remote custom MCPs.

VoxOS accepts remote MCP servers over Streamable HTTP or SSE. It does not accept
local stdio MCP commands from the frontend. Except for the exact-URL
Flightradar24 and Monzo Compose catalog variants, local or stdio-only MCP
servers must be hosted behind a public HTTPS MCP gateway or reverse proxy first.

In the app this is one `MCP Gateway` catalog preset. A gateway can expose
multiple upstream tools, so do not create separate VoxOS catalog entries for
DuckDuckGo, Brave Search, Playwright, or Local Browser Use. Register the gateway
once, run discovery, then set explicit `allowed_tools` from the discovered tool
list.

Avoid putting secrets in MCP URLs when a header or OAuth auth mode is available.
URLs are configuration; use `static_bearer`, `api_key_header`, or
`oauth_authorization_code` for credentials.

## Quick Picks

| Need | First option | Free posture | Notes |
| --- | --- | --- | --- |
| General web search | Exa | Keyless/free plan available | Good default for search plus page fetch. |
| Vacation-rental discovery | OpenBnB | Free hosted account currently available | Hosted OAuth with dynamic client registration; booking completes outside Voxos. |
| Live flight data | Flightradar24 | Provider subscription/API key required | Local Compose preset or any separately deployed public HTTPS MCP URL. |
| Personal Monzo data | Monzo | Personal Developer API client | Local Compose only; Voxos-owned provider OAuth plus a separate MCP endpoint bearer, with no starter tool grants. |
| Search plus extraction | Tavily | Free account/API key available | Focused search and extraction tools. |
| Scrape, crawl, extract | Firecrawl | Keyless free endpoint available | Useful for page scraping, crawling, and structured extraction. |
| Local or stdio MCP servers | MCP Gateway | Depends on upstream servers | One gateway registration can expose DuckDuckGo, Brave, Playwright, Browser Use, or other tools. |
| Gmail, Drive, and calendars | Google Workspace | Google account and developer-preview access | One Google-hosted MCP endpoint and Voxos server connection per Workspace product; reuse one OAuth web client. |
| Hosted browser automation | Browserless | Free plan with 1,000 units/month | Easiest managed option for short browser sessions and page extraction. |
| Hosted browser automation | Browserbase MCP | Paid/account based | Strong hosted browser and Stagehand automation. |
| Browser agent sessions | Browser Use Cloud MCP | Account/API key required | Good for high-level browser tasks and managed sessions. |
| Actor marketplace and scraping | Apify MCP | Account based | Large Actor ecosystem; narrow tool exposure carefully. |
| Code and repo context | GitHub MCP | GitHub account/PAT | Prefer read-only URL variants and fine-grained PATs. |
| Error and trace debugging | Sentry MCP | Sentry account | OAuth remote MCP with debugging-focused tools. |
| Issue/project workflow | Linear MCP | Linear account | OAuth remote MCP for issues, projects, and comments. |
| Workspace knowledge | Notion MCP | Notion account; plan-dependent features | OAuth remote MCP for pages, databases, comments, users, teams. |
| SQL and analytics | MotherDuck MCP | MotherDuck account | OAuth remote MCP; keep write tools disabled by default. |

## VoxOS Setup Pattern

For every service below:

1. Create the provider account, API key, OAuth connection, or self-hosted gateway
   required by the service.
2. In VoxOS, add a remote MCP server using the service URL and transport listed
   below.
3. Select the matching auth mode:
   - `none` for public/keyless endpoints.
   - `static_bearer` when the service expects `Authorization: Bearer ...`.
   - `api_key_header` when the service expects a named API key header.
   - `oauth_authorization_code` for remote MCP servers with OAuth.
4. Run the VoxOS server test to discover tools.
5. Set explicit `allowed_tools`; start read-only where possible and add write,
   crawl, actor, or browser-control tools only when an agent really needs them.

Browser OAuth also requires a stable Voxos
`MCP_OAUTH_STATE_SIGNING_SECRET`. This protects the callback and is separate
from provider credentials. Providers with dynamic client registration, such as
OpenBnB, do not require a manually configured OAuth client.

## Travel And Lifestyle

### Flightradar24

- Best for: live aircraft positions and counts, airline and airport details,
  and historical flight data available to the provider account.
- Cost posture: requires a Flightradar24 API subscription and API key.
- Local Compose setup:
  1. Set `FR24_API_KEY` in the ignored root `.env` file.
  2. Start the root Voxos Compose stack, then run `make mcp-stack-up`.
  3. Open the MCP catalog, choose **Flightradar24** and **Local Compose**, then
     test discovery and review the starter tools.
  4. The fixed internal URL is `http://flightradar24-mcp:8000/mcp`; no custom
     server entry or Voxos bearer token is required.
- Separate deployment setup:
  1. Deploy the same Flightradar24 MCP package behind a public HTTPS endpoint.
  2. Choose **Deployed HTTPS endpoint** and enter the complete endpoint URL.
     Any public hostname and path are accepted.
  3. Test discovery and grant only the tools the agent needs.
- Notes:
  - The official MCP package is stdio-only. The included local Compose service
    uses a pinned Supergateway image to expose Streamable HTTP.
  - The MCP package reads one process-wide `FR24_API_KEY`; it does not support
    per-user provider credentials or shared-server authentication.
  - Arbitrary unregistered private and loopback custom URLs remain blocked. The
    local preset is limited to its exact configured Compose URL.
  - A public deployment needs external network access controls because the MCP
    itself does not provide Voxos user authentication.
- Source:
  - [Flightradar24 MCP server](https://github.com/Flightradar24/fr24api-mcp)

### OpenBnB

- Best for: vacation-rental search, listing details, nearby places, maps, and
  routing.
- Cost posture: the hosted service currently offers a free account without a
  credit card.
- VoxOS setup:
  - URL: `https://mcp.openbnb.ai/mcp`
  - Transport: `streamable_http`
  - Auth: `oauth_authorization_code`
  - OAuth client credentials: none; Voxos uses provider metadata discovery,
    dynamic client registration, and PKCE.
  - Suggested starter tools after discovery: read-only search and listing
    detail tools.
- Service setup:
  1. Add OpenBnB from the Voxos MCP catalog.
  2. Select **Start sign-in** and create or sign in to the hosted OpenBnB
     account.
  3. Approve the provider page and return to Voxos.
  4. Review the discovered tools before granting agent access.
- Notes:
  - Booking completion remains on the provider website.
  - OpenBnB is community-operated and is not affiliated with or endorsed by
    Airbnb.
  - Public listing extraction can change when upstream pages change.
- Sources:
  - [OpenBnB](https://openbnb.ai/)
  - [OpenBnB MCP server](https://github.com/openbnb-org/mcp-server-airbnb)

## Web Search And Research

### Exa

- Best for: web search, neural search, page fetch, research workflows.
- Cost posture: keyless access and a free plan are available; API keys raise
  limits and are required for some production or agent tools.
- VoxOS setup:
  - URL: `https://mcp.exa.ai/mcp`
  - Transport: `streamable_http`
  - Auth for keyless use: `none`
  - Auth for API-key use: `api_key_header` with header name `x-api-key`
  - Suggested `allowed_tools`: `web_search_exa`, `web_fetch_exa`
  - Voxos provisions the canonical keyless variant and its two low-risk tools
    for accounts that do not already have an Exa endpoint. Disable that server
    in MCP settings to opt out, or replace it with the API-key variant for
    higher limits.
- Service setup:
  1. For a manual keyless setup, register the URL without credentials and test
     it.
  2. For higher limits, create an API key in the Exa dashboard.
  3. Optional URL query parameters can narrow tools, for example
     `https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa`.
- Notes:
  - Exa's agent tools are usage-based and should be enabled separately only for
    agents that need long-running research tasks.
- Sources:
  - [Exa MCP reference](https://exa.ai/docs/reference/exa-mcp)
  - [Exa API keys](https://dashboard.exa.ai/api-keys)

### Tavily

- Best for: search, answer-oriented research, extracting content from URLs.
- Cost posture: free account/API key available; production usage depends on the
  Tavily plan.
- VoxOS setup:
  - URL: `https://mcp.tavily.com/mcp/`
  - Transport: `streamable_http`
  - Preferred auth: `oauth_authorization_code`, if the VoxOS auth start flow
    succeeds for the Tavily remote MCP endpoint.
  - API-key auth: `static_bearer` with the Tavily API key.
  - Suggested `allowed_tools`: `tavily-search`, `tavily-extract`
- Service setup:
  1. Create a Tavily account.
  2. Create an API key in the Tavily dashboard if not using OAuth.
  3. Register the remote MCP URL in VoxOS and test tool discovery.
- Notes:
  - Tavily documents API keys in both URL-query and `Authorization` header
    forms. Prefer the bearer-header path in VoxOS so the key is encrypted.
- Sources:
  - [Tavily MCP documentation](https://docs.tavily.com/documentation/mcp)
  - [Tavily app](https://app.tavily.com)

### Firecrawl

- Best for: scraping, crawling, URL mapping, structured extraction, web search.
- Cost posture: a keyless remote MCP endpoint is available with free,
  rate-limited access; API-key usage depends on the Firecrawl plan.
- VoxOS setup:
  - URL: `https://mcp.firecrawl.dev/v2/mcp`
  - Transport: `streamable_http`
  - Keyless auth: `none`
  - API-key auth: `static_bearer` with the Firecrawl API key.
  - Suggested starter `allowed_tools`: `firecrawl_search`, `firecrawl_scrape`,
    `firecrawl_map`
- Service setup:
  1. For keyless testing, register the URL with `none` auth.
  2. For authenticated usage, create a Firecrawl API key.
  3. Prefer the bearer-header form in VoxOS. Do not put the API key in the URL
     path even though Firecrawl documents that form for some clients.
  4. Add crawl or extraction tools only after confirming cost and latency.
- Notes:
  - Crawl and agent-style tools can perform broad work. Keep them out of
    `allowed_tools` until a workflow needs them.
- Sources:
  - [Firecrawl MCP server documentation](https://docs.firecrawl.dev/mcp-server)

### DuckDuckGo MCP

- Best for: free, no-account web search using DuckDuckGo results.
- Cost posture: free/no account; community package.
- VoxOS setup:
  - Catalog preset: `MCP Gateway`
  - Direct remote URL: none. The package is stdio-first.
  - Self-hosted VoxOS URL: your public HTTPS gateway URL, for example
    `https://duckduckgo-mcp.example.com/mcp`
  - Transport: depends on your gateway; prefer `streamable_http`.
  - Auth: `static_bearer` if your gateway enforces bearer auth; otherwise
    `none` for an internal-only trusted deployment.
  - Suggested `allowed_tools` after discovery: `duckduckgo_search`
- Service setup:
  1. Deploy the `duckduckgo-mcp-server` package in your own infrastructure.
  2. Expose it through a public HTTPS MCP gateway because VoxOS cannot register
     local stdio commands.
  3. Add gateway-level rate limiting and bearer auth.
  4. Register the gateway URL in VoxOS and test discovery.
- Notes:
  - The package documents `query`, `count`, and `safeSearch` inputs.
  - Treat reliability and rate limits as best-effort compared with paid search
    APIs.
- Sources:
  - [duckduckgo-mcp-server package](https://www.npmjs.com/package/duckduckgo-mcp-server)
  - [duckduckgo-mcp-server repository](https://github.com/zhsama/duckduckgo-mcp-server)

### Brave Search MCP

- Best for: official search API results, web search, local search.
- Cost posture: Brave Search API has a free tier with an API key; paid tiers are
  available for higher usage.
- VoxOS setup:
  - Catalog preset: `MCP Gateway`
  - Direct remote URL: none. The official package is stdio-first.
  - Self-hosted VoxOS URL: your public HTTPS gateway URL, for example
    `https://brave-search-mcp.example.com/mcp`
  - Transport: depends on your gateway; prefer `streamable_http`.
  - Auth to VoxOS: `static_bearer` if your gateway enforces bearer auth.
  - Suggested `allowed_tools` after discovery: `brave_web_search`,
    `brave_local_search`
- Service setup:
  1. Create a Brave Search API account and API key.
  2. Run `@modelcontextprotocol/server-brave-search` with `BRAVE_API_KEY`
     configured in the server environment.
  3. Expose it through a public HTTPS MCP gateway.
  4. Register the gateway URL in VoxOS and test discovery.
- Notes:
  - Keep the Brave API key on the hosted server. VoxOS only needs credentials
    for your gateway if the gateway requires them.
- Sources:
  - [Brave Search API](https://brave.com/search/api/)
  - [Brave Search API keys](https://api.search.brave.com/app/keys)
  - [Official Brave MCP package](https://www.npmjs.com/package/@modelcontextprotocol/server-brave-search)

## Browser Automation

### Browserless

- Best for: testing hosted browser control, page scraping, search, site maps,
  exports, and performance audits without running an MCP server.
- Cost posture: the free plan includes 1,000 units per month, two concurrent
  browsers, one-minute maximum sessions, and one day of persisted sessions and
  logs. One unit covers up to 30 seconds of browser time; reconnects, proxies,
  and CAPTCHA solving can consume additional units.
- VoxOS setup:
  - URL: `https://mcp.browserless.io/mcp`
  - Transport: `streamable_http`
  - Auth: `static_bearer` with a Browserless API token.
  - Starter `allowed_tools`: `browserless_smartscraper`,
    `browserless_search`, `browserless_map`, `browserless_performance`
  - Add `browserless_agent` only when an agent needs logins or multi-step
    browser control.
- Service setup:
  1. Create a free Browserless account and copy an API token.
  2. Choose **Browserless** in the VoxOS MCP catalog and store the token as
     bearer auth.
  3. Test discovery, then grant `browserless_agent` only to a trusted agent
     when browser control is required.
- Notes:
  - The browser runs in Browserless cloud. Use dedicated, least-privilege site
    accounts for login testing and avoid exposing browser control or arbitrary
    JavaScript tools broadly.
  - Keep the token in VoxOS encrypted credential storage, not in the MCP URL.
- Sources:
  - [Browserless MCP setup](https://docs.browserless.io/mcp/browserless-mcp-server/setup)
  - [Browserless browser agent](https://docs.browserless.io/mcp/browser-agent)
  - [Browserless REST API tools](https://docs.browserless.io/mcp/rest-api-tools)
  - [Browserless pricing](https://cloud.browserless.io/pricing)

### Browser Use Cloud MCP

- Best for: managed browser agent sessions, high-level browser tasks, session
  inspection.
- Cost posture: Browser Use Cloud account and API key required; usage depends on
  the Browser Use Cloud plan.
- VoxOS setup:
  - URL: `https://api.browser-use.com/v3/mcp`
  - Transport: `streamable_http`
  - Auth: `api_key_header` with header name `x-browser-use-api-key`
  - Suggested `allowed_tools`: `run_session`, `get_session`, `send_task`,
    `stop_session`, `get_session_messages`, `list_sessions`,
    `list_browser_profiles`
- Service setup:
  1. Create a Browser Use Cloud account.
  2. Create an API key in the Browser Use Cloud settings page.
  3. Register the MCP URL in VoxOS with the `x-browser-use-api-key` header.
  4. Test discovery and start with session tools only.
- Notes:
  - Browser Use also has local open-source MCP options, but those are stdio
    servers and need the same `MCP Gateway` setup before VoxOS can use them.
- Sources:
  - [Browser Use Cloud MCP server](https://docs.browser-use.com/cloud/guides/mcp-server)
  - [Browser Use Cloud quickstart](https://docs.browser-use.com/cloud/quickstart)
  - [Browser Use local MCP server](https://docs.browser-use.com/customize/mcp-server)
  - [Browser Use API keys](https://cloud.browser-use.com/settings?tab=api-keys&new=1)

### Browserbase MCP

- Best for: hosted browser automation through Browserbase and Stagehand.
- Cost posture: Browserbase account and API key required; usage depends on the
  Browserbase plan.
- VoxOS setup:
  - URL: `https://mcp.browserbase.com/mcp`
  - Transport: `streamable_http`
  - Auth: `static_bearer` with the Browserbase API key.
  - Suggested `allowed_tools`: `start`, `navigate`, `observe`, `act`,
    `extract`, `end`
- Service setup:
  1. Create a Browserbase account and API key.
  2. Register the Browserbase MCP URL in VoxOS.
  3. Add optional query parameters only when needed, for example model,
     keep-alive, proxy, or stealth settings.
  4. Test discovery and keep write/control tools limited to browser agents.
- Notes:
  - Browserbase also accepts an `x-bb-api-key` header, but `static_bearer`
    matches its documented `Authorization: Bearer ...` form and is simpler in
    VoxOS.
- Sources:
  - [Stagehand Browserbase MCP setup](https://docs.stagehand.dev/v3/integrations/mcp/setup)
  - [Browserbase](https://www.browserbase.com)

### Playwright MCP

- Best for: self-hosted browser automation, deterministic UI testing, browser
  snapshots and screenshots.
- Cost posture: free/self-hosted.
- VoxOS setup:
  - Catalog preset: `MCP Gateway`
  - Direct hosted URL: none.
  - Self-hosted VoxOS URL: your public HTTPS URL that proxies Playwright MCP,
    for example `https://playwright-mcp.example.com/mcp`
  - Transport: `streamable_http`
  - Auth: `static_bearer` if your proxy enforces bearer auth.
  - Suggested starter `allowed_tools`: `browser_navigate`, `browser_snapshot`,
    `browser_click`, `browser_type`, `browser_fill_form`,
    `browser_wait_for`, `browser_take_screenshot`, `browser_close`
- Service setup:
  1. Run the official package in HTTP mode, for example
     `npx @playwright/mcp@latest --port 8931`.
  2. Put it behind HTTPS and gateway auth. VoxOS cannot register
     `http://localhost:8931/mcp`.
  3. Register the HTTPS gateway URL in VoxOS and test discovery.
  4. Give each agent a narrow set of browser tools.
- Notes:
  - The Playwright MCP README states that the server is not a security
    boundary. Isolate it from sensitive browser profiles and internal networks.
  - Do not allow `browser_run_code_unsafe` to normal agents. It is equivalent to
    arbitrary code execution in the MCP server process.
- Sources:
  - [Microsoft Playwright MCP README](https://github.com/microsoft/playwright-mcp)

### Local Browser Use MCP

- Best for: self-hosting open-source browser-use agents with your own LLM keys.
- Cost posture: free/open-source software, but you pay for browser
  infrastructure and LLM/API usage.
- VoxOS setup:
  - Catalog preset: `MCP Gateway`
  - Direct remote URL: none. The local server is stdio-first.
  - Self-hosted VoxOS URL: your public HTTPS gateway URL.
  - Transport: depends on your gateway; prefer `streamable_http`.
  - Auth: `static_bearer` if your gateway enforces bearer auth.
  - Suggested `allowed_tools`: start with read/navigation tools such as
    `browser_navigate`, `browser_get_state`, `browser_extract_content`; add
    click/type/session-closing tools only for trusted agents.
- Service setup:
  1. Run the local Browser Use MCP server with the required LLM environment
     variables, such as OpenAI or Anthropic API keys.
  2. Expose it through a public HTTPS MCP gateway.
  3. Register the gateway URL in VoxOS and test discovery.
- Notes:
  - Prefer Browser Use Cloud if you want a managed endpoint and simpler VoxOS
    setup.
- Sources:
  - [Browser Use local MCP server](https://docs.browser-use.com/customize/mcp-server)
  - [browser-use-mcp package](https://www.npmjs.com/package/browser-use-mcp)

## Web Scraping And Actor Platforms

### Apify MCP

- Best for: Actor marketplace access, scraping workflows, documentation search,
  RAG web browsing.
- Cost posture: Apify account required; usage depends on Apify plans and Actor
  costs.
- VoxOS setup:
  - URL: `https://mcp.apify.com`
  - Transport: `streamable_http`
  - Preferred auth: `oauth_authorization_code`
  - Token auth: `static_bearer` with an Apify API token.
  - Suggested starter `allowed_tools`: `search-actors`,
    `fetch-actor-details`, `search-apify-docs`, `fetch-apify-docs`,
    `apify/rag-web-browser`, `get-actor-output`
- Service setup:
  1. Create an Apify account.
  2. Use OAuth where possible, or create an Apify API token in the Apify console.
  3. Narrow the remote server with query parameters when appropriate, for
     example `https://mcp.apify.com?tools=actors,docs,apify/rag-web-browser`.
  4. Register the URL in VoxOS and test discovery.
- Notes:
  - SSE is deprecated for Apify MCP. Use Streamable HTTP.
  - Do not expose `call-actor` or broad Actor write/run tools to general agents
    until the specific workflow and costs are reviewed.
- Sources:
  - [Apify MCP server documentation](https://docs.apify.com/platform/integrations/mcp)
  - [Apify MCP configurator](https://mcp.apify.com/)

## Developer And Operations Context

### GitHub MCP

- Best for: repository context, issues, pull requests, Actions, code search.
- Cost posture: GitHub account required; GitHub Copilot remote MCP endpoint is
  provided by GitHub.
- VoxOS setup:
  - General URL: `https://api.githubcopilot.com/mcp/`
  - Read-only default URL: `https://api.githubcopilot.com/mcp/readonly`
  - Read-only repo URL: `https://api.githubcopilot.com/mcp/x/repos/readonly`
  - Read-only issues URL: `https://api.githubcopilot.com/mcp/x/issues/readonly`
  - Read-only pull requests URL:
    `https://api.githubcopilot.com/mcp/x/pull_requests/readonly`
  - Transport: `streamable_http`
  - Auth: `static_bearer` with a fine-grained GitHub PAT.
  - Suggested `allowed_tools`: discover after test and start with read-only
    repo, issue, and pull request tools.
- Service setup:
  1. Create a fine-grained PAT with the minimum repositories and permissions.
  2. Pick the narrowest URL path variant that covers the workflow.
  3. Register the server in VoxOS and test discovery.
  4. Add write tools only for agents explicitly trusted to mutate GitHub state.
- Notes:
  - GitHub documents OAuth for hosts that configure their own GitHub app, but
    PAT bearer auth is the practical default for VoxOS unless host-level OAuth
    app configuration is added.
- Sources:
  - [GitHub MCP server README](https://github.com/github/github-mcp-server)
  - [GitHub remote MCP server docs](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)

### Sentry MCP

- Best for: debugging errors, reading issues/events, stack traces, traces, and
  guided Sentry workflows.
- Cost posture: Sentry account required.
- VoxOS setup:
  - URL: `https://mcp.sentry.dev/mcp`
  - Transport: `streamable_http`
  - Preferred auth: `oauth_authorization_code`
  - Suggested starter `allowed_tools`: `use_sentry`, `find_organizations`,
    `find_projects`, `search_issues`, `get_issue_details`,
    `get_event_stacktrace`
- Service setup:
  1. Register the Sentry remote MCP URL in VoxOS.
  2. Use OAuth. The server advertises OAuth metadata, dynamic registration, PKCE,
     and bearer-token resource metadata.
  3. Test discovery and keep issue mutation tools disabled until needed.
- Notes:
  - Sentry describes the MCP server as optimized for coding-agent debugging
    workflows rather than as a complete Sentry API wrapper.
  - If self-hosting the Sentry MCP server, create a Sentry user auth token with
    only the scopes required for the tools you expose.
- Sources:
  - [Sentry MCP server README](https://github.com/getsentry/sentry-mcp)
  - [Sentry docs index for MCP](https://docs.sentry.io/llms.txt)
  - [Sentry MCP OAuth metadata](https://mcp.sentry.dev/.well-known/oauth-authorization-server)

### Linear MCP

- Best for: issue, project, comment, and team workflows in Linear.
- Cost posture: Linear account required; availability can depend on the Linear
  plan and client.
- VoxOS setup:
  - URL: `https://mcp.linear.app/mcp`
  - Transport: `streamable_http`
  - Preferred auth: `oauth_authorization_code`
  - Token auth: `static_bearer` with a Linear API key or OAuth access token.
  - Suggested `allowed_tools`: discover after test; start with read/search tools
    for issues, projects, teams, and users.
- Service setup:
  1. Register the Linear remote MCP URL in VoxOS.
  2. Prefer OAuth for user-scoped access.
  3. If using token auth, create a restricted Linear API key and store it as a
     bearer token.
  4. Add create/update/comment tools only for trusted agents.
- Sources:
  - [Linear MCP documentation](https://linear.app/docs/mcp)

## Productivity And Collaboration

### Google Workspace

- Best for: Gmail, Google Drive, Google Calendar, Google Chat, and People tools.
- Availability: Google Workspace MCP is currently part of the
  [Google Workspace Developer Preview Program](https://developers.google.com/workspace/preview).
- VoxOS setup:
  1. Complete the one-time Google Cloud, API, consent-screen, and OAuth-client
     setup in the canonical
     [Google Workspace setup guide](wiki/MCP-And-Tools.md#google-workspace-setup).
  2. Add one Voxos server for each Workspace product because Google exposes a
     separate MCP endpoint for each product.
  3. Reuse the same Google OAuth web client ID and client secret for every
     selected product.
  4. Authorize and test each connection, then grant only the discovered tools
     the agent needs.
- Sources:
  - [Configure the Google Workspace MCP servers](https://developers.google.com/workspace/guides/configure-mcp-servers)
  - [Supported Google Workspace MCP products and tools](https://developers.google.com/workspace/guides/configure-mcp-servers#support-products)

## Knowledge And Documents

### Notion MCP

- Best for: workspace search, pages, databases/data sources, comments, users,
  teams, meeting notes.
- Cost posture: Notion account required; some search and query features depend
  on Notion AI, Business, or Enterprise plan capabilities.
- VoxOS setup:
  - URL: `https://mcp.notion.com/mcp`
  - Transport: `streamable_http`
  - Auth: `oauth_authorization_code`
  - Suggested starter `allowed_tools`: `notion-search`, `notion-fetch`,
    `notion-get-users`, `notion-get-teams`, `notion-get-comments`
- Service setup:
  1. Register the Notion remote MCP URL in VoxOS.
  2. Complete OAuth and choose the Notion workspace/pages the user should grant.
  3. Test discovery.
  4. Add write tools such as `notion-create-pages`, `notion-update-page`, or
     `notion-create-comment` only for agents that should edit Notion.
- Notes:
  - Notion MCP has the same effective access as the connected Notion user.
    Treat it as sensitive workspace data.
  - The legacy SSE endpoint is `https://mcp.notion.com/sse`; use Streamable HTTP
    unless a client requires SSE.
- Sources:
  - [Notion MCP overview](https://developers.notion.com/docs/mcp)
  - [Notion MCP setup guide](https://developers.notion.com/guides/mcp/get-started-with-mcp)
  - [Notion MCP supported tools](https://developers.notion.com/guides/mcp/mcp-supported-tools)
  - [Notion MCP security practices](https://developers.notion.com/guides/mcp/mcp-security-best-practices)

## Data And Analytics

### MotherDuck MCP

- Best for: SQL over MotherDuck/DuckDB, catalog search, table/schema discovery,
  docs Q&A, data workflows.
- Cost posture: MotherDuck account required; usage depends on MotherDuck plan.
- VoxOS setup:
  - URL: `https://api.motherduck.com/mcp`
  - Transport: `streamable_http`
  - Auth: `oauth_authorization_code`
  - Suggested starter `allowed_tools`: `list_databases`, `list_tables`,
    `list_columns`, `search_catalog`, `query`, `ask_docs_question`
- Service setup:
  1. Register the MotherDuck remote MCP URL in VoxOS.
  2. Complete OAuth for the MotherDuck account.
  3. Test discovery.
  4. Keep read-write tools disabled unless the agent is explicitly allowed to
     modify data.
- Notes:
  - MotherDuck's remote MCP exposes both read-only and read-write capabilities.
    Do not allow `query_rw`, Dives mutation tools, or Flights
    create/update/delete/run tools by default.
  - If self-hosting the MotherDuck MCP server, prefer its HTTP transport and
    still put it behind public HTTPS for VoxOS.
- Sources:
  - [MotherDuck MCP documentation](https://motherduck.com/docs/sql-reference/mcp/)
  - [MotherDuck MCP server repository](https://github.com/motherduckdb/mcp-server-motherduck)

## Payments And Personal Finance

### Monzo

- Best for: personal account discovery, balances, pots, and bounded transaction
  reads through a stateless self-hosted MCP container.
- Cost posture: no separate MCP fee; Monzo Developer API eligibility and limits
  apply.
- Local setup:
  1. Create an owner-only MCP endpoint-bearer file outside the repository.
  2. Start Voxos and `make mcp-stack-up`.
  3. Create a confidential client in the Monzo developer portal with the exact
     Voxos callback URI shown by the setup form.
  4. Choose **Monzo** and **Local Compose**, enter the endpoint bearer and OAuth
     client details, then approve the connection in the Monzo mobile app.
  5. Test discovery and explicitly grant only the required read tools.
- Voxos setup:
  - URL: `http://monzo-mcp:8000/mcp`
  - Transport: `streamable_http`
  - Endpoint auth: `static_bearer` using the local MCP endpoint bearer.
  - Provider auth: Voxos-owned authorization-code OAuth. Voxos sends a
    short-lived assertion in `X-MCP-Credential-Delegation`; the container uses
    it only with the private Voxos credential broker.
  - Starter `allowed_tools`: none.
- Notes:
  - One container can serve multiple Voxos users because it stores no provider
    credentials, but neither the endpoint nor broker route should be exposed
    through untrusted ingress.
  - The Compose configuration disables all financial writes. Pot transfers also
    require MCP elicitation, which Voxos does not currently provide.
  - Balances and transaction details enter model context and may be retained in
    durable chats, runs, transcripts, and tool events.
  - Monzo requires mobile Strong Customer Authentication approval. After the
    first five minutes, transaction access is limited to the documented
    90-day window, and protected pots cannot be withdrawn through the API.
  - Voxos serializes refreshes by locking the tenant's OAuth token row and
    atomically stores Monzo's rotated refresh token. The container serves native
    Streamable HTTP and needs no stdio gateway.
- Sources:
  - [Official Monzo API documentation](https://docs.monzo.com/)
  - [Monzo MCP repository](https://github.com/s-block/monzo-mcp)

## Services To Treat Carefully

- Browser automation servers: isolate browser profiles, avoid internal network
  access, and keep screenshots/session data out of broad agent contexts unless
  needed.
- Broad actor/scraper platforms: Apify and Firecrawl can trigger long-running or
  costly work. Start with search/read tools and add execution tools only for a
  reviewed workflow.
- Write-capable workspaces: GitHub, Linear, Notion, MotherDuck, and Sentry all
  expose mutation tools. Keep starter `allowed_tools` read-only.
- Personal finance: Monzo balances and transactions are highly sensitive even
  when tools are read-only. Grant them only to the intended agent and account
  for durable model/tool-event retention.
- Stdio-only community servers: DuckDuckGo, Brave Search, and local Browser Use
  are useful, but VoxOS needs a public HTTPS bridge. Do not register a local
  command or localhost URL.
- Unsafe tool names: deny tools with names that imply arbitrary code execution,
  shell access, raw HTTP/header access, credential reads, cookie/local storage
  reads, or broad deletion unless a specific workflow justifies them.

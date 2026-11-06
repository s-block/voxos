# Give your AI agent web search with MCP

Last updated: 2026-11-06

Every new Voxos account can search the web straight away through Exa's keyless
MCP server, and you can add an API key or switch providers when you need to.

## What you get by default

New accounts start with Exa's keyless endpoint connected for `web_search_exa` and
`web_fetch_exa`, in both chat and voice. Keyless access has Exa's free-plan
limits. See [Default web search](../wiki/MCP-And-Tools.md#default-web-search).

## 1. Try it

In Chat, ask a question about something recent, and ask for sources. If you want
no web search at all, disable the Exa server under **MCP**.

## 2. Raise your limits

Create a key in the [Exa dashboard](https://dashboard.exa.ai/api-keys). Then, in
**MCP**, use the catalog's API-key option for Exa, with `api_key_header` auth and
the header name `x-api-key`. Test it and keep the same two allowed tools. The
[Exa entry in the catalog](../mcp-service-catalog.md#exa) has the details.

## 3. Choose a provider

Tavily, Firecrawl and Browserless can also do web search. Connect the one you
want from the [MCP service catalog](../mcp-service-catalog.md), and grant its
search tool to your agent.

Then open **Tools**. Automatic routing uses the first authorized provider in this
order: Exa, Tavily, Firecrawl, Browserless. You can also pin one exact tool. A
pinned route never falls back, so if that tool stops working, the agent has no
web search until you change it. See
[Tool approval flow](../wiki/MCP-And-Tools.md#tool-approval-flow).

## Next steps

- Load the [evidence-backed web research skill](../../examples/skills/README.md).
- Connect your code with the [GitHub guide](GitHub-MCP-Agent.md).

# Connect your AI agent to GitHub with MCP

Last updated: 2026-11-06

Voxos connects to GitHub's remote MCP server, so your agent can read
repositories, issues and pull requests with only the access you allow.

This guide starts read-only. Add write tools later, and only for agents you trust
to change things on GitHub.

## What you need

- A local Voxos stack from the [Quick Start](../wiki/Quick-Start.md).
- A GitHub account that can create a fine-grained personal access token.

## 1. Create a narrow token

In GitHub, create a fine-grained token with the fewest repositories and
permissions the job needs.

## 2. Pick the narrowest URL

GitHub offers read-only variants of its MCP endpoint. Choose the smallest one
that covers what you want:

| Access | URL |
| --- | --- |
| Read-only default | `https://api.githubcopilot.com/mcp/readonly` |
| Repositories | `https://api.githubcopilot.com/mcp/x/repos/readonly` |
| Issues | `https://api.githubcopilot.com/mcp/x/issues/readonly` |
| Pull requests | `https://api.githubcopilot.com/mcp/x/pull_requests/readonly` |

## 3. Add the server

Open **MCP** and add a server with the URL you chose, the `streamable_http`
transport, and `static_bearer` auth with your token. Voxos encrypts the token in
your account's secret vault. The
[GitHub entry in the catalog](../mcp-service-catalog.md#github-mcp) has the same
settings.

## 4. Test it and allow tools

Test the server so Voxos can discover its tools. Set the allowed tools, starting
with the read-only ones you need, and approve them for your agent. An agent only
gets tools you have explicitly allowed. See
[Tool approval flow](../wiki/MCP-And-Tools.md#tool-approval-flow).

## 5. Ask it something

In Chat, try: "Summarize the open pull requests in my repo." For a repeatable
routine, load the [example skills](../../examples/skills/README.md), which include
GitHub repository orientation and pull request review.

## If something goes wrong

- **Discovery fails.** Check the token hasn't expired and that it covers the
  repository. See [MCP and tools](../wiki/MCP-And-Tools.md#remote-mcp-servers).
- **The agent says it has no such tool.** Check the tool is both allowed on the
  server and granted to that agent.

## Next steps

- Connect other services from the [MCP service catalog](../mcp-service-catalog.md).
- Read the [web search guide](Web-Search-Agent.md) to choose a search provider.

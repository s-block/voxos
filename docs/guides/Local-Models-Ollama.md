# Run a self-hosted AI agent with Ollama

Last updated: 2026-11-06

Voxos can use a model running in Ollama on your own machine for text chat, tools
and scheduled runs, through its OpenAI-compatible provider.

This covers text. Realtime voice currently runs on OpenAI, Gemini or xAI, so
voice calls still use one of those.

## What you need

- A local Voxos stack from the [Quick Start](../wiki/Quick-Start.md).
- [Ollama](https://docs.ollama.com/), and a machine that can run a model with
  tool calling. Ollama's page puts `gpt-oss:20b` at about 16 GB of memory. Pick a
  smaller tool-capable model if that won't fit.

## 1. Start the model

```bash
ollama pull gpt-oss:20b
curl http://localhost:11434/v1/models
```

The second command should list the model.

## 2. Let the containers reach Ollama

With `make up`, Voxos runs in containers, so `localhost` means the container
rather than your machine. Ollama has to listen where the containers can reach it,
and how you set that depends on your system. Follow the steps for
[Linux](../wiki/Self-Hosted-Models.md#linux),
[macOS](../wiki/Self-Hosted-Models.md#macos) or
[Windows](../wiki/Self-Hosted-Models.md#windows).

Never leave an unauthenticated model server open to the internet. Restrict the
port with a firewall.

## 3. Trust the endpoint

Voxos refuses local addresses until you register the exact URL. Set this in
`.env`, then run `make up`:

```dotenv
OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON=[{"purpose":"text_provider","kind":"local_container","url":"http://host.docker.internal:11434/v1"}]
```

## 4. Add the provider

Open **Providers**, find **OpenAI compatible**, and enter:

```text
Base URL: http://host.docker.internal:11434/v1
Model ID: gpt-oss:20b
API key:  leave empty
```

Save it. Then open **Agent** and select the provider and model.

## 5. Try it

Open **Chat** and ask something. Then ask for something that needs a tool, such
as a web search. Models vary in how well they call tools, so test the ones you
care about before leaving them on a schedule.

## If something goes wrong

- **The endpoint is rejected.** The saved URL has to match the trusted URL
  exactly. See [Endpoint reachability](../wiki/Self-Hosted-Models.md#endpoint-reachability).
- **Other problems.** [Self-Hosted Models](../wiki/Self-Hosted-Models.md) covers
  LM Studio, vLLM and llama.cpp too.

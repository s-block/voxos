# Self-Hosted Models

Last updated: 2026-08-26

Voxos can use an explicitly registered local model server or a public HTTPS
endpoint through one generic **OpenAI compatible** text provider. The server
must implement the streaming
`POST /v1/chat/completions` contract and structured tool calls.
Authentication is optional; when a bearer key is entered, Voxos stores it in
the encrypted user vault and sends it only to that configured endpoint.

These instructions were checked against the linked official runtime
documentation on 2026-08-19.

## Why One OpenAI-Compatible Provider

Ollama, LM Studio, vLLM, llama.cpp, and many hosted gateways expose an
OpenAI-compatible API. A single endpoint-based integration keeps the Voxos
runtime independent of the model runner and supports both local and non-local
servers.

Voxos requires an explicit list of model IDs. `GET /v1/models` is useful for
finding those IDs, but Voxos does not import it automatically because API
compatibility and the models' tool behavior vary between runtimes. A model
listed by the endpoint is not necessarily capable of reliable structured tool
calling.

Custom models are treated as text and tool-calling models. Voxos does not infer
image input, browser-execution, or image-generation support from a model name.
Use a model and server configuration whose documentation explicitly supports
streaming tool calls. Test important tools before relying on scheduled or
unattended runs.

## Configure Voxos

1. Start the model server and load or pull a tool-capable instruct model.
2. Verify its OpenAI-compatible model list from the machine or container that
   runs the Voxos backend.
3. Open **Providers** in Voxos.
4. Find **OpenAI compatible**.
5. For a local or private endpoint, register the complete base URL as described
   in [Endpoint Reachability](#endpoint-reachability).
6. Enter the API base URL, including its `/v1` path.
7. Enter one model ID per line. Commas are also accepted.
8. Enter a bearer API key when the server requires one. Leave it empty for an
   endpoint that intentionally has no authentication.
9. Save the endpoint, then open **Agent** and select the provider, primary
   model, allowed subagent models, and default subagent model.

Example values for Ollama running on the same host as the Voxos Compose stack:

```text
Base URL: http://host.docker.internal:11434/v1
Model ID: gpt-oss:20b
API key:  leave empty
```

The URL is resolved by the Voxos API and durable run worker, not by the web
browser. `localhost` therefore means the backend process or its container.

## Endpoint Reachability

Unregistered endpoints must use HTTPS on port 443, contain no URL credentials,
query, or fragment, and resolve only to global IP addresses. Requests do not
follow redirects. Local, private, link-local, and non-standard-port endpoints
therefore require an exact deployment-owned registration.

For host or Compose development, add complete base URLs to the JSON array in
`OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON`. Each entry must use the
`text_provider` purpose and `local_container` kind:

```dotenv
OUTBOUND_ADDITIONAL_TRUSTED_ENDPOINTS_JSON=[{"purpose":"text_provider","kind":"local_container","url":"http://host.docker.internal:11434/v1"}]
```

Use `http://localhost:<port>/v1` instead when Voxos runs directly on the host.
The saved URL must normalize to the registered URL; another path, port, host,
or endpoint purpose is not trusted.

Root Docker Compose configures `host.docker.internal` and maps it to the host
gateway. Use:

```text
http://host.docker.internal:<port>/v1
```

For a server on another private machine, register its exact URL as a
`local_container` endpoint and ensure every API and durable-worker replica has a
network route to it.

Never expose an unauthenticated model server to the public internet. Binding a
server to `0.0.0.0` can expose it to the LAN; use that only when a container or
another machine must reach it, then restrict the listening port with a host
firewall and enable authentication or a protected reverse proxy.

## Ollama: Recommended Cross-Platform Start

Ollama is the simplest common setup across Linux, macOS, and Windows. Its
[OpenAI compatibility reference](https://docs.ollama.com/api/openai-compatibility)
documents Chat Completions, streaming, tools, and `/v1/models`. Its
[tool-calling guide](https://docs.ollama.com/capabilities/tool-calling) covers
single, parallel, multi-turn, and streamed tool calls.

`gpt-oss:20b` is a useful tool-capable starting point for machines with enough
memory. Ollama's current model page describes the 20B variant as fitting in
about 16 GB of memory. Choose a smaller tool-capable model if the host cannot
run it comfortably.

### Linux

Install with the current command from the
[official Linux guide](https://docs.ollama.com/linux):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gpt-oss:20b
curl http://localhost:11434/v1/models
```

The installer normally creates a system service. If the Voxos backend runs
directly on the host, keep Ollama on its default loopback listener and use
`http://localhost:11434/v1`.

For a containerized Voxos backend, Ollama must listen on an address reachable
through the host gateway. Run `sudo systemctl edit ollama.service` and add:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Then reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

Restrict port `11434` to the Docker host/network before using this listener.

### macOS

Install the current application from the
[official macOS guide](https://docs.ollama.com/macos), start it, then run:

```bash
ollama pull gpt-oss:20b
curl http://localhost:11434/v1/models
```

The current Ollama requirement is macOS 14 or newer. Apple Silicon uses CPU and
GPU acceleration; Intel Macs use CPU execution.

For host-mode Voxos, use `http://localhost:11434/v1`. If Docker Desktop cannot
reach the default loopback listener, follow the official
[Ollama server configuration](https://docs.ollama.com/faq) and set:

```bash
launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
```

Restart the Ollama application, use
`http://host.docker.internal:11434/v1` in Voxos, and keep the port protected
from untrusted LAN clients.

### Windows

Install `OllamaSetup.exe` from the
[official Windows guide](https://docs.ollama.com/windows). The current native
application supports Windows 10 22H2 or newer and makes `ollama` available in
PowerShell and Command Prompt. Then run:

```powershell
ollama pull gpt-oss:20b
Invoke-RestMethod http://localhost:11434/v1/models
```

Use `http://localhost:11434/v1` when Voxos runs directly in Windows. For Docker
Desktop, create the user environment variable
`OLLAMA_HOST=0.0.0.0:11434`, restart Ollama, and configure
`http://host.docker.internal:11434/v1`. Limit inbound access to the local
machine and Docker networking in Windows Firewall.

## LM Studio: Desktop Alternative

LM Studio is a GUI-first alternative on supported macOS, Windows, and Linux
systems. Its current
[system requirements](https://lmstudio.ai/docs/app/system-requirements) cover
Apple Silicon macOS, x64/ARM Windows, and x64/ARM64 Linux. The
[OpenAI-compatible endpoint guide](https://lmstudio.ai/docs/developer/openai-compat)
documents `/v1/models`, `/v1/responses`, and `/v1/chat/completions`.

After installing LM Studio, download and load a tool-capable model in the app,
open **Developer**, and start the server. The CLI equivalent is:

```bash
lms get openai/gpt-oss-20b
lms load openai/gpt-oss-20b
lms server start
```

The default base URL is `http://localhost:1234/v1`. Use the exact ID returned by
`http://localhost:1234/v1/models` in Voxos.

For Docker-host access, LM Studio's
[network serving guide](https://lmstudio.ai/docs/developer/core/server/serve-on-network)
uses:

```bash
lms server start --bind 0.0.0.0
```

Enable **Require Authentication** in LM Studio server settings, create an API
token, enter it in the Voxos provider card, and restrict the port before
binding beyond loopback.

## vLLM: Linux GPU Serving

vLLM is a strong choice for a dedicated Linux GPU host and higher-throughput
serving. Follow its frequently updated
[installation and serving quickstart](https://docs.vllm.ai/en/latest/getting_started/quickstart/)
for the accelerator and Python versions on the host.

For GPT-OSS tool calling, the current vLLM
[tool-calling guide](https://docs.vllm.ai/en/latest/features/tool_calling/)
requires automatic tool choice and the OpenAI parser:

```bash
uv venv --python 3.12 --seed
source .venv/bin/activate
uv pip install vllm --torch-backend=auto
vllm serve openai/gpt-oss-20b \
  --enable-auto-tool-choice \
  --tool-call-parser openai \
  --api-key replace-with-a-long-random-token
```

The default base URL is `http://localhost:8000/v1`. If Voxos runs in Docker,
add `--host 0.0.0.0`, use `http://host.docker.internal:8000/v1`, enter the same
API key in Voxos, and firewall the port. vLLM also publishes current
[official Docker images](https://docs.vllm.ai/en/latest/deployment/docker/) for
NVIDIA, AMD ROCm, and Intel XPU deployments.

## llama.cpp: Lightweight Manual Serving

llama.cpp provides prebuilt packages and binaries for Linux, macOS, and
Windows, plus multiple CPU and GPU backends. Its
[server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
documents the OpenAI-compatible Chat Completions and model-list endpoints.

Start a model with a stable API alias and tool-aware Jinja processing:

```bash
llama-server \
  -hf bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M \
  --alias qwen2.5-7b-instruct \
  --jinja \
  --port 8080
```

On Windows, use `llama-server.exe` with the same arguments. Configure
`http://localhost:8080/v1` and `qwen2.5-7b-instruct` in host-mode Voxos. The
official
[function-calling guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
explains compatible templates and model-specific overrides; tool reliability
depends on choosing the correct template.

## Verify Before Selecting The Provider

First list the models from the endpoint that the Voxos backend can reach:

```bash
curl http://host.docker.internal:11434/v1/models
```

Then test a streamed tool call against the chosen runtime. A basic text reply
alone does not prove the model can operate a Voxos agent. Confirm that the
response emits structured `tool_calls`, accepts a later `tool` role result, and
returns a final answer.

After saving the provider and Agent defaults, create a new chat and ask for a
low-risk built-in read action that is granted to the agent. Review the Chat
activity panel for the structured tool call and result. Run the same check with
the durable run worker before enabling schedules.

## Operational Guidance

- Size model weights, KV cache, and context length to fit available RAM/VRAM.
- Prefer an instruct model explicitly trained for function calling.
- Pin model IDs or artifacts for repeatable unattended runs.
- Keep the server and model runtime current, but retest streaming and tools
  before upgrading production.
- Put remote endpoints behind TLS and bearer authentication.
- Treat prompts, attachments, tool schemas, and tool results as user data; the
  endpoint receives all content needed for each run.
- Monitor queueing, time to first token, context exhaustion, empty responses,
  malformed tool calls, and worker timeouts.
- Do not assume an endpoint's `/v1/models` result proves model capability.

See [Troubleshooting](Troubleshooting.md) for container networking and endpoint
policy errors.

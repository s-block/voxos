# Providers And Keys

Last updated: 2026-08-24

Provider keys are user-owned secrets. The frontend sends them once to the API.
The API encrypts them through `UserSecretVaultService` and stores ciphertext and
encryption metadata in Postgres. API responses return only safe summaries such
as whether a provider is configured and the last four characters when available.

## Providers Page

The Providers page is the central place to save provider credentials. Cards are
named for the provider, then list the Voxos text and voice integrations that
currently use that key. Image generation reuses the compatible OpenAI, xAI, or
NVIDIA text credential. Each card includes:

- Provider label and supported capabilities.
- Saved or required credential state.
- Default models and voice where applicable.
- Voice runtime readiness where applicable.
- Password input for the provider key.
- Save or replace action.
- Provider docs link.

Saving a key does not change an agent's text or voice defaults. The Agent page
selects those defaults independently, including the model and voice for voice
sessions. OpenAI and xAI cards share one credential across their supported text
voice, and image capabilities. NVIDIA API Catalog supports text chat and hosted
image generation with the same key.

Current providers:

| Provider | Capabilities | Key label | Runtime |
| --- | --- | --- | --- |
| OpenAI | Voice, text, and image generation/editing | OpenAI API key | Ready |
| OpenAI compatible | Local or remote text through a user-entered endpoint and model list | Optional bearer API key | Ready |
| Google | Voice | Google API key | Ready for voice |
| Hume | Voice | Hume API key | Adapter needed |
| ElevenLabs | Voice | ElevenLabs API key | Adapter needed |
| Mistral | Voice | Mistral API key | Adapter needed |
| xAI | Voice, text, and image generation/editing | xAI API key | Ready |
| NVIDIA API Catalog | Text chat and hosted image generation | NVIDIA API key | Ready |

## Text Provider Keys

Current text providers are OpenAI, OpenAI compatible, xAI, and NVIDIA API
Catalog. Chat uses the
Agent text default for new chats, while a chat can explicitly select a different
configured text provider and model without changing the agent default.

The Agent page receives provider model metadata from the API and shows the
current model description, recommendation, pricing summary, and explicit image,
tool-calling, and browser-use capabilities. It also stores the active
provider's allowed subagent models and default delegated model. These are model
policy settings only; plaintext credentials never enter that configuration.
Agent and Chat model selectors use the same provider model metadata as their
availability source.

The OpenAI-compatible provider accepts one local or remote API base URL and an
explicit list of model IDs. It uses streaming Chat Completions with structured
tool calls. A bearer key is optional and is encrypted in the same user vault
when supplied. The base URL can use a local or private address only when the
deployment registers that complete URL. Unregistered endpoints require public
HTTPS on port 443 and globally routable DNS; redirects are rejected. Custom
model IDs do not imply image input, browser execution, or image generation. See
[Self-Hosted Models](Self-Hosted-Models.md) for the current Linux, macOS,
Windows, Docker, Ollama, LM Studio, vLLM, and llama.cpp setup guide.

Agent settings also expose an independent image-generation provider and model
default. The API returns a separate output-image catalog so image generation is
not confused with a chat model's image-input capability. The current catalogs
include OpenAI GPT Image 2; xAI Grok Imagine Image Quality and Grok Imagine
Image; and NVIDIA NIM's FLUX.2 Klein 4B, FLUX.1 Schnell, and FLUX.1 Dev. Each
entry includes provider documentation, pricing or deployment guidance, and
separate model and hosted-endpoint editing capabilities. Saving an explicit
image default requires a stored credential compatible with that provider,
while the active text provider and model remain unchanged.

When no explicit image default is saved, an enabled NVIDIA credential makes
FLUX.2 Klein 4B the automatic generation default. Without NVIDIA, routing uses
an enabled compatible provider, preferring the active text provider before
other enabled providers. Hosted private-image edits use OpenAI GPT Image 2 or
xAI Grok Imagine. FLUX.2 Klein's model supports multi-reference edits in a
self-hosted NVIDIA NIM, but NVIDIA API Catalog's hosted preview is prompt-only
for arbitrary user images, so Voxos does not send private references to that
endpoint.

The curated OpenAI text catalog contains the GPT-5.6 Sol, Terra, and Luna tiers,
GPT-5.5, GPT-5.4 mini, and GPT-5.4 nano. OpenAI documents each model as
supporting streaming Responses API calls and function calling; Voxos generates
the typed Responses request, function-call, and function-output items. OpenAI
response storage is disabled because Voxos persists the user-owned transcript
and runtime history. Deprecated, legacy, endpoint-specific, and superseded
model choices are intentionally omitted. GPT-5.4 mini is the OpenAI primary
default and recommended catalog choice; GPT-5.6 Terra remains the default for
delegated subagent work. Explicitly saved user model choices are preserved. xAI
text generation uses the Responses API. NVIDIA API Catalog text generation uses
its OpenAI-compatible Chat Completions endpoint with a curated catalog of text
and image-capable models suitable for Voxos chat, coding, reasoning, and agent
workflows. Specialized embedding, reranking, safety, speech, media-generation,
and OCR models are excluded. NVIDIA account availability, model terms, and
usage limits determine whether each model can be used with a saved key.

NVIDIA defaults ordinary chat and delegated text work to `openai/gpt-oss-20b`.
Its provider policy routes image-bearing chat turns and browser execution to
`stepfun-ai/step-3.7-flash`, while preserving an explicitly selected
browser-capable or image-capable model. Routing stays within the selected
provider and reuses that provider's encrypted credential.

## Security Rules

- Do not commit provider keys.
- Do not place provider keys in frontend environment files.
- Do not log plaintext provider keys.
- Do not reuse one user's vault credential for another user.
- Do not pass provider keys through tool arguments or LiveKit metadata.
- Do not expose an unauthenticated local model port to an untrusted network.

Secret lookup is scoped by owner user id, provider, credential id, and secret
purpose. The same fields form the authenticated encryption context, so a secret
cannot be decrypted under a different context.

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProviderModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    pricing_summary: str | None = Field(default=None, min_length=1, max_length=512)
    recommended: bool = False
    supports_image_input: bool = False
    supports_tool_calling: bool = False
    supports_browser_use: bool = False

    @model_validator(mode="after")
    def validate_browser_capabilities(self) -> "ProviderModelOption":
        if self.supports_browser_use and not (self.supports_image_input and self.supports_tool_calling):
            message = "Browser-use models must support image input and tool calling"
            raise ValueError(message)
        return self


class ProviderImageGenerationModelOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    pricing_summary: str | None = Field(default=None, min_length=1, max_length=512)
    docs_url: str = Field(min_length=1, max_length=2048)
    recommended: bool = False
    supports_image_editing: bool = False
    hosted_api_supports_image_editing: bool = False


OPENAI_REALTIME_MODEL_OPTIONS = (
    ProviderModelOption(
        model="gpt-realtime-2.1-mini",
        label="GPT Realtime 2.1 mini",
        description=(
            "Fast, cost-efficient realtime voice model with strong tool use, "
            "noise handling, interruptions, and alphanumeric accuracy."
        ),
        pricing_summary=(
            "Audio: $10 input, $0.30 cached input, and $20 output per 1M tokens. "
            "Text: $0.60 input, $0.06 cached input, and $2.40 output per 1M tokens."
        ),
        recommended=True,
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="gpt-realtime-2.1",
        label="GPT Realtime 2.1",
        description=(
            "Highest-quality OpenAI realtime voice model for demanding reasoning and tool-heavy conversations."
        ),
        pricing_summary=(
            "Audio: $32 input, $0.40 cached input, and $64 output per 1M tokens. "
            "Text: $4 input, $0.40 cached input, and $24 output per 1M tokens."
        ),
        supports_tool_calling=True,
    ),
)

GOOGLE_GEMINI_LIVE_MODEL_OPTIONS = (
    ProviderModelOption(
        model="gemini-3.1-flash-live-preview",
        label="Gemini 3.1 Flash Live preview",
        description=(
            "Google's current low-latency Live API model with native audio and improved reliability for voice agents."
        ),
        pricing_summary="See Google Gemini API pricing; preview pricing may change.",
        recommended=True,
    ),
    ProviderModelOption(
        model="gemini-live-2.5-flash-native-audio",
        label="Gemini Live 2.5 Flash native audio",
        description="Stable Gemini 2.5 native-audio Live API model.",
        pricing_summary="See Google Gemini API pricing.",
    ),
)

GROK_VOICE_MODEL_OPTIONS = (
    ProviderModelOption(
        model="grok-voice-think-fast-1.0",
        label="Grok Voice Think Fast 1.0",
        description="Version-pinned xAI Voice Agent model for production use.",
        pricing_summary="$0.05 per voice minute; text inputs are $0.004 per message.",
        recommended=True,
    ),
    ProviderModelOption(
        model="grok-voice-latest",
        label="Grok Voice latest",
        description=(
            "Moving alias for the latest xAI Voice Agent model; convenient for "
            "experimentation but less predictable than a pinned version."
        ),
        pricing_summary="$0.05 per voice minute; text inputs are $0.004 per message.",
    ),
)

OPENAI_TEXT_MODEL_OPTIONS = (
    ProviderModelOption(
        model="gpt-5.6-sol",
        label="GPT-5.6 Sol",
        description=(
            "OpenAI's flagship GPT-5.6 model for the hardest agentic, coding, reasoning, and tool-calling work."
        ),
        pricing_summary=("$5 input, $0.50 cached input, $6.25 cache write, and $30 output per 1M tokens."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="gpt-5.6-terra",
        label="GPT-5.6 Terra",
        description=(
            "Balanced GPT-5.6 model for delegated work where strong agentic quality and lower cost both matter."
        ),
        pricing_summary=("$2.50 input, $0.25 cached input, $3.125 cache write, and $15 output per 1M tokens."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="gpt-5.6-luna",
        label="GPT-5.6 Luna",
        description=("Fast, economical GPT-5.6 model for well-scoped or high-volume delegated tasks."),
        pricing_summary=("$1 input, $0.10 cached input, $1.25 cache write, and $6 output per 1M tokens."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="gpt-5.5",
        label="GPT-5.5",
        description=(
            "Previous-generation frontier model with strong reasoning, function calling, and a 1M-token context window."
        ),
        pricing_summary=(
            "$5 input, $0.50 cached input, and $30 output per 1M tokens "
            "below 272K input tokens; long-context rates are higher."
        ),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="gpt-5.4-mini",
        label="GPT-5.4 mini",
        description=("Fast, capable mini model for coding, tool use, and high-volume subagent work."),
        pricing_summary=("$0.75 input, $0.075 cached input, and $4.50 output per 1M tokens."),
        recommended=True,
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="gpt-5.4-nano",
        label="GPT-5.4 nano",
        description=(
            "Lowest-cost current model for tightly scoped classification, "
            "extraction, ranking, and simple tool-using subagent tasks."
        ),
        pricing_summary=("$0.20 input, $0.02 cached input, and $1.25 output per 1M tokens."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
)

XAI_TEXT_MODEL_OPTIONS = (
    ProviderModelOption(
        model="grok-4.5",
        label="Grok 4.5",
        description="xAI's flagship model for coding, agentic work, knowledge, and tool use.",
        pricing_summary=(
            "$2 input, $0.50 cached input, and $6 output per 1M tokens below "
            "200K context; long-context rates are higher."
        ),
        recommended=True,
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="grok-4.3",
        label="Grok 4.3",
        description="Cost-efficient xAI model optimized for agentic tool calling.",
        pricing_summary="$1.25 input, $0.20 cached input, and $2.50 output per 1M tokens.",
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="grok-build-0.1",
        label="Grok Build 0.1",
        description="Fast xAI coding and general agentic model.",
        pricing_summary="$1 input, $0.20 cached input, and $2 output per 1M tokens.",
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
)


NVIDIA_TEXT_MODEL_OPTIONS = (
    ProviderModelOption(
        model="stepfun-ai/step-3.7-flash",
        label="Step 3.7 Flash",
        description=(
            "Multimodal sparse MoE model with approximately 11B active "
            "parameters for chat, image understanding, and tool-using agent workflows."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        label="Nemotron 3 Nano Omni 30B A3B",
        description=(
            "Efficient NVIDIA multimodal reasoning model with approximately 3B active "
            "parameters for image-aware chat, document understanding, and tool-calling workflows."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
    ProviderModelOption(
        model="nvidia/nemotron-3-nano-30b-a3b",
        label="Nemotron 3 Nano 30B A3B",
        description=(
            "Fast 30B MoE model with a 1M-token context window for coding, reasoning, "
            "tool use, and everyday agent tasks."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="nvidia/nemotron-3-super-120b-a12b",
        label="Nemotron 3 Super 120B A12B",
        description=(
            "Balanced 124B NVIDIA MoE model with a 1M-token context window for "
            "long-context reasoning, coding, planning, and instruction following."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="nvidia/nemotron-3-ultra-550b-a55b",
        label="Nemotron 3 Ultra 550B A55B",
        description=(
            "NVIDIA's highest-capability 1M-context model for complex agentic reasoning, "
            "coding, planning, and function-calling workflows."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="openai/gpt-oss-120b",
        label="GPT-OSS 120B",
        description=(
            "General-purpose 117B MoE reasoning model with a 131K-token context "
            "window for agentic tool use, coding, and complex tasks."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="openai/gpt-oss-20b",
        label="GPT-OSS 20B",
        description=(
            "Lower-latency 21B MoE reasoning model with a 131K-token context window "
            "for tool use, math, and specialized tasks."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        recommended=True,
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="z-ai/glm-5.2",
        label="GLM-5.2",
        description=(
            "Z.ai's 1M-context flagship model for long-horizon reasoning, coding, "
            "tool use, and agentic workflows, served through NVIDIA API Catalog."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="moonshotai/kimi-k2.6",
        label="Kimi K2.6",
        description=(
            "Large MoE model for long-horizon coding, agentic tool use, and complex "
            "reasoning, served through NVIDIA API Catalog."
        ),
        pricing_summary=("Free endpoint available in NVIDIA API Catalog; availability and limits may change."),
        supports_tool_calling=True,
    ),
    ProviderModelOption(
        model="minimaxai/minimax-m3",
        label="MiniMax M3",
        description=(
            "Multimodal MoE model with strong reasoning, coding, and tool-calling "
            "capabilities for image-aware chat and agent workflows."
        ),
        pricing_summary=(
            "Free endpoint available in NVIDIA API Catalog; non-commercial model terms, availability, and limits apply."
        ),
        supports_image_input=True,
        supports_tool_calling=True,
        supports_browser_use=True,
    ),
)


OPENAI_IMAGE_GENERATION_MODEL_OPTIONS = (
    ProviderImageGenerationModelOption(
        model="gpt-image-2",
        label="GPT Image 2",
        description=(
            "OpenAI's current image model for fast, high-quality generation, "
            "precise instruction following, text rendering, and image editing."
        ),
        pricing_summary=(
            "Usage is token-based: $5 text input, $8 image input, and $30 image "
            "output per 1M tokens; output cost varies with size and quality."
        ),
        docs_url="https://developers.openai.com/api/docs/models/gpt-image-2",
        recommended=True,
        supports_image_editing=True,
        hosted_api_supports_image_editing=True,
    ),
)

XAI_IMAGE_GENERATION_MODEL_OPTIONS = (
    ProviderImageGenerationModelOption(
        model="grok-imagine-image-quality",
        label="Grok Imagine Image Quality",
        description=(
            "xAI's highest-quality image generation and editing model with 1K and "
            "2K output plus multi-image reference support."
        ),
        pricing_summary=("$0.05 per 1K output or $0.07 per 2K output; image inputs are $0.01 each."),
        docs_url="https://docs.x.ai/developers/models/grok-imagine-image-quality",
        recommended=True,
        supports_image_editing=True,
        hosted_api_supports_image_editing=True,
    ),
    ProviderImageGenerationModelOption(
        model="grok-imagine-image",
        label="Grok Imagine Image",
        description=("xAI's economical image generation and editing model for rapid iteration at 1K or 2K resolution."),
        pricing_summary=("$0.02 per 1K or 2K output; image inputs are $0.002 each."),
        docs_url="https://docs.x.ai/developers/models/grok-imagine-image",
        supports_image_editing=True,
        hosted_api_supports_image_editing=True,
    ),
)

NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS = (
    ProviderImageGenerationModelOption(
        model="black-forest-labs/flux.2-klein-4b",
        label="FLUX.2 Klein 4B",
        description=(
            "Fast Black Forest Labs model for NVIDIA-hosted text-to-image generation. "
            "Self-hosted NIM deployments also support multi-reference editing, "
            "inpainting, and style transfer."
        ),
        pricing_summary=("NVIDIA API Catalog trial access or self-hosted NIM deployment terms and costs apply."),
        docs_url=("https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_2-klein-4b"),
        recommended=True,
        supports_image_editing=True,
    ),
    ProviderImageGenerationModelOption(
        model="black-forest-labs/flux.1-schnell",
        label="FLUX.1 Schnell",
        description=(
            "Low-latency, commercially usable FLUX model in NVIDIA NIM that generates "
            "images from text in one to four diffusion steps."
        ),
        pricing_summary=("NVIDIA API Catalog trial access or self-hosted NIM deployment terms and costs apply."),
        docs_url=("https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_1-schnell"),
    ),
    ProviderImageGenerationModelOption(
        model="black-forest-labs/flux.1-dev",
        label="FLUX.1 Dev",
        description=(
            "High-quality FLUX text-to-image model in NVIDIA NIM for development and "
            "creative experimentation; review its model-specific terms before production use."
        ),
        pricing_summary=("NVIDIA API Catalog trial access or self-hosted NIM deployment terms and costs apply."),
        docs_url="https://docs.api.nvidia.com/nim/reference/black-forest-labs-flux_1-dev",
    ),
)


def model_ids(options: tuple[ProviderModelOption, ...]) -> tuple[str, ...]:
    return tuple(option.model for option in options)


def image_generation_model_ids(
    options: tuple[ProviderImageGenerationModelOption, ...],
) -> tuple[str, ...]:
    return tuple(option.model for option in options)

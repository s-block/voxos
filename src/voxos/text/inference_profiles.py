from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from voxos.text.models import TextProvider, TextReasoningMode


@dataclass(frozen=True, slots=True)
class TextModelSamplingProfile:
    """Provider sampling defaults for one model and execution mode."""

    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None


@dataclass(frozen=True, slots=True)
class TextModelInferenceProfile:
    """Exact-model request behavior that must not leak across model families."""

    supports_thinking_mode: bool = False
    fast_sampling: TextModelSamplingProfile | None = None
    agentic_sampling: TextModelSamplingProfile | None = None
    fast_tool_sampling: TextModelSamplingProfile | None = None
    agentic_tool_sampling: TextModelSamplingProfile | None = None

    def sampling_for(
        self,
        *,
        reasoning_mode: TextReasoningMode,
        has_tools: bool,
    ) -> TextModelSamplingProfile | None:
        if reasoning_mode == TextReasoningMode.AGENTIC:
            if has_tools and self.agentic_tool_sampling is not None:
                return self.agentic_tool_sampling
            return self.agentic_sampling
        if has_tools and self.fast_tool_sampling is not None:
            return self.fast_tool_sampling
        return self.fast_sampling


NEMOTRON_NANO_TOOL_SAMPLING = TextModelSamplingProfile(
    temperature=0.6,
    top_p=0.95,
)
NEMOTRON_OMNI_INSTRUCT_SAMPLING = TextModelSamplingProfile(
    temperature=0.2,
    top_k=1,
)
NEMOTRON_OMNI_THINKING_SAMPLING = TextModelSamplingProfile(
    temperature=0.6,
    top_p=0.95,
)


TEXT_MODEL_INFERENCE_PROFILES: Mapping[
    tuple[TextProvider, str],
    TextModelInferenceProfile,
] = MappingProxyType(
    {
        (
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-nano-30b-a3b",
        ): TextModelInferenceProfile(
            supports_thinking_mode=True,
            fast_tool_sampling=NEMOTRON_NANO_TOOL_SAMPLING,
            agentic_tool_sampling=NEMOTRON_NANO_TOOL_SAMPLING,
        ),
        (
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        ): TextModelInferenceProfile(
            supports_thinking_mode=True,
            fast_sampling=NEMOTRON_OMNI_INSTRUCT_SAMPLING,
            agentic_sampling=NEMOTRON_OMNI_THINKING_SAMPLING,
        ),
        (
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-super-120b-a12b",
        ): TextModelInferenceProfile(
            supports_thinking_mode=True,
        ),
        (
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-ultra-550b-a55b",
        ): TextModelInferenceProfile(
            supports_thinking_mode=True,
        ),
    },
)


def text_model_inference_profile(
    provider: TextProvider,
    model: str,
) -> TextModelInferenceProfile | None:
    """Return an exact model profile, preserving provider defaults when absent."""

    return TEXT_MODEL_INFERENCE_PROFILES.get((provider, model.lower()))

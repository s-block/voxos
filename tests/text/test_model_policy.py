import pytest

from voxos.db.session import AsyncSessionFactory
from voxos.providers.model_catalog import (
    GOOGLE_GEMINI_LIVE_MODEL_OPTIONS,
    GROK_VOICE_MODEL_OPTIONS,
    NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS,
    NVIDIA_TEXT_MODEL_OPTIONS,
    OPENAI_IMAGE_GENERATION_MODEL_OPTIONS,
    OPENAI_REALTIME_MODEL_OPTIONS,
    OPENAI_TEXT_MODEL_OPTIONS,
    XAI_IMAGE_GENERATION_MODEL_OPTIONS,
    XAI_TEXT_MODEL_OPTIONS,
)
from voxos.text.model_policy import TextSubAgentModelPolicy
from voxos.text.models import (
    TextProvider,
    TextProviderConfig,
    UserTextPreferences,
    effective_allowed_subagent_models,
    effective_image_generation_model,
    effective_image_generation_provider,
    effective_text_model,
    text_model_for_browser_use,
    text_model_for_image_input,
    text_model_supports_browser_use,
    text_model_supports_image_input,
    text_model_supports_tool_calling,
    text_provider_definition,
)
from voxos.text.storage import UserTextPreferenceService


def test_nvidia_catalog_includes_researched_multimodal_tool_models() -> None:
    candidate_models = {
        "stepfun-ai/step-3.7-flash",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "minimaxai/minimax-m3",
        "openai/gpt-oss-20b",
    }

    assert candidate_models <= {option.model for option in NVIDIA_TEXT_MODEL_OPTIONS}


def test_image_generation_catalogs_are_separate_and_have_provider_defaults() -> None:
    assert {option.model for option in OPENAI_IMAGE_GENERATION_MODEL_OPTIONS} == {
        "gpt-image-2",
    }
    assert {option.model for option in XAI_IMAGE_GENERATION_MODEL_OPTIONS} == {
        "grok-imagine-image-quality",
        "grok-imagine-image",
    }
    assert {option.model for option in NVIDIA_IMAGE_GENERATION_MODEL_OPTIONS} == {
        "black-forest-labs/flux.2-klein-4b",
        "black-forest-labs/flux.1-schnell",
        "black-forest-labs/flux.1-dev",
    }
    for provider in TextProvider:
        definition = text_provider_definition(provider)
        if definition.default_image_generation_model is None:
            assert definition.image_generation_model_options == ()
        else:
            assert definition.default_image_generation_model in {
                option.model for option in definition.image_generation_model_options
            }


def test_image_generation_preferences_can_use_a_provider_independent_of_text() -> None:
    preferences = UserTextPreferences(
        user_id="user-123",
        provider=TextProvider.OPENAI,
        model="gpt-5.4-mini",
        provider_config=TextProviderConfig(
            image_generation_provider=TextProvider.NVIDIA,
            image_generation_model="black-forest-labs/flux.1-schnell",
        ),
    )

    assert effective_image_generation_provider(preferences) == TextProvider.NVIDIA
    assert effective_image_generation_model(preferences) == ("black-forest-labs/flux.1-schnell")


def test_nvidia_catalog_falls_back_from_deprecated_free_endpoint_models() -> None:
    deprecated_model = "deepseek-ai/deepseek-v4-flash"
    preferences = UserTextPreferences(
        user_id="user-123",
        provider=TextProvider.NVIDIA,
        model=deprecated_model,
        provider_config=TextProviderConfig(
            allowed_subagent_models=(
                deprecated_model,
                "nvidia/nemotron-3-super-120b-a12b",
            ),
            subagent_model=deprecated_model,
        ),
    )

    assert deprecated_model not in {option.model for option in NVIDIA_TEXT_MODEL_OPTIONS}
    assert effective_text_model(preferences) == "openai/gpt-oss-20b"
    assert effective_allowed_subagent_models(preferences) == ("nvidia/nemotron-3-super-120b-a12b",)


def test_image_input_capability_is_explicit_for_chat_models_only() -> None:
    expected = {
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "grok-4.5",
        "grok-4.3",
        "grok-build-0.1",
        "stepfun-ai/step-3.7-flash",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "minimaxai/minimax-m3",
    }
    text_options = (
        *OPENAI_TEXT_MODEL_OPTIONS,
        *XAI_TEXT_MODEL_OPTIONS,
        *NVIDIA_TEXT_MODEL_OPTIONS,
    )

    assert {option.model for option in text_options if option.supports_image_input} == expected
    assert all(
        not option.supports_image_input
        for option in (
            *OPENAI_REALTIME_MODEL_OPTIONS,
            *GOOGLE_GEMINI_LIVE_MODEL_OPTIONS,
            *GROK_VOICE_MODEL_OPTIONS,
        )
    )
    assert text_model_supports_image_input(TextProvider.OPENAI, "gpt-5.6-sol")
    assert text_model_supports_image_input(TextProvider.XAI, "grok-4.5")
    assert text_model_supports_image_input(
        TextProvider.NVIDIA,
        "stepfun-ai/step-3.7-flash",
    )
    assert text_model_supports_image_input(
        TextProvider.NVIDIA,
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    )
    assert text_model_supports_image_input(
        TextProvider.NVIDIA,
        "minimaxai/minimax-m3",
    )
    assert not text_model_supports_image_input(
        TextProvider.NVIDIA,
        "nvidia/nemotron-3-nano-30b-a3b",
    )
    assert not text_model_supports_image_input(TextProvider.OPENAI, "unknown")
    assert not text_model_supports_image_input(TextProvider.XAI, "gpt-5.6-sol")


def test_nvidia_capability_routes_use_fast_text_and_step_multimodal_defaults() -> None:
    definition = text_provider_definition(TextProvider.NVIDIA)

    assert definition.default_model == "openai/gpt-oss-20b"
    assert definition.default_subagent_model == "openai/gpt-oss-20b"
    assert definition.default_multimodal_model == "stepfun-ai/step-3.7-flash"
    assert definition.default_browser_model == "stepfun-ai/step-3.7-flash"
    assert text_model_supports_tool_calling(
        TextProvider.NVIDIA,
        "openai/gpt-oss-20b",
    )
    assert not text_model_supports_image_input(
        TextProvider.NVIDIA,
        "openai/gpt-oss-20b",
    )
    assert (
        text_model_for_image_input(
            TextProvider.NVIDIA,
            "openai/gpt-oss-20b",
        )
        == "stepfun-ai/step-3.7-flash"
    )
    assert (
        text_model_for_image_input(
            TextProvider.NVIDIA,
            "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        )
        == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    )
    assert text_model_for_image_input(TextProvider.NVIDIA, "unknown") is None
    assert (
        text_model_for_browser_use(
            TextProvider.NVIDIA,
            "openai/gpt-oss-20b",
        )
        == "stepfun-ai/step-3.7-flash"
    )
    assert text_model_supports_browser_use(
        TextProvider.NVIDIA,
        "stepfun-ai/step-3.7-flash",
    )


@pytest.mark.asyncio
async def test_subagent_model_policy_uses_configured_default_and_allowlist(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        await UserTextPreferenceService(session).set_preferences(
            UserTextPreferences(
                user_id="user-123",
                provider=TextProvider.OPENAI,
                model="gpt-5.6-sol",
                credential_ref=None,
                provider_config=TextProviderConfig(
                    allowed_subagent_models=("gpt-5.6-sol", "gpt-5.6-luna"),
                    subagent_model="gpt-5.6-luna",
                ),
            ),
        )
    policy = TextSubAgentModelPolicy(session_factory=sqlite_session_factory)

    default_selection = await policy.select_model(
        user_id="user-123",
        parent_provider="openai",
        parent_model="gpt-5.6-sol",
        requested_model=None,
    )
    explicit_selection = await policy.select_model(
        user_id="user-123",
        parent_provider="openai",
        parent_model="gpt-5.6-sol",
        requested_model="gpt-5.6-sol",
    )

    assert default_selection.model == "gpt-5.6-luna"
    assert explicit_selection.model == "gpt-5.6-sol"

    with pytest.raises(ValueError, match="not allowed"):
        await policy.select_model(
            user_id="user-123",
            parent_provider="openai",
            parent_model="gpt-5.6-sol",
            requested_model="gpt-5.6-terra",
        )


@pytest.mark.asyncio
async def test_subagent_model_policy_uses_current_provider_defaults_without_preferences(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    selection = await TextSubAgentModelPolicy(
        session_factory=sqlite_session_factory,
    ).select_model(
        user_id="user-without-preferences",
        parent_provider="xai",
        parent_model="grok-4.5",
        requested_model=None,
    )

    assert selection.provider == "xai"
    assert selection.model == "grok-4.3"

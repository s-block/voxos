from dataclasses import dataclass

from voxos.text.models import (
    TextProvider,
    UserTextPreferences,
    image_generation_model_option,
    text_provider_definition,
)


@dataclass(frozen=True, slots=True)
class ImageGenerationChoice:
    provider: TextProvider
    model: str


def choose_image_generation_model(
    *,
    preferences: UserTextPreferences,
    configured_providers: frozenset[TextProvider],
    require_editing: bool = False,
) -> ImageGenerationChoice | None:
    configured_provider = preferences.provider_config.image_generation_provider
    configured_model = preferences.provider_config.image_generation_model
    if configured_provider is not None and configured_model is not None:
        configured_option = image_generation_model_option(
            configured_provider,
            configured_model,
        )
        if (
            configured_provider in configured_providers
            and configured_option is not None
            and (not require_editing or configured_option.hosted_api_supports_image_editing)
        ):
            return ImageGenerationChoice(
                provider=configured_provider,
                model=configured_model,
            )

    for provider in _automatic_provider_order(
        preferences.provider,
        require_editing=require_editing,
    ):
        if provider not in configured_providers:
            continue
        definition = text_provider_definition(provider)
        if definition.default_image_generation_model is None:
            continue
        option = image_generation_model_option(
            provider,
            definition.default_image_generation_model,
        )
        if option is None:
            continue
        if require_editing and not option.hosted_api_supports_image_editing:
            continue
        return ImageGenerationChoice(
            provider=provider,
            model=option.model,
        )
    return None


def displayed_image_generation_model(
    *,
    preferences: UserTextPreferences,
    configured_providers: frozenset[TextProvider],
) -> ImageGenerationChoice:
    choice = choose_image_generation_model(
        preferences=preferences,
        configured_providers=configured_providers,
    )
    if choice is not None:
        return choice
    provider = preferences.provider_config.image_generation_provider or preferences.provider
    definition = text_provider_definition(provider)
    if definition.default_image_generation_model is None:
        provider = TextProvider.OPENAI
        definition = text_provider_definition(provider)
    configured_model = preferences.provider_config.image_generation_model
    model = (
        configured_model
        if configured_model in definition.available_image_generation_models
        else definition.default_image_generation_model
    )
    if model is None:
        message = "No image generation model is configured"
        raise ValueError(message)
    return ImageGenerationChoice(provider=provider, model=model)


def _automatic_provider_order(
    active_provider: TextProvider,
    *,
    require_editing: bool,
) -> tuple[TextProvider, ...]:
    preferred = (
        (TextProvider.OPENAI, TextProvider.XAI, TextProvider.NVIDIA)
        if require_editing
        else (
            TextProvider.NVIDIA,
            active_provider,
            TextProvider.OPENAI,
            TextProvider.XAI,
        )
    )
    return tuple(dict.fromkeys(preferred))

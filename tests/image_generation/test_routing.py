from voxos.image_generation.routing import choose_image_generation_model
from voxos.text.models import (
    TextProvider,
    TextProviderConfig,
    UserTextPreferences,
)


def test_nvidia_is_the_automatic_generation_default_when_enabled() -> None:
    preferences = UserTextPreferences(
        user_id="user-1",
        provider=TextProvider.OPENAI,
        provider_config=TextProviderConfig(),
    )

    choice = choose_image_generation_model(
        preferences=preferences,
        configured_providers=frozenset(
            {TextProvider.OPENAI, TextProvider.NVIDIA},
        ),
    )

    assert choice is not None
    assert choice.provider == TextProvider.NVIDIA
    assert choice.model == "black-forest-labs/flux.2-klein-4b"


def test_hosted_editing_falls_back_from_nvidia_to_openai() -> None:
    preferences = UserTextPreferences(
        user_id="user-1",
        provider=TextProvider.NVIDIA,
        model="openai/gpt-oss-20b",
        provider_config=TextProviderConfig(
            image_generation_provider=TextProvider.NVIDIA,
            image_generation_model="black-forest-labs/flux.2-klein-4b",
        ),
    )

    choice = choose_image_generation_model(
        preferences=preferences,
        configured_providers=frozenset(
            {TextProvider.NVIDIA, TextProvider.OPENAI},
        ),
        require_editing=True,
    )

    assert choice is not None
    assert choice.provider == TextProvider.OPENAI
    assert choice.model == "gpt-image-2"


def test_nvidia_hosted_key_alone_does_not_claim_private_image_editing() -> None:
    preferences = UserTextPreferences(
        user_id="user-1",
        provider=TextProvider.NVIDIA,
        model="openai/gpt-oss-20b",
        provider_config=TextProviderConfig(),
    )

    choice = choose_image_generation_model(
        preferences=preferences,
        configured_providers=frozenset({TextProvider.NVIDIA}),
        require_editing=True,
    )

    assert choice is None

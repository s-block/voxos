import pytest
from pydantic import ValidationError

from voxos.providers.models import OpenAIRealtimeConfig, RealtimeVoiceProvider
from voxos.voice.models import UserVoicePreferences


def test_user_preferences_put_provider_at_top_level() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(modalities=("text", "audio")),
    )

    payload = preferences.model_dump(mode="json")

    assert payload["provider"] == "openai_realtime"
    assert payload["provider_config"]["modalities"] == ["text", "audio"]
    assert payload["credential_ref"] == "openai-local"
    assert "token" not in payload
    assert "api_key" not in payload


def test_openai_provider_config_rejects_secret_fields() -> None:
    with pytest.raises(ValidationError):
        OpenAIRealtimeConfig.model_validate(
            {
                "modalities": ["audio"],
                "api_key": "sk-should-not-be-stored",
            },
        )


def test_preferences_require_user_and_credential_reference() -> None:
    with pytest.raises(ValidationError):
        UserVoicePreferences.model_validate(
            {
                "provider": "openai_realtime",
                "model": "gpt-realtime",
                "voice": "marin",
            },
        )

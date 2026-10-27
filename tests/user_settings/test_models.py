from voxos.user_settings.models import (
    ProviderCredentialStatus,
    UserProviderCredentialRecord,
    UserVoicePreferenceRecord,
)


def test_voice_preference_record_stores_provider_at_top_level() -> None:
    record = UserVoicePreferenceRecord(
        user_id="local-user-123",
        provider="openai_realtime",
        model="gpt-realtime",
        voice="marin",
        default_credential_id="credential-123",
        provider_config={"modalities": ["text", "audio"]},
    )

    assert record.user_id == "local-user-123"
    assert record.provider == "openai_realtime"
    assert record.default_credential_id == "credential-123"


def test_credential_record_does_not_repr_plaintext() -> None:
    record = UserProviderCredentialRecord(
        user_id="local-user-123",
        provider="openai_realtime",
        label="OpenAI",
        status=ProviderCredentialStatus.ACTIVE.value,
    )

    assert "ciphertext" not in repr(record)
    assert "sk-" not in repr(record)
    assert set(record.model_dump()) == {
        "id",
        "user_id",
        "provider",
        "label",
        "auth_type",
        "status",
        "last_four",
        "created_at",
        "updated_at",
    }

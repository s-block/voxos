import pytest
from pydantic import SecretStr
from sqlmodel import col, select

from voxos.credentials.models import SecretPurpose
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.providers.models import (
    OpenAIRealtimeConfig,
    RealtimeVoiceProvider,
)
from voxos.user_settings.models import (
    ProviderCredentialStatus,
    UserVoicePreferenceRecord,
)
from voxos.user_settings.services import (
    UserCredentialService,
    UserVoicePreferenceService,
)
from voxos.voice.errors import (
    InvalidVoiceConfigurationError,
    MissingVoiceCredentialError,
    VoicePreferencesNotFoundError,
)
from voxos.voice.models import UserVoicePreferences


@pytest.mark.asyncio
async def test_user_settings_services_round_trip_preferences_and_credentials(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        credential = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI",
            secret=SecretStr("sk-test-secret"),
        )
        preferences = UserVoicePreferences(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            model="gpt-realtime",
            voice="marin",
            credential_ref=credential.id,
            provider_config=OpenAIRealtimeConfig(agent_instructions="Be concise."),
        )
        await UserVoicePreferenceService(session).set_preferences(preferences)

        loaded_preferences = await UserVoicePreferenceService(session).get_preferences(
            "user-123",
        )
        credentials = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).resolve_credential(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref=credential.id,
        )

    assert loaded_preferences == preferences
    assert credentials.token.get_secret_value() == "sk-test-secret"
    assert credential.last_four == "cret"
    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserSecretRecord))
        secret_record = result.one()
    assert secret_record.ciphertext != b"sk-test-secret"
    assert secret_record.purpose == SecretPurpose.VOICE_PROVIDER_CREDENTIAL.value


@pytest.mark.asyncio
async def test_user_settings_services_reject_missing_preferences(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        with pytest.raises(VoicePreferencesNotFoundError):
            await UserVoicePreferenceService(session).get_preferences("missing-user")


@pytest.mark.asyncio
async def test_user_settings_services_reject_invalid_provider_config(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        credential = await UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI",
            secret=SecretStr("sk-test-secret"),
        )
        preferences = UserVoicePreferences(
            user_id="user-123",
            credential_ref=credential.id,
        )
        await UserVoicePreferenceService(session).set_preferences(preferences)
        statement = select(UserVoicePreferenceRecord).where(
            col(UserVoicePreferenceRecord.user_id) == "user-123",
        )
        result = await session.exec(statement)
        record = result.one_or_none()
        assert record is not None
        record.provider_config = {"api_key": "sk-should-not-be-here"}
        await session.commit()

        with pytest.raises(InvalidVoiceConfigurationError):
            await UserVoicePreferenceService(session).get_preferences("user-123")


@pytest.mark.asyncio
async def test_user_settings_services_enforce_credential_ownership_and_status(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        )
        credential = await service.create_credential(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI",
            secret=SecretStr("sk-test-secret"),
        )

        with pytest.raises(MissingVoiceCredentialError):
            await service.resolve_credential(
                user_id="other-user",
                provider=RealtimeVoiceProvider.OPENAI_REALTIME,
                credential_ref=credential.id,
            )

        credential.status = ProviderCredentialStatus.REVOKED.value
        await session.commit()

        with pytest.raises(MissingVoiceCredentialError):
            await service.resolve_credential(
                user_id="user-123",
                provider=RealtimeVoiceProvider.OPENAI_REALTIME,
                credential_ref=credential.id,
            )


@pytest.mark.asyncio
async def test_user_settings_revoke_credential_deletes_vault_secret(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        )
        credential = await service.create_credential(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            label="OpenAI",
            secret=SecretStr("sk-test-secret"),
        )

        await service.revoke_credential(
            user_id="user-123",
            credential_ref=credential.id,
        )

        result = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.credential_id) == credential.id,
            ),
        )
        secret_record = result.one_or_none()

    assert secret_record is None

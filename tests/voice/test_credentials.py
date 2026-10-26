import pytest
from pydantic import SecretStr

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.providers.models import RealtimeVoiceProvider
from voxos.user_settings.services import UserCredentialService
from voxos.voice.credentials import SqlModelVoiceCredentialResolver
from voxos.voice.errors import MissingVoiceCredentialError


@pytest.mark.asyncio
async def test_sqlmodel_resolver_resolves_owned_credential(
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
            secret=SecretStr("sk-local-secret"),
        )

    resolver = SqlModelVoiceCredentialResolver(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    )
    credentials = await resolver.resolve(
        user_id="user-123",
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        credential_ref=credential.id,
    )

    assert credentials.reference == credential.id
    assert credentials.provider == RealtimeVoiceProvider.OPENAI_REALTIME
    assert credentials.token.get_secret_value() == "sk-local-secret"
    assert "sk-local-secret" not in str(credentials)


@pytest.mark.asyncio
async def test_sqlmodel_resolver_rejects_cross_user_reference(
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
            secret=SecretStr("sk-local-secret"),
        )

    resolver = SqlModelVoiceCredentialResolver(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    )

    with pytest.raises(MissingVoiceCredentialError):
        await resolver.resolve(
            user_id="other-user",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref=credential.id,
        )


@pytest.mark.asyncio
async def test_sqlmodel_resolver_rejects_missing_reference(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    resolver = SqlModelVoiceCredentialResolver(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    )

    with pytest.raises(MissingVoiceCredentialError):
        await resolver.resolve(
            user_id="user-123",
            provider=RealtimeVoiceProvider.OPENAI_REALTIME,
            credential_ref="missing-credential",
        )

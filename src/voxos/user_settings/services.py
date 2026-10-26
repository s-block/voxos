from dataclasses import dataclass

from pydantic import SecretStr, ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.providers.models import (
    OpenAIRealtimeConfig,
    RealtimeVoiceConfig,
    RealtimeVoiceProvider,
)
from voxos.user_settings.models import (
    ProviderCredentialAuthType,
    ProviderCredentialStatus,
    UserProviderCredentialRecord,
    UserVoicePreferenceRecord,
    new_record_id,
    utc_now,
)
from voxos.voice.errors import (
    InvalidVoiceConfigurationError,
    MissingVoiceCredentialError,
    VoicePreferencesNotFoundError,
)
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences

_VOICE_COMPATIBLE_TEXT_PROVIDER_IDS: dict[RealtimeVoiceProvider, tuple[str, ...]] = {
    RealtimeVoiceProvider.OPENAI_REALTIME: ("openai",),
    RealtimeVoiceProvider.GROK_VOICE_AGENT: ("xai",),
}


@dataclass(frozen=True, slots=True)
class UserCredentialSummary:
    provider: RealtimeVoiceProvider
    credential_ref: str
    label: str
    last_four: str | None


class UserVoicePreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        record = await self._get_record(user_id)
        if record is None:
            raise VoicePreferencesNotFoundError(user_id)

        try:
            return preferences_from_record(record)
        except (ValueError, ValidationError) as exc:
            raise InvalidVoiceConfigurationError.invalid_preferences(user_id) from exc

    async def set_preferences(
        self,
        preferences: UserVoicePreferences,
    ) -> UserVoicePreferenceRecord:
        provider_config = preferences.provider_config.model_dump(mode="json")
        record = await self._get_record(preferences.user_id)

        if record is None:
            record = UserVoicePreferenceRecord(
                user_id=preferences.user_id,
                provider=preferences.provider.value,
                model=preferences.model,
                voice=preferences.voice,
                default_credential_id=preferences.credential_ref,
                provider_config=provider_config,
            )
            self._session.add(record)
        else:
            record.provider = preferences.provider.value
            record.model = preferences.model
            record.voice = preferences.voice
            record.default_credential_id = preferences.credential_ref
            record.provider_config = provider_config
            record.updated_at = utc_now()

        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def _get_record(self, user_id: str) -> UserVoicePreferenceRecord | None:
        statement = select(UserVoicePreferenceRecord).where(
            col(UserVoicePreferenceRecord.user_id) == user_id,
        )
        result = await self._session.exec(statement)
        return result.one_or_none()


class UserCredentialService:
    def __init__(
        self,
        session: AsyncSession,
        secret_vault: UserSecretVaultService,
    ) -> None:
        self._session = session
        self._secret_vault = secret_vault

    async def create_credential(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        label: str,
        secret: SecretStr,
        auth_type: ProviderCredentialAuthType = ProviderCredentialAuthType.API_KEY,
    ) -> UserProviderCredentialRecord:
        credential_id = new_record_id()
        reference = voice_credential_secret_reference(
            user_id=user_id,
            provider=provider,
            credential_id=credential_id,
        )
        await self._secret_vault.seal_secret(
            session=self._session,
            secret=secret,
            reference=reference,
        )
        secret_value = secret.get_secret_value()
        record = UserProviderCredentialRecord(
            id=credential_id,
            user_id=user_id,
            provider=provider.value,
            label=label,
            auth_type=auth_type.value,
            last_four=secret_value[-4:] if len(secret_value) >= 4 else None,
        )
        self._session.add(record)
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def resolve_credential(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        credential_ref: str,
    ) -> ResolvedVoiceCredentials:
        record = await self.get_active_credential_record(
            user_id=user_id,
            provider=provider,
            credential_ref=credential_ref,
        )
        token = await self._secret_vault.unseal_secret(
            session=self._session,
            reference=credential_secret_reference_for_record(
                user_id=user_id,
                provider=provider,
                record=record,
            ),
        )
        if token is None:
            raise MissingVoiceCredentialError(credential_ref)
        return ResolvedVoiceCredentials(
            provider=provider,
            reference=record.id,
            token=token,
        )

    async def get_active_credential_record(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        credential_ref: str,
    ) -> UserProviderCredentialRecord:
        record = await self._get_record(credential_ref)
        if (
            record is None
            or record.user_id != user_id
            or record.provider not in compatible_credential_provider_ids(provider)
            or record.status != ProviderCredentialStatus.ACTIVE.value
        ):
            raise MissingVoiceCredentialError(credential_ref)
        return record

    async def get_latest_active_credential_record(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
    ) -> UserProviderCredentialRecord:
        statement = (
            select(UserProviderCredentialRecord)
            .where(
                col(UserProviderCredentialRecord.user_id) == user_id,
                col(UserProviderCredentialRecord.provider).in_(
                    compatible_credential_provider_ids(provider),
                ),
                col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
            )
            .order_by(col(UserProviderCredentialRecord.created_at).desc())
        )
        result = await self._session.exec(statement)
        record = result.first()
        if record is None:
            raise MissingVoiceCredentialError(provider.value)
        return record

    async def list_active_credential_summaries(
        self,
        user_id: str,
    ) -> tuple[UserCredentialSummary, ...]:
        statement = (
            select(UserProviderCredentialRecord)
            .where(
                col(UserProviderCredentialRecord.user_id) == user_id,
                col(UserProviderCredentialRecord.provider).in_(
                    all_voice_credential_provider_ids(),
                ),
                col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
            )
            .order_by(col(UserProviderCredentialRecord.created_at).desc())
        )
        result = await self._session.exec(statement)
        records = result.all()
        return tuple(
            UserCredentialSummary(
                provider=voice_provider_for_credential_record(record),
                credential_ref=record.id,
                label=record.label,
                last_four=record.last_four,
            )
            for record in records
        )

    async def revoke_active_credentials_for_provider(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        except_credential_ref: str | None = None,
    ) -> None:
        statement = select(UserProviderCredentialRecord).where(
            col(UserProviderCredentialRecord.user_id) == user_id,
            col(UserProviderCredentialRecord.provider).in_(
                compatible_credential_provider_ids(provider),
            ),
            col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
        )
        result = await self._session.exec(statement)
        records = result.all()
        for record in records:
            if record.id == except_credential_ref:
                continue
            await self._secret_vault.delete_secret(
                session=self._session,
                reference=credential_secret_reference_for_record(
                    user_id=user_id,
                    provider=provider,
                    record=record,
                ),
            )
            record.status = ProviderCredentialStatus.REVOKED.value
            record.updated_at = utc_now()
        await self._session.commit()

    async def revoke_credential(self, user_id: str, credential_ref: str) -> None:
        record = await self._get_record(credential_ref)
        if record is None or record.user_id != user_id:
            raise MissingVoiceCredentialError(credential_ref)

        record.status = ProviderCredentialStatus.REVOKED.value
        record.updated_at = utc_now()
        await self._secret_vault.delete_secret(
            session=self._session,
            reference=credential_secret_reference_for_record(
                user_id=user_id,
                provider=voice_provider_for_credential_record(record),
                record=record,
            ),
        )
        await self._session.commit()

    async def _get_record(
        self,
        credential_ref: str,
    ) -> UserProviderCredentialRecord | None:
        statement = select(UserProviderCredentialRecord).where(
            col(UserProviderCredentialRecord.id) == credential_ref,
        )
        result = await self._session.exec(statement)
        return result.one_or_none()


def voice_credential_secret_reference(
    user_id: str,
    provider: RealtimeVoiceProvider,
    credential_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=provider.value,
        credential_id=credential_id,
        purpose=SecretPurpose.VOICE_PROVIDER_CREDENTIAL,
    )


def compatible_credential_provider_ids(
    provider: RealtimeVoiceProvider,
) -> tuple[str, ...]:
    return (
        provider.value,
        *_VOICE_COMPATIBLE_TEXT_PROVIDER_IDS.get(provider, ()),
    )


def all_voice_credential_provider_ids() -> tuple[str, ...]:
    return tuple(
        provider_id
        for provider in RealtimeVoiceProvider
        for provider_id in compatible_credential_provider_ids(provider)
    )


def voice_provider_for_credential_record(
    record: UserProviderCredentialRecord,
) -> RealtimeVoiceProvider:
    for provider in RealtimeVoiceProvider:
        if record.provider in compatible_credential_provider_ids(provider):
            return provider
    message = f"Unsupported voice credential provider: {record.provider}"
    raise ValueError(message)


def credential_secret_reference_for_record(
    *,
    user_id: str,
    provider: RealtimeVoiceProvider,
    record: UserProviderCredentialRecord,
) -> SecretReference:
    if record.provider == provider.value:
        return voice_credential_secret_reference(
            user_id=user_id,
            provider=provider,
            credential_id=record.id,
        )
    if record.provider in _VOICE_COMPATIBLE_TEXT_PROVIDER_IDS.get(provider, ()):
        return SecretReference(
            owner=SecretOwner(user_id=user_id),
            provider=record.provider,
            credential_id=record.id,
            purpose=SecretPurpose.TEXT_PROVIDER_CREDENTIAL,
        )
    message = f"Credential is not compatible with voice provider: {provider.value}"
    raise MissingVoiceCredentialError(message)


def preferences_from_record(
    record: UserVoicePreferenceRecord,
) -> UserVoicePreferences:
    provider = RealtimeVoiceProvider(record.provider)
    return UserVoicePreferences.model_validate(
        {
            "user_id": record.user_id,
            "provider": provider,
            "model": record.model,
            "voice": record.voice,
            "credential_ref": record.default_credential_id,
            "provider_config": provider_config_from_record(
                provider=provider,
                provider_config=record.provider_config,
            ),
        },
    )


def provider_config_from_record(
    provider: RealtimeVoiceProvider,
    provider_config: dict[str, object],
) -> RealtimeVoiceConfig:
    if provider == RealtimeVoiceProvider.OPENAI_REALTIME:
        return OpenAIRealtimeConfig.model_validate(provider_config)
    return RealtimeVoiceConfig.model_validate(provider_config)

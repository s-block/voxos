from dataclasses import dataclass

from pydantic import SecretStr, ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.text.errors import (
    InvalidTextConfigurationError,
    MissingTextCredentialError,
    TextPreferencesNotFoundError,
)
from voxos.text.models import (
    ResolvedTextCredentials,
    TextProvider,
    TextProviderConfig,
    UserTextPreferences,
    text_provider_definition,
)
from voxos.text.records import UserTextPreferenceRecord
from voxos.user_settings.models import (
    ProviderCredentialAuthType,
    ProviderCredentialStatus,
    UserProviderCredentialRecord,
)


@dataclass(frozen=True, slots=True)
class TextCredentialSummary:
    provider: TextProvider
    credential_ref: str
    label: str
    last_four: str | None


class UserTextPreferenceService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_preferences(self, user_id: str) -> UserTextPreferences:
        record = await self._get_record(user_id)
        if record is None:
            raise TextPreferencesNotFoundError(user_id)
        try:
            return preferences_from_record(record)
        except (ValueError, ValidationError) as exc:
            raise InvalidTextConfigurationError.invalid_preferences(user_id) from exc

    async def set_preferences(
        self,
        preferences: UserTextPreferences,
    ) -> UserTextPreferenceRecord:
        provider_config = preferences.provider_config.model_dump(mode="json")
        record = await self._get_record(preferences.user_id)
        if record is None:
            record = UserTextPreferenceRecord(
                user_id=preferences.user_id,
                provider=preferences.provider.value,
                model=preferences.model,
                default_credential_id=preferences.credential_ref,
                provider_config=provider_config,
            )
            self._session.add(record)
        else:
            record.provider = preferences.provider.value
            record.model = preferences.model
            record.default_credential_id = preferences.credential_ref
            record.provider_config = provider_config
            record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return record

    async def _get_record(self, user_id: str) -> UserTextPreferenceRecord | None:
        statement = select(UserTextPreferenceRecord).where(
            col(UserTextPreferenceRecord.user_id) == user_id,
        )
        result = await self._session.exec(statement)
        return result.one_or_none()


@dataclass(frozen=True, slots=True)
class UserTextCredentialService:
    session: AsyncSession
    secret_vault: UserSecretVaultService

    async def create_credential(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        label: str,
        secret: SecretStr,
        auth_type: ProviderCredentialAuthType = ProviderCredentialAuthType.API_KEY,
    ) -> UserProviderCredentialRecord:
        credential = UserProviderCredentialRecord(
            user_id=user_id,
            provider=provider.value,
            label=label,
            auth_type=auth_type.value,
            last_four=last_four(secret),
        )
        self.session.add(credential)
        await self.session.flush()
        await self.secret_vault.seal_secret(
            session=self.session,
            secret=secret,
            reference=text_credential_secret_reference(
                user_id=user_id,
                provider=provider.value,
                credential_id=credential.id,
            ),
        )
        await self.session.commit()
        await self.session.refresh(credential)
        return credential

    async def resolve_credential(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        credential_ref: str,
    ) -> ResolvedTextCredentials:
        record = await self.get_active_credential_record(
            user_id=user_id,
            provider=provider,
            credential_ref=credential_ref,
        )
        token = await self.secret_vault.unseal_secret(
            session=self.session,
            reference=credential_secret_reference_for_record(
                user_id=user_id,
                provider=provider,
                record=record,
            ),
        )
        if token is None:
            raise MissingTextCredentialError(credential_ref)
        return ResolvedTextCredentials(
            provider=provider,
            reference=record.id,
            token=token,
        )

    async def get_active_credential_record(
        self,
        *,
        user_id: str,
        provider: TextProvider,
        credential_ref: str,
    ) -> UserProviderCredentialRecord:
        compatible_providers = compatible_provider_ids(provider)
        statement = select(UserProviderCredentialRecord).where(
            col(UserProviderCredentialRecord.id) == credential_ref,
            col(UserProviderCredentialRecord.user_id) == user_id,
            col(UserProviderCredentialRecord.provider).in_(compatible_providers),
            col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise MissingTextCredentialError(credential_ref)
        return record

    async def get_latest_active_credential_record(
        self,
        *,
        user_id: str,
        provider: TextProvider,
    ) -> UserProviderCredentialRecord:
        statement = (
            select(UserProviderCredentialRecord)
            .where(
                col(UserProviderCredentialRecord.user_id) == user_id,
                col(UserProviderCredentialRecord.provider).in_(compatible_provider_ids(provider)),
                col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
            )
            .order_by(col(UserProviderCredentialRecord.created_at).desc())
        )
        result = await self.session.exec(statement)
        record = result.first()
        if record is None:
            raise MissingTextCredentialError(provider.value)
        return record

    async def list_active_credential_summaries(
        self,
        user_id: str,
    ) -> tuple[TextCredentialSummary, ...]:
        supported_providers = tuple(
            provider_id for provider in TextProvider for provider_id in compatible_provider_ids(provider)
        )
        statement = (
            select(UserProviderCredentialRecord)
            .where(
                col(UserProviderCredentialRecord.user_id) == user_id,
                col(UserProviderCredentialRecord.provider).in_(supported_providers),
                col(UserProviderCredentialRecord.status) == ProviderCredentialStatus.ACTIVE.value,
            )
            .order_by(col(UserProviderCredentialRecord.created_at).desc())
        )
        result = await self.session.exec(statement)
        return tuple(
            TextCredentialSummary(
                provider=provider_for_credential_record(record),
                credential_ref=record.id,
                label=record.label,
                last_four=record.last_four,
            )
            for record in result.all()
        )


def preferences_from_record(record: UserTextPreferenceRecord) -> UserTextPreferences:
    provider = TextProvider(record.provider)
    return UserTextPreferences(
        user_id=record.user_id,
        provider=provider,
        model=record.model,
        credential_ref=record.default_credential_id,
        provider_config=TextProviderConfig.model_validate(record.provider_config),
    )


def compatible_provider_ids(provider: TextProvider) -> tuple[str, ...]:
    definition = text_provider_definition(provider)
    return (provider.value, *definition.compatible_credential_providers)


def provider_for_credential_record(record: UserProviderCredentialRecord) -> TextProvider:
    for provider in TextProvider:
        if record.provider in compatible_provider_ids(provider):
            return provider
    message = f"Unsupported text credential provider: {record.provider}"
    raise ValueError(message)


def credential_secret_reference_for_record(
    *,
    user_id: str,
    provider: TextProvider,
    record: UserProviderCredentialRecord,
) -> SecretReference:
    if record.provider == provider.value:
        return text_credential_secret_reference(
            user_id=user_id,
            provider=provider.value,
            credential_id=record.id,
        )
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=record.provider,
        credential_id=record.id,
        purpose=SecretPurpose.VOICE_PROVIDER_CREDENTIAL,
    )


def text_credential_secret_reference(
    *,
    user_id: str,
    provider: str,
    credential_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=provider,
        credential_id=credential_id,
        purpose=SecretPurpose.TEXT_PROVIDER_CREDENTIAL,
    )


def last_four(secret: SecretStr) -> str | None:
    value = secret.get_secret_value()
    return value[-4:] if len(value) >= 4 else None

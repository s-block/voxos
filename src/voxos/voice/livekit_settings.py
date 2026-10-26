from dataclasses import dataclass, field

import orjson
from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.security.encryption.errors import SecretEncryptionError
from voxos.user_settings.models import UserLiveKitSettingsRecord, utc_now
from voxos.voice.endpoint_policy import normalize_livekit_url
from voxos.voice.errors import InvalidLiveKitCredentialsError, MissingLiveKitCredentialsError
from voxos.voice.livekit_tokens import ResolvedLiveKitCredentials

ENCRYPTION_PROVIDER = "livekit"
API_KEY_SECRET_FIELD = "api_key"
API_SECRET_SECRET_FIELD = "api_secret"


@dataclass(frozen=True, slots=True)
class UserLiveKitSettingsSummary:
    configured: bool
    livekit_url: str | None = field(repr=False)


class UserLiveKitSettingsService:
    def __init__(
        self,
        session: AsyncSession,
        secret_vault: UserSecretVaultService,
    ) -> None:
        self._session = session
        self._secret_vault = secret_vault

    async def get_summary(self, user_id: str) -> UserLiveKitSettingsSummary:
        record = await self._get_record(user_id)
        if record is None:
            return UserLiveKitSettingsSummary(configured=False, livekit_url=None)
        return UserLiveKitSettingsSummary(
            configured=True,
            livekit_url=record.livekit_url,
        )

    async def set_settings(
        self,
        user_id: str,
        livekit_url: str,
        api_key: SecretStr,
        api_secret: SecretStr,
    ) -> UserLiveKitSettingsSummary:
        normalized_livekit_url = normalize_livekit_url(livekit_url)
        record = await self._get_record(user_id, for_update=True)
        if record is None:
            record = UserLiveKitSettingsRecord(
                user_id=user_id,
                livekit_url=normalized_livekit_url,
            )
            self._session.add(record)

        await self._secret_vault.seal_secret(
            session=self._session,
            secret=SecretStr(livekit_credentials_json(api_key, api_secret)),
            reference=livekit_secret_reference(user_id=user_id, record_id=record.id),
        )
        record.livekit_url = normalized_livekit_url
        record.updated_at = utc_now()
        await self._session.commit()
        await self._session.refresh(record)
        return UserLiveKitSettingsSummary(
            configured=True,
            livekit_url=record.livekit_url,
        )

    async def resolve_settings(self, user_id: str) -> ResolvedLiveKitCredentials:
        # Lock the URL row until its vault payload has been read. This prevents a
        # concurrent rotation from combining the old URL with the new key pair.
        record = await self._get_record(
            user_id,
            for_update=True,
            shared_lock=True,
        )
        if record is None:
            raise MissingLiveKitCredentialsError

        try:
            decrypted = await self._secret_vault.unseal_secret(
                session=self._session,
                reference=livekit_secret_reference(user_id=user_id, record_id=record.id),
            )
        except SecretEncryptionError as exc:
            raise InvalidLiveKitCredentialsError from exc
        if decrypted is None:
            raise MissingLiveKitCredentialsError
        try:
            return livekit_credentials_from_json(
                livekit_url=record.livekit_url,
                secret=decrypted,
            )
        except (MissingLiveKitCredentialsError, orjson.JSONDecodeError, ValueError) as exc:
            raise InvalidLiveKitCredentialsError from exc

    async def _get_record(
        self,
        user_id: str,
        *,
        for_update: bool = False,
        shared_lock: bool = False,
    ) -> UserLiveKitSettingsRecord | None:
        statement = select(UserLiveKitSettingsRecord).where(
            col(UserLiveKitSettingsRecord.user_id) == user_id,
        )
        if for_update:
            statement = statement.with_for_update(read=shared_lock)
        result = await self._session.exec(statement)
        return result.one_or_none()


def livekit_secret_reference(user_id: str, record_id: str) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=ENCRYPTION_PROVIDER,
        credential_id=record_id,
        purpose=SecretPurpose.LIVEKIT_CREDENTIALS,
    )


def livekit_credentials_json(api_key: SecretStr, api_secret: SecretStr) -> str:
    return orjson.dumps(
        {
            API_KEY_SECRET_FIELD: api_key.get_secret_value(),
            API_SECRET_SECRET_FIELD: api_secret.get_secret_value(),
        },
    ).decode()


def livekit_credentials_from_json(
    livekit_url: str,
    secret: SecretStr,
) -> ResolvedLiveKitCredentials:
    payload = orjson.loads(secret.get_secret_value())
    if not isinstance(payload, dict):
        raise MissingLiveKitCredentialsError

    api_key = payload.get(API_KEY_SECRET_FIELD)
    api_secret = payload.get(API_SECRET_SECRET_FIELD)
    if not isinstance(api_key, str) or not isinstance(api_secret, str):
        raise MissingLiveKitCredentialsError

    stripped_api_key = api_key.strip()
    stripped_api_secret = api_secret.strip()
    if stripped_api_key == "" or stripped_api_secret == "":
        raise MissingLiveKitCredentialsError

    return ResolvedLiveKitCredentials(
        livekit_url=normalize_livekit_url(livekit_url),
        api_key=stripped_api_key,
        api_secret=SecretStr(stripped_api_secret),
    )

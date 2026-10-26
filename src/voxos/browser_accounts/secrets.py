from dataclasses import dataclass
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService

BROWSER_PROFILE_ENCRYPTION_PROVIDER = "browser_use_cloud_profile"
BROWSER_SESSION_ENCRYPTION_PROVIDER = "browser_account_session"
BROWSER_ACCOUNT_CREDENTIAL_ENCRYPTION_PROVIDER = "browser_account_credential"


class BrowserProfileReferencePayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    version: Literal[1] = 1
    provider: BrowserAccountProvider
    provider_handle: str = Field(min_length=1, max_length=512, repr=False)


class BrowserSessionReferencePayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    version: Literal[1] = 1
    provider: BrowserAccountProvider
    provider_handle: str = Field(min_length=1, max_length=512, repr=False)


class BrowserAccountCredentialPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    version: Literal[1] = 1
    username: str = Field(min_length=1, max_length=512, repr=False)
    password: str = Field(min_length=1, max_length=4096, repr=False)


@dataclass(frozen=True, slots=True)
class OpaqueBrowserProfileHandle:
    value: SecretStr


@dataclass(frozen=True, slots=True)
class OpaqueBrowserSessionHandle:
    value: SecretStr


@dataclass(frozen=True, slots=True)
class BrowserWebsiteCredentials:
    username: SecretStr
    password: SecretStr


@dataclass(frozen=True, slots=True)
class BrowserAccountSecretStore:
    secret_vault: UserSecretVaultService

    async def seal_profile_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
        handle: OpaqueBrowserProfileHandle,
    ) -> None:
        payload = BrowserProfileReferencePayload(
            provider=BrowserAccountProvider.BROWSER_USE_CLOUD,
            provider_handle=handle.value.get_secret_value(),
        )
        await self.secret_vault.seal_secret(
            session=session,
            secret=SecretStr(orjson.dumps(payload.model_dump(mode="json")).decode()),
            reference=profile_secret_reference(user_id, browser_account_id),
        )

    async def load_profile_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
    ) -> OpaqueBrowserProfileHandle | None:
        secret = await self.secret_vault.unseal_secret(
            session=session,
            reference=profile_secret_reference(user_id, browser_account_id),
        )
        if secret is None:
            return None
        payload = BrowserProfileReferencePayload.model_validate_json(
            secret.get_secret_value(),
        )
        return OpaqueBrowserProfileHandle(SecretStr(payload.provider_handle))

    async def delete_profile_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
    ) -> None:
        await self.secret_vault.delete_secret(
            session=session,
            reference=profile_secret_reference(user_id, browser_account_id),
        )

    async def seal_session_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        credential_id: str,
        handle: OpaqueBrowserSessionHandle,
        provider: BrowserAccountProvider = BrowserAccountProvider.BROWSER_USE_CLOUD,
    ) -> None:
        payload = BrowserSessionReferencePayload(
            provider=provider,
            provider_handle=handle.value.get_secret_value(),
        )
        await self.secret_vault.seal_secret(
            session=session,
            secret=SecretStr(orjson.dumps(payload.model_dump(mode="json")).decode()),
            reference=session_secret_reference(user_id, credential_id),
        )

    async def load_session_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        credential_id: str,
    ) -> OpaqueBrowserSessionHandle | None:
        secret = await self.secret_vault.unseal_secret(
            session=session,
            reference=session_secret_reference(user_id, credential_id),
        )
        if secret is None:
            return None
        payload = BrowserSessionReferencePayload.model_validate_json(
            secret.get_secret_value(),
        )
        return OpaqueBrowserSessionHandle(SecretStr(payload.provider_handle))

    async def delete_session_handle(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        credential_id: str,
    ) -> None:
        await self.secret_vault.delete_secret(
            session=session,
            reference=session_secret_reference(user_id, credential_id),
        )

    async def seal_account_credentials(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
        credentials: BrowserWebsiteCredentials,
    ) -> None:
        payload = BrowserAccountCredentialPayload(
            username=credentials.username.get_secret_value(),
            password=credentials.password.get_secret_value(),
        )
        await self.secret_vault.seal_secret(
            session=session,
            secret=SecretStr(orjson.dumps(payload.model_dump()).decode()),
            reference=account_credential_secret_reference(
                user_id,
                browser_account_id,
            ),
        )

    async def load_account_credentials(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
    ) -> BrowserWebsiteCredentials | None:
        secret = await self.secret_vault.unseal_secret(
            session=session,
            reference=account_credential_secret_reference(
                user_id,
                browser_account_id,
            ),
        )
        if secret is None:
            return None
        payload = BrowserAccountCredentialPayload.model_validate_json(
            secret.get_secret_value(),
        )
        return BrowserWebsiteCredentials(
            username=SecretStr(payload.username),
            password=SecretStr(payload.password),
        )

    async def delete_account_credentials(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        browser_account_id: str,
    ) -> None:
        await self.secret_vault.delete_secret(
            session=session,
            reference=account_credential_secret_reference(
                user_id,
                browser_account_id,
            ),
        )


def profile_secret_reference(
    user_id: str,
    browser_account_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=BROWSER_PROFILE_ENCRYPTION_PROVIDER,
        credential_id=browser_account_id,
        purpose=SecretPurpose.BROWSER_PROFILE_REFERENCE,
    )


def session_secret_reference(user_id: str, credential_id: str) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=BROWSER_SESSION_ENCRYPTION_PROVIDER,
        credential_id=credential_id,
        purpose=SecretPurpose.BROWSER_SESSION_REFERENCE,
    )


def account_credential_secret_reference(
    user_id: str,
    browser_account_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=BROWSER_ACCOUNT_CREDENTIAL_ENCRYPTION_PROVIDER,
        credential_id=browser_account_id,
        purpose=SecretPurpose.BROWSER_ACCOUNT_CREDENTIAL,
    )

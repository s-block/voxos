from dataclasses import dataclass
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService

CODEX_AUTH_ENCRYPTION_PROVIDER = "openai_codex_chatgpt"
MAX_CODEX_AUTH_JSON_BYTES = 512 * 1024


class CodexAuthPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: Literal[1] = 1
    auth_json: str = Field(min_length=2, max_length=MAX_CODEX_AUTH_JSON_BYTES, repr=False)

    @field_validator("auth_json")
    @classmethod
    def validate_auth_json(cls, value: str) -> str:
        try:
            parsed = orjson.loads(value)
        except orjson.JSONDecodeError as exc:
            message = "Codex authentication data is not valid JSON"
            raise ValueError(message) from exc
        if not isinstance(parsed, dict):
            message = "Codex authentication data must be a JSON object"
            raise ValueError(message)
        auth_mode = parsed.get("auth_mode")
        if auth_mode != "chatgpt":
            message = "Only ChatGPT Codex authentication may be stored"
            raise ValueError(message)
        return value


@dataclass(frozen=True, slots=True)
class OpaqueCodexAuth:
    value: SecretStr


@dataclass(frozen=True, slots=True)
class CodexAccountSecretStore:
    secret_vault: UserSecretVaultService

    async def seal_auth(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_id: str,
        auth: OpaqueCodexAuth,
    ) -> None:
        payload = CodexAuthPayload(auth_json=auth.value.get_secret_value())
        await self.secret_vault.seal_secret(
            session=session,
            secret=SecretStr(orjson.dumps(payload.model_dump()).decode()),
            reference=codex_auth_secret_reference(user_id, account_id),
        )

    async def load_auth(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_id: str,
    ) -> OpaqueCodexAuth | None:
        secret = await self.secret_vault.unseal_secret(
            session=session,
            reference=codex_auth_secret_reference(user_id, account_id),
        )
        if secret is None:
            return None
        payload = CodexAuthPayload.model_validate_json(secret.get_secret_value())
        return OpaqueCodexAuth(SecretStr(payload.auth_json))

    async def delete_auth(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        account_id: str,
    ) -> None:
        await self.secret_vault.delete_secret(
            session=session,
            reference=codex_auth_secret_reference(user_id, account_id),
        )


def codex_auth_secret_reference(user_id: str, account_id: str) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=CODEX_AUTH_ENCRYPTION_PROVIDER,
        credential_id=account_id,
        purpose=SecretPurpose.CODEX_ACCOUNT_AUTH,
    )

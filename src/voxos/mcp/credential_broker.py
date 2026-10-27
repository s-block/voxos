import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretPurpose
from voxos.credentials.vault import UserSecretVaultService
from voxos.mcp.delegation import (
    DELEGATION_PROVIDER_MONZO,
    SignedMcpCredentialDelegation,
)
from voxos.mcp.errors import McpOAuthConfigurationError
from voxos.mcp.models import (
    McpOAuthClientInfo,
    McpOAuthToken,
    McpSecretKind,
)
from voxos.mcp.oauth import McpOAuthHttpClient
from voxos.mcp.records import (
    UserMcpSecretRecord,
    UserMcpServerRecord,
    utc_now,
)
from voxos.mcp.services import (
    is_brokered_monzo,
    mcp_secret_reference,
    model_secret_json,
)

BROKER_TOKEN_EXPIRY_SKEW = timedelta(seconds=60)


class McpCredentialBrokerError(Exception):
    """A safe credential-broker failure."""


class McpCredentialBrokerAuthorizationError(McpCredentialBrokerError):
    """The delegation does not authorize this provider or server."""


class McpCredentialBrokerReauthenticationRequiredError(McpCredentialBrokerError):
    """The user must reconnect the upstream provider."""


class McpCredentialBrokerUpstreamError(McpCredentialBrokerError):
    """The provider token endpoint could not issue a usable token."""


class McpCredentialBrokerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rejected_access_token_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class McpCredentialBrokerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1, repr=False)
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime | None = None


class McpCredentialBrokerService:
    """Resolve and atomically refresh one tenant-owned Monzo access token."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        secret_vault: UserSecretVaultService,
        oauth_client: McpOAuthHttpClient,
    ) -> None:
        self._session = session
        self._secret_vault = secret_vault
        self._oauth_client = oauth_client

    async def issue_access_token(
        self,
        *,
        delegation: SignedMcpCredentialDelegation,
        request: McpCredentialBrokerRequest,
    ) -> McpCredentialBrokerResponse:
        if delegation.provider != DELEGATION_PROVIDER_MONZO:
            raise McpCredentialBrokerAuthorizationError

        server = await self._server_for_delegation(delegation)
        if not is_brokered_monzo(server) or not server.enabled:
            raise McpCredentialBrokerAuthorizationError

        token_record = await self._locked_secret_record(
            delegation=delegation,
            secret_kind=McpSecretKind.OAUTH_TOKEN,
        )
        client_record = await self._secret_record(
            delegation=delegation,
            secret_kind=McpSecretKind.OAUTH_CLIENT_INFO,
        )
        if token_record is None or client_record is None:
            raise McpCredentialBrokerReauthenticationRequiredError

        try:
            token = McpOAuthToken.model_validate_json(
                (
                    await self._unseal_secret(
                        delegation=delegation,
                        record=token_record,
                    )
                ).get_secret_value()
            )
            client_info = McpOAuthClientInfo.model_validate_json(
                (
                    await self._unseal_secret(
                        delegation=delegation,
                        record=client_record,
                    )
                ).get_secret_value()
            )
        except ValidationError:
            raise McpCredentialBrokerReauthenticationRequiredError from None

        rejected_fingerprint = request.rejected_access_token_sha256
        current_fingerprint = hashlib.sha256(token.access_token.encode()).hexdigest()
        rejected_token_is_current = rejected_fingerprint is not None and hmac.compare_digest(
            rejected_fingerprint,
            current_fingerprint,
        )
        if not _token_expiring(token) and not rejected_token_is_current:
            return _response_from_token(token)

        if token.refresh_token is None:
            raise McpCredentialBrokerReauthenticationRequiredError
        try:
            refreshed = await self._oauth_client.refresh_token(
                client_info=client_info,
                token=token,
            )
        except (httpx.HTTPError, McpOAuthConfigurationError, ValueError) as exc:
            raise McpCredentialBrokerUpstreamError from exc
        if refreshed.token_type.lower() != "bearer" or refreshed.refresh_token is None:
            raise McpCredentialBrokerReauthenticationRequiredError

        await self._secret_vault.seal_secret(
            session=self._session,
            secret=SecretStr(model_secret_json(refreshed)),
            reference=mcp_secret_reference(
                user_id=delegation.user_id,
                record_id=token_record.id,
                purpose=SecretPurpose.MCP_SERVER_SECRET,
            ),
        )
        token_record.secret_metadata = {"scopes": list(refreshed.scopes)}
        token_record.expires_at = refreshed.expires_at
        token_record.updated_at = utc_now()
        await self._session.commit()
        return _response_from_token(refreshed)

    async def _server_for_delegation(
        self,
        delegation: SignedMcpCredentialDelegation,
    ) -> UserMcpServerRecord:
        result = await self._session.exec(
            select(UserMcpServerRecord).where(
                col(UserMcpServerRecord.id) == delegation.server_id,
                col(UserMcpServerRecord.user_id) == delegation.user_id,
            )
        )
        server = result.one_or_none()
        if server is None:
            raise McpCredentialBrokerAuthorizationError
        return server

    async def _locked_secret_record(
        self,
        *,
        delegation: SignedMcpCredentialDelegation,
        secret_kind: McpSecretKind,
    ) -> UserMcpSecretRecord | None:
        statement = (
            select(UserMcpSecretRecord)
            .where(
                col(UserMcpSecretRecord.user_id) == delegation.user_id,
                col(UserMcpSecretRecord.server_id) == delegation.server_id,
                col(UserMcpSecretRecord.secret_kind) == secret_kind.value,
            )
            .with_for_update()
        )
        result = await self._session.exec(statement)
        return result.one_or_none()

    async def _secret_record(
        self,
        *,
        delegation: SignedMcpCredentialDelegation,
        secret_kind: McpSecretKind,
    ) -> UserMcpSecretRecord | None:
        result = await self._session.exec(
            select(UserMcpSecretRecord).where(
                col(UserMcpSecretRecord.user_id) == delegation.user_id,
                col(UserMcpSecretRecord.server_id) == delegation.server_id,
                col(UserMcpSecretRecord.secret_kind) == secret_kind.value,
            )
        )
        return result.one_or_none()

    async def _unseal_secret(
        self,
        *,
        delegation: SignedMcpCredentialDelegation,
        record: UserMcpSecretRecord,
    ) -> SecretStr:
        secret = await self._secret_vault.unseal_secret(
            session=self._session,
            reference=mcp_secret_reference(
                user_id=delegation.user_id,
                record_id=record.id,
                purpose=SecretPurpose.MCP_SERVER_SECRET,
            ),
        )
        if secret is None:
            raise McpCredentialBrokerReauthenticationRequiredError
        return secret


def _token_expiring(token: McpOAuthToken) -> bool:
    if token.expires_at is None:
        return False
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC) + BROKER_TOKEN_EXPIRY_SKEW


def _response_from_token(
    token: McpOAuthToken,
) -> McpCredentialBrokerResponse:
    if token.token_type.lower() != "bearer":
        raise McpCredentialBrokerReauthenticationRequiredError
    return McpCredentialBrokerResponse(
        access_token=token.access_token,
        expires_at=token.expires_at,
    )

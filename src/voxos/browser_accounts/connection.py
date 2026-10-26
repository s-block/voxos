from dataclasses import dataclass
from typing import Protocol

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.browser_accounts.errors import (
    BrowserAccountProviderConnectionError,
    BrowserAccountProviderToolRequiredError,
)
from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.providers.base import BrowserProviderCredential
from voxos.credentials.vault import UserSecretVaultService
from voxos.mcp.models import (
    McpAuthType,
    McpSecretKind,
    McpServerStatus,
    McpTrustState,
)
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import (
    McpSecretStore,
    runtime_preset_id_for_record,
    safe_metadata_from_record,
)

BROWSER_USE_PRESET_ID = "browser-use-cloud"
BROWSERLESS_PRESET_ID = "browserless"
BROWSER_USE_EXECUTION_TOOL = "run_session"
BROWSERLESS_EXECUTION_TOOL = "browserless_agent"


class BrowserProviderCredentialResolver(Protocol):
    async def require_connection(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> UserMcpServerRecord:
        """Validate and return an exact supported user-owned browser connection."""

    async def resolve_credential(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> BrowserProviderCredential:
        """Resolve the exact connection's encrypted provider credential."""


@dataclass(frozen=True, slots=True)
class McpBrowserCredentialResolver:
    secret_vault: UserSecretVaultService

    async def require_connection(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> UserMcpServerRecord:
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.id) == connection_id,
            col(UserMcpServerRecord.user_id) == user_id,
        )
        result = await session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise BrowserAccountProviderConnectionError

        safe_metadata = safe_metadata_from_record(record.safe_metadata)
        preset_id = runtime_preset_id_for_record(record, safe_metadata)
        auth_type = McpAuthType(record.auth_type)
        if preset_id == BROWSER_USE_PRESET_ID:
            required_auth_type = McpAuthType.API_KEY_HEADER
            required_tool = BROWSER_USE_EXECUTION_TOOL
            provider_label = "Browser Use Cloud"
        elif preset_id == BROWSERLESS_PRESET_ID:
            required_auth_type = McpAuthType.STATIC_BEARER
            required_tool = BROWSERLESS_EXECUTION_TOOL
            provider_label = "Browserless"
        else:
            raise BrowserAccountProviderConnectionError
        if auth_type != required_auth_type:
            raise BrowserAccountProviderConnectionError
        if require_active:
            active = (
                record.enabled
                and McpTrustState(record.trust_state) == McpTrustState.TRUSTED
                and McpServerStatus(record.status) == McpServerStatus.CONNECTED
            )
            if not active:
                raise BrowserAccountProviderConnectionError
        if required_tool not in record.allowed_tools:
            raise BrowserAccountProviderToolRequiredError(
                provider_label=provider_label,
                required_tool=required_tool,
            )
        return record

    async def resolve_credential(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        connection_id: str,
        require_active: bool = True,
    ) -> BrowserProviderCredential:
        record = await self.require_connection(
            session=session,
            user_id=user_id,
            connection_id=connection_id,
            require_active=require_active,
        )
        safe_metadata = safe_metadata_from_record(record.safe_metadata)
        preset_id = runtime_preset_id_for_record(record, safe_metadata)
        provider = (
            BrowserAccountProvider.BROWSERLESS
            if preset_id == BROWSERLESS_PRESET_ID
            else BrowserAccountProvider.BROWSER_USE_CLOUD
        )
        secret_kind = (
            McpSecretKind.STATIC_BEARER if provider == BrowserAccountProvider.BROWSERLESS else McpSecretKind.API_KEY
        )
        secret = await McpSecretStore(
            session=session,
            secret_vault=self.secret_vault,
        ).load_secret_value(
            user_id=user_id,
            server_id=connection_id,
            secret_kind=secret_kind,
        )
        if secret is None or secret.get_secret_value().strip() == "":
            raise BrowserAccountProviderConnectionError
        return BrowserProviderCredential(api_key=secret, provider=provider)

import ipaddress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.auth_strategies import (
    McpAuthStrategyDependencies,
    OAuthClientCredentialsStrategy,
    StaticBearerStrategy,
)
from voxos.mcp.models import (
    McpAuthType,
    McpOAuthClientInfo,
    McpOAuthToken,
    McpSecretKind,
    McpServerCreateRequest,
    McpTokenEndpointAuthMethod,
)
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import McpSecretStore, UserMcpServerService
from voxos.mcp.url_policy import McpOutboundUrlPolicy
from voxos.security.outbound_endpoints import OutboundEndpointPolicy

FRESH_ACCESS_TOKEN = "fresh-token"
EXPIRED_ACCESS_TOKEN = "expired-token"
CLIENT_SECRET = "client-secret"
TOKEN_ENDPOINT = "https://auth.example.com/token"
STATIC_BEARER_TOKEN = "sentry-token"


async def public_addresses(
    host: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    del host
    return (ipaddress.ip_address("93.184.216.34"),)


@dataclass(slots=True)
class RefreshingOAuthClient:
    requested_client_id: str | None = None

    async def client_credentials_token_for_client(
        self,
        client_info: McpOAuthClientInfo,
    ) -> McpOAuthToken:
        self.requested_client_id = client_info.client_id
        return McpOAuthToken(
            access_token=FRESH_ACCESS_TOKEN,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


@dataclass(slots=True)
class EmptyMcpClient:
    pass


@pytest.mark.asyncio
async def test_static_bearer_strategy_builds_authorization_header(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        secret_store = McpSecretStore(session, user_secret_vault)
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=EmptyMcpClient(),
            oauth_client=RefreshingOAuthClient(),
        )
        response = await service.create_server(
            user_id="user-123",
            request=McpServerCreateRequest(
                label="Sentry",
                url="https://mcp.sentry.dev/mcp",
                auth_type=McpAuthType.STATIC_BEARER,
            ),
        )
        await secret_store.upsert_secret(
            user_id="user-123",
            server_id=response.id,
            secret_kind=McpSecretKind.STATIC_BEARER,
            secret=SecretStr(STATIC_BEARER_TOKEN),
        )
        record = await get_record(session)

        auth = await StaticBearerStrategy().auth_for_connection(
            server=record,
            dependencies=McpAuthStrategyDependencies(
                secret_store=secret_store,
                oauth_client=RefreshingOAuthClient(),
            ),
        )

    assert auth.headers == {"Authorization": f"Bearer {STATIC_BEARER_TOKEN}"}


@pytest.mark.asyncio
async def test_client_credentials_strategy_refreshes_expired_token(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    oauth_client = RefreshingOAuthClient()
    async with sqlite_session_factory() as session:
        secret_store = McpSecretStore(session, user_secret_vault)
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=EmptyMcpClient(),
            oauth_client=oauth_client,
            url_policy=McpOutboundUrlPolicy(
                OutboundEndpointPolicy(address_resolver=public_addresses),
            ),
        )
        response = await service.create_server(
            user_id="user-123",
            request=McpServerCreateRequest(
                label="Custom",
                url="https://mcp.example.com/mcp",
                auth_type=McpAuthType.OAUTH_CLIENT_CREDENTIALS,
            ),
        )
        await secret_store.store_oauth_client_info(
            user_id="user-123",
            server_id=response.id,
            client_info=McpOAuthClientInfo(
                client_id="client-123",
                client_secret=CLIENT_SECRET,
                token_endpoint=TOKEN_ENDPOINT,
                token_endpoint_auth_method=(McpTokenEndpointAuthMethod.CLIENT_SECRET_BASIC),
            ),
        )
        await secret_store.store_oauth_token(
            user_id="user-123",
            server_id=response.id,
            token=McpOAuthToken(
                access_token=EXPIRED_ACCESS_TOKEN,
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            ),
        )
        record = await get_record(session)

        auth = await OAuthClientCredentialsStrategy().auth_for_connection(
            server=record,
            dependencies=McpAuthStrategyDependencies(
                secret_store=secret_store,
                oauth_client=oauth_client,
            ),
        )
        stored_token = await secret_store.load_oauth_token("user-123", response.id)

    assert oauth_client.requested_client_id == "client-123"
    assert auth.headers == {"Authorization": f"Bearer {FRESH_ACCESS_TOKEN}"}
    assert stored_token is not None
    assert stored_token.access_token == FRESH_ACCESS_TOKEN


async def get_record(session: AsyncSession) -> UserMcpServerRecord:
    result = await session.exec(select(UserMcpServerRecord))
    return result.one()

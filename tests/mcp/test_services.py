import ipaddress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from pydantic import SecretStr
from sqlmodel import col, select

from voxos.credentials.models import SecretPurpose
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
)
from voxos.mcp.errors import McpPresetConfigurationError, McpServerNotFoundError
from voxos.mcp.models import (
    McpAuthType,
    McpOAuthClientInfo,
    McpOAuthStartRequest,
    McpOAuthToken,
    McpPresetServerCreateRequest,
    McpSecretKind,
    McpServerCreateRequest,
    McpServerStatus,
    McpServerUpdateRequest,
    McpStaticAuthRequest,
    McpTokenEndpointAuthMethod,
    McpTrustState,
)
from voxos.mcp.oauth import McpPreparedAuthorization, state_hash
from voxos.mcp.oauth_state import McpOAuthStateSigner
from voxos.mcp.records import (
    UserMcpOAuthStateRecord,
    UserMcpSecretRecord,
    UserMcpServerRecord,
)
from voxos.mcp.services import (
    MAX_DISCOVERED_TOOL_DEFINITIONS,
    McpSecretStore,
    UserMcpServerService,
    safe_metadata_with_discovered_tools,
)
from voxos.mcp.url_policy import McpOutboundUrlPolicy
from voxos.security.outbound_endpoints import OutboundEndpointPolicy

CLIENT_SECRET = "client-secret"
OAUTH_ACCESS_TOKEN = "oauth-access-token"
OAUTH_REFRESH_TOKEN = "oauth-refresh-token"
TOKEN_ENDPOINT = "https://auth.example.com/token"


async def public_addresses(
    host: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    del host
    return (ipaddress.ip_address("93.184.216.34"),)


@dataclass(slots=True)
class FakeMcpClient:
    capabilities: DiscoveredMcpCapabilities = field(
        default_factory=lambda: DiscoveredMcpCapabilities(
            tools=("issues.list", "merge_requests.list"),
        ),
    )
    seen_config: McpClientConnectionConfig | None = None

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        self.seen_config = config
        return self.capabilities


@dataclass(slots=True)
class FakeOAuthClient:
    exchanged_code: str | None = None
    exchanged_verifier: str | None = None
    prepared_request: McpOAuthStartRequest | None = None

    async def prepare_authorization(
        self,
        server_url: str,
        request: McpOAuthStartRequest,
        state: str | None = None,
        use_pkce: bool = True,
    ) -> McpPreparedAuthorization:
        del server_url, use_pkce
        self.prepared_request = request
        resolved_state = state or "provider-state"
        return McpPreparedAuthorization(
            authorization_url=(f"https://auth.example.com/authorize?state={resolved_state}"),
            client_info=McpOAuthClientInfo(
                client_id="client-123",
                client_secret=CLIENT_SECRET,
                token_endpoint=TOKEN_ENDPOINT,
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint_auth_method=(McpTokenEndpointAuthMethod.CLIENT_SECRET_BASIC),
                scopes=request.scopes,
            ),
            state=resolved_state,
            code_verifier="pkce-verifier",
        )

    async def exchange_authorization_code(
        self,
        client_info: McpOAuthClientInfo,
        code: str,
        redirect_uri: str,
        code_verifier: str | None,
    ) -> McpOAuthToken:
        self.exchanged_code = code
        self.exchanged_verifier = code_verifier
        return McpOAuthToken(
            access_token=OAUTH_ACCESS_TOKEN,
            refresh_token=OAUTH_REFRESH_TOKEN,
            scope="read write",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_mcp_service_encrypts_static_auth_and_discovers_tools(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_client = FakeMcpClient()
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=fake_client,
            oauth_client=FakeOAuthClient(),
            url_policy=McpOutboundUrlPolicy(
                OutboundEndpointPolicy(address_resolver=public_addresses),
            ),
        )
        created = await service.create_server(
            user_id="user-123",
            request=McpServerCreateRequest(
                label="GitHub",
                url="https://api.githubcopilot.com/mcp/",
                auth_type=McpAuthType.STATIC_BEARER,
                enabled=True,
                allowed_tools=("issues.list",),
            ),
        )
        assert created.status == McpServerStatus.NEEDS_AUTH
        assert created.trust_state == McpTrustState.UNTRUSTED
        assert created.non_admin_tools_enabled is False
        assert created.logo is not None
        assert created.logo.domain == "api.githubcopilot.com"

        authed = await service.store_static_auth(
            user_id="user-123",
            server_id=created.id,
            request=McpStaticAuthRequest(secret=SecretStr("github-token-secret")),
        )
        assert authed.auth.authenticated is True
        assert "github-token-secret" not in authed.model_dump_json()
        assert "cret" not in authed.model_dump_json()

        tested = await service.test_server(user_id="user-123", server_id=created.id)

        assert tested.status == McpServerStatus.CONNECTED
        assert tested.trust_state == McpTrustState.TRUSTED
        assert tested.discovered_tools == ("issues.list", "merge_requests.list")
        assert fake_client.seen_config is not None
        assert fake_client.seen_config.headers == {
            "Authorization": "Bearer github-token-secret",
        }

        secret_result = await session.exec(select(UserMcpSecretRecord))
        secret_record = secret_result.one()
        assert secret_record.last_four is None
        vault_result = await session.exec(select(UserSecretRecord))
        vault_record = vault_result.one()
        assert vault_record.ciphertext != b"github-token-secret"
        assert vault_record.credential_id == secret_record.id

        tool_configs = await service.enabled_toolset_configs("user-123")
        assert len(tool_configs) == 1
        assert tool_configs[0].allowed_tools == ("issues.list",)
        assert tool_configs[0].logo is not None
        assert tool_configs[0].logo.domain == "api.githubcopilot.com"

        updated = await service.update_server(
            user_id="user-123",
            server_id=created.id,
            request=McpServerUpdateRequest(
                url="https://api.githubcopilot.com/other-mcp/",
                non_admin_tools_enabled=True,
            ),
        )
        assert updated.trust_state == McpTrustState.UNTRUSTED
        assert updated.non_admin_tools_enabled is True


@pytest.mark.asyncio
async def test_mcp_service_enforces_user_ownership(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=FakeMcpClient(),
            oauth_client=FakeOAuthClient(),
            url_policy=McpOutboundUrlPolicy(
                OutboundEndpointPolicy(address_resolver=public_addresses),
            ),
        )
        created = await service.create_server(
            user_id="user-123",
            request=McpServerCreateRequest(
                label="Sentry",
                url="https://mcp.sentry.dev/mcp",
            ),
        )

        with pytest.raises(McpServerNotFoundError):
            await service.get_server(user_id="other-user", server_id=created.id)


@pytest.mark.asyncio
async def test_mcp_service_creates_direct_preset_with_safe_metadata(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=FakeMcpClient(),
            oauth_client=FakeOAuthClient(),
        )

        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="browser-use-cloud",
            request=McpPresetServerCreateRequest(
                variant_id="api-key",
                label="Browser Use",
                enabled=True,
            ),
        )

        assert created.label == "Browser Use"
        assert created.url == "https://api.browser-use.com/v3/mcp"
        assert created.auth_type == McpAuthType.API_KEY_HEADER
        assert created.status == McpServerStatus.NEEDS_AUTH
        assert created.allowed_tools == ()
        assert created.safe_metadata.preset_id == "browser-use-cloud"
        assert created.safe_metadata.preset_variant_id == "api-key"
        assert created.safe_metadata.logo is not None
        assert created.safe_metadata.logo.domain == "browser-use.com"
        assert created.logo is not None
        assert created.logo.domain == "browser-use.com"
        assert created.safe_metadata.docs_urls

        server_result = await session.exec(select(UserMcpServerRecord))
        server_record = server_result.one()
        assert server_record.auth_config["api_key_header_name"] == ("x-browser-use-api-key")
        assert server_record.safe_metadata["preset_id"] == "browser-use-cloud"


@pytest.mark.asyncio
async def test_existing_exa_keyless_server_discovers_optional_api_key_auth(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_client = FakeMcpClient()
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=fake_client,
            oauth_client=FakeOAuthClient(),
        )
        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="exa",
            request=McpPresetServerCreateRequest(
                variant_id="keyless",
                enabled=True,
            ),
        )
        record = await service.get_server_record(
            user_id="user-123",
            server_id=created.id,
        )
        record.safe_metadata = {
            **record.safe_metadata,
            "preset_variant_id": "legacy-keyless",
        }
        record.auth_config = {}
        await session.commit()

        existing = await service.get_server(
            user_id="user-123",
            server_id=created.id,
        )
        assert existing.optional_auth is not None
        assert existing.optional_auth.api_key_header_name == "x-api-key"  # pragma: allowlist secret
        assert existing.optional_auth_configured is False

        stored = await service.store_static_auth(
            user_id="user-123",
            server_id=created.id,
            request=McpStaticAuthRequest(
                secret=SecretStr("test-existing-exa-key"),  # pragma: allowlist secret
            ),
        )
        assert stored.optional_auth_configured is True

        await service.test_server(user_id="user-123", server_id=created.id)
        assert fake_client.seen_config is not None
        assert fake_client.seen_config.headers == {
            "x-api-key": "test-existing-exa-key",  # pragma: allowlist secret
        }


@pytest.mark.asyncio
async def test_mcp_service_carries_preset_identity_into_enabled_tool_config(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_client = FakeMcpClient(
        capabilities=DiscoveredMcpCapabilities(
            tools=("browserless_smartscraper",),
        ),
    )
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=fake_client,
            oauth_client=FakeOAuthClient(),
            url_policy=McpOutboundUrlPolicy(
                OutboundEndpointPolicy(address_resolver=public_addresses),
            ),
        )
        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="browserless",
            request=McpPresetServerCreateRequest(
                variant_id="bearer",
                enabled=True,
                allowed_tools=("browserless_smartscraper",),
                non_admin_tools_enabled=True,
            ),
        )
        await service.store_static_auth(
            user_id="user-123",
            server_id=created.id,
            request=McpStaticAuthRequest(
                secret=SecretStr("browserless-test-token"),
            ),
        )
        await service.test_server(user_id="user-123", server_id=created.id)

        tool_configs = await service.enabled_toolset_configs("user-123")

        assert len(tool_configs) == 1
        assert tool_configs[0].preset_id == "browserless"
        assert tool_configs[0].allowed_tools == ("browserless_smartscraper",)
        assert created.non_admin_tools_enabled is True

        await service.update_server(
            user_id="user-123",
            server_id=created.id,
            request=McpServerUpdateRequest(
                url="https://custom-browser.example/mcp",
            ),
        )
        await service.test_server(user_id="user-123", server_id=created.id)

        updated_tool_configs = await service.enabled_toolset_configs("user-123")

        assert len(updated_tool_configs) == 1
        assert updated_tool_configs[0].preset_id is None


@pytest.mark.asyncio
async def test_mcp_service_enforces_preset_url_rules(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=FakeMcpClient(),
            oauth_client=FakeOAuthClient(),
            url_policy=McpOutboundUrlPolicy(
                OutboundEndpointPolicy(address_resolver=public_addresses),
            ),
        )

        with pytest.raises(
            McpPresetConfigurationError,
            match="do not allow URL overrides",
        ):
            await service.create_server_from_preset(
                user_id="user-123",
                preset_id="exa",
                request=McpPresetServerCreateRequest(
                    variant_id="keyless",
                    url="https://mcp.example.com/mcp",
                ),
            )

        with pytest.raises(
            McpPresetConfigurationError,
            match="require a public HTTPS gateway URL",
        ):
            await service.create_server_from_preset(
                user_id="user-123",
                preset_id="mcp-gateway",
                request=McpPresetServerCreateRequest(variant_id="http-bearer"),
            )

        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="mcp-gateway",
            request=McpPresetServerCreateRequest(
                variant_id="http-bearer",
                url="https://mcp-gateway.example.com/mcp",
                allowed_tools=("duckduckgo_search", "brave_web_search"),
            ),
        )

        assert created.url == "https://mcp-gateway.example.com/mcp"
        assert created.allowed_tools == ("duckduckgo_search", "brave_web_search")
        assert created.safe_metadata.preset_id == "mcp-gateway"


@pytest.mark.asyncio
async def test_mcp_service_persists_discovered_tool_definitions_with_guidance(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_client = FakeMcpClient(
        capabilities=DiscoveredMcpCapabilities(
            tools=("web_fetch_exa", "web_search_exa"),
            tool_definitions=(
                DiscoveredMcpTool(
                    name="web_search_exa",
                    description="Search the web.",
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                ),
                DiscoveredMcpTool(
                    name="web_fetch_exa",
                    description="Fetch a page.",
                    input_schema={"type": "object"},
                ),
            ),
        ),
    )
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=fake_client,
            oauth_client=FakeOAuthClient(),
        )
        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="exa",
            request=McpPresetServerCreateRequest(
                variant_id="keyless",
                enabled=True,
            ),
        )

        tested = await service.test_server(user_id="user-123", server_id=created.id)

        assert tested.status == McpServerStatus.CONNECTED
        assert tested.discovered_tools == ("web_fetch_exa", "web_search_exa")
        assert len(tested.safe_metadata.tool_definitions) == 2
        search_tool = tested.safe_metadata.tool_definitions[0]
        assert search_tool.name == "web_search_exa"
        assert search_tool.input_schema["type"] == "object"
        assert search_tool.guidance is not None
        assert search_tool.guidance.recommended is True
        assert search_tool.guidance.read_only is True


def test_discovered_tool_cache_prioritizes_allowed_tools_before_cap() -> None:
    tools = tuple(
        DiscoveredMcpTool(
            name=f"tool-{index}",
            description=f"Tool {index}",
            input_schema={"type": "object"},
        )
        for index in range(MAX_DISCOVERED_TOOL_DEFINITIONS + 1)
    )

    metadata = safe_metadata_with_discovered_tools(
        {},
        tools,
        preferred_tool_names=("tool-100",),
    )

    definitions = metadata["tool_definitions"]
    assert isinstance(definitions, list)
    assert len(definitions) == MAX_DISCOVERED_TOOL_DEFINITIONS
    first_definition = definitions[0]
    assert isinstance(first_definition, dict)
    assert first_definition["name"] == "tool-100"


@pytest.mark.asyncio
async def test_mcp_service_oauth_start_and_callback_store_encrypted_tokens(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    oauth_client = FakeOAuthClient()
    oauth_state_signer = McpOAuthStateSigner(
        secret=SecretStr("test-mcp-oauth-state-secret"),
    )
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=FakeMcpClient(),
            oauth_client=oauth_client,
            oauth_state_signer=oauth_state_signer,
        )
        created = await service.create_server(
            user_id="user-123",
            request=McpServerCreateRequest(
                label="GitLab",
                url="https://gitlab.com/api/v4/mcp",
                auth_type=McpAuthType.OAUTH_AUTHORIZATION_CODE,
                enabled=True,
            ),
        )

        start_response = await service.start_oauth(
            user_id="user-123",
            server_id=created.id,
            request=McpOAuthStartRequest(
                redirect_uri=(f"https://app.example.com/api/mcp/servers/{created.id}/auth/callback"),
                scopes=("read_api",),
            ),
        )

        callback_state = parse_qs(
            urlparse(start_response.authorization_url).query,
        )["state"][0]
        signed_state = oauth_state_signer.verify(
            callback_state,
            server_id=created.id,
        )
        assert signed_state.user_id == "user-123"
        state_result = await session.exec(select(UserMcpOAuthStateRecord))
        state_record = state_result.one()
        assert state_record.state_hash == state_hash(callback_state)
        state_secret_result = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.credential_id) == state_record.id,
                col(UserSecretRecord.purpose) == SecretPurpose.MCP_OAUTH_STATE.value,
            ),
        )
        state_secret_record = state_secret_result.one()
        assert state_secret_record.ciphertext != b"pkce-verifier"

        callback_result = await service.complete_oauth(
            server_id=created.id,
            code="provider-code",
            state=callback_state,
        )

        assert callback_result.status == McpServerStatus.UNTESTED
        assert oauth_client.exchanged_code == "provider-code"
        assert oauth_client.exchanged_verifier == "pkce-verifier"

        token_result = await session.exec(
            select(UserMcpSecretRecord).where(
                col(UserMcpSecretRecord.secret_kind) == McpSecretKind.OAUTH_TOKEN.value,
            ),
        )
        token_record = token_result.one()
        assert token_record.last_four is None
        token_secret_result = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.credential_id) == token_record.id,
                col(UserSecretRecord.purpose) == SecretPurpose.MCP_SERVER_SECRET.value,
            ),
        )
        token_secret_record = token_secret_result.one()
        assert token_secret_record.ciphertext != OAUTH_ACCESS_TOKEN.encode()

        server_result = await session.exec(select(UserMcpServerRecord))
        server_record = server_result.one()
        assert server_record.status == McpServerStatus.UNTESTED.value

        secret_store = McpSecretStore(session, user_secret_vault)
        token = await secret_store.load_oauth_token("user-123", created.id)
        assert token is not None
        assert token.access_token == OAUTH_ACCESS_TOKEN


@pytest.mark.asyncio
async def test_mcp_service_oauth_start_reuses_stored_client(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    oauth_client = FakeOAuthClient()
    async with sqlite_session_factory() as session:
        service = UserMcpServerService(
            session=session,
            secret_vault=user_secret_vault,
            mcp_client=FakeMcpClient(),
            oauth_client=oauth_client,
            oauth_state_signer=McpOAuthStateSigner(
                secret=SecretStr("test-mcp-oauth-state-secret"),
            ),
        )
        created = await service.create_server_from_preset(
            user_id="user-123",
            preset_id="google-workspace",
            request=McpPresetServerCreateRequest(
                variant_id="gmail",
                enabled=True,
            ),
        )
        secret_store = McpSecretStore(session, user_secret_vault)
        await secret_store.store_oauth_client_info(
            user_id="user-123",
            server_id=created.id,
            client_info=McpOAuthClientInfo(
                client_id="stored-google-client",
                client_secret=CLIENT_SECRET,
                token_endpoint="https://oauth2.googleapis.com/token",  # noqa: S106
                authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
                scopes=("scope-one", "scope-two"),
            ),
        )

        await service.start_oauth(
            user_id="user-123",
            server_id=created.id,
            request=McpOAuthStartRequest(
                redirect_uri="https://app.example.com/api/mcp/auth/callback",
            ),
        )

    prepared_request = oauth_client.prepared_request
    assert prepared_request is not None
    assert prepared_request.client_id == "stored-google-client"
    assert prepared_request.client_secret is not None
    assert prepared_request.client_secret.get_secret_value() == CLIENT_SECRET
    assert prepared_request.scopes == ("scope-one", "scope-two")
    assert prepared_request.token_endpoint_auth_method == McpTokenEndpointAuthMethod.CLIENT_SECRET_POST

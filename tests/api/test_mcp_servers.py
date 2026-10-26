import ipaddress
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlmodel import select
from starlette.requests import Request

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.api.mcp_servers import create_mcp_servers_router
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.client import DiscoveredMcpCapabilities, McpClientConnectionConfig
from voxos.mcp.oauth_state import McpOAuthStateSigner
from voxos.mcp.records import UserMcpSecretRecord
from voxos.mcp.settings import McpSettings
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    TrustedOutboundEndpoint,
    TrustedOutboundEndpointKind,
)


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


@dataclass(slots=True)
class FakeMcpClient:
    seen_config: McpClientConnectionConfig | None = None

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        self.seen_config = config
        return DiscoveredMcpCapabilities(tools=("issues.list",))


class RecordingMcpToolCatalogCache(McpToolCatalogCache):
    def __init__(self) -> None:
        super().__init__(max_entries=10, ttl_seconds=60)
        self.invalidated_users: list[str] = []

    async def invalidate_user(self, user_id: str) -> int:
        self.invalidated_users.append(user_id)
        return await super().invalidate_user(user_id)


def authenticated_user(user_id: str = "local-user-123") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


def create_test_client(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    fake_mcp_client: FakeMcpClient,
    user_id: str = "local-user-123",
    http_handler: httpx.MockTransport | None = None,
    oauth_state_signer: McpOAuthStateSigner | None = None,
    mcp_settings: McpSettings | None = None,
    trusted_endpoint_urls: tuple[str, ...] = (),
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_mcp_servers_router(
            auth_dependency=StaticAuthDependency(authenticated_user(user_id)),
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=httpx.AsyncClient(
                transport=http_handler or httpx.MockTransport(empty_response),
            ),
            mcp_client=fake_mcp_client,
            mcp_settings=mcp_settings,
            oauth_state_signer=oauth_state_signer,
            outbound_endpoint_policy=OutboundEndpointPolicy(
                trusted_endpoints=tuple(
                    TrustedOutboundEndpoint(
                        purpose=OutboundEndpointPurpose.MCP,
                        kind=TrustedOutboundEndpointKind.LOCAL_CONTAINER,
                        url=url,
                    )
                    for url in trusted_endpoint_urls
                ),
                address_resolver=public_addresses,
            ),
            mcp_tool_catalog_cache=mcp_tool_catalog_cache,
        ),
    )
    return TestClient(app)


async def public_addresses(
    host: str,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
    del host
    return (ipaddress.ip_address("93.184.216.34"),)


def empty_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code=404, request=request)


def oauth_callback_response(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and str(request.url) == "https://example.com/token":
        body = request.content.decode()
        assert "code=provider-code" in body
        assert "redirect_uri=https%3A%2F%2Fapp.example.com%2Fapi%2Fmcp%2Fauth%2Fcallback" in body
        return httpx.Response(
            status_code=200,
            json={"access_token": "oauth-token", "token_type": "Bearer"},
            request=request,
        )
    return empty_response(request)


def monzo_oauth_callback_response(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and str(request.url) == "https://api.monzo.com/oauth2/token":
        body = request.content.decode()
        assert "code=provider-code" in body
        assert "client_secret=monzo-client-secret" in body
        assert "code_verifier" not in body
        return httpx.Response(
            status_code=200,
            json={
                "access_token": "monzo-access-token",
                "refresh_token": "monzo-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
            request=request,
        )
    return empty_response(request)


def test_mcp_servers_endpoint_returns_empty_list(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    response = client.get(
        "/api/mcp/servers",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"servers": []}


def test_mcp_server_persists_non_admin_tool_access(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    cache = RecordingMcpToolCatalogCache()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
        mcp_tool_catalog_cache=cache,
    )
    create_response = client.post(
        "/api/mcp/servers",
        json={
            "label": "Search",
            "url": "https://mcp.example.com/search",
            "non_admin_tools_enabled": True,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert create_response.status_code == 201
    assert create_response.json()["non_admin_tools_enabled"] is True
    server_id = create_response.json()["id"]

    update_response = client.patch(
        f"/api/mcp/servers/{server_id}",
        json={"non_admin_tools_enabled": False},
        headers={"Authorization": "Bearer firebase-token"},
    )
    list_response = client.get(
        "/api/mcp/servers",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert update_response.status_code == 200
    assert update_response.json()["non_admin_tools_enabled"] is False
    assert list_response.status_code == 200
    assert list_response.json()["servers"][0]["non_admin_tools_enabled"] is False
    assert cache.invalidated_users == ["local-user-123", "local-user-123"]


def test_mcp_presets_endpoint_returns_catalog(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    response = client.get(
        "/api/mcp/presets",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == "2026-08-02.1"
    assert body["researched_at"] == "2026-07-24"
    preset_ids = {preset["id"] for preset in body["presets"]}
    assert {
        "aftership",
        "browser-use-cloud",
        "browserless",
        "exa",
        "flightradar24",
        "google-maps-grounding-lite",
        "mcp-gateway",
        "openbnb",
        "paypal",
        "tripgo",
        "viator",
    }.issubset(
        preset_ids,
    )
    assert "duckduckgo-gateway" not in preset_ids
    presets_by_id = {preset["id"]: preset for preset in body["presets"]}
    assert [variant["id"] for variant in presets_by_id["flightradar24"]["variants"]] == ["deployed-https"]
    assert "monzo" not in presets_by_id


def test_flightradar24_local_preset_is_rejected_without_compose_host_allowlist(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    response = client.post(
        "/api/mcp/presets/flightradar24/servers",
        json={"variant_id": "local-compose", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == ("This local MCP preset is not enabled in the current environment")


def test_flightradar24_local_preset_uses_explicit_compose_host_allowlist(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
        trusted_endpoint_urls=("http://flightradar24-mcp:8000/mcp",),
    )

    catalog_response = client.get(
        "/api/mcp/presets",
        headers={"Authorization": "Bearer firebase-token"},
    )
    presets_by_id = {preset["id"]: preset for preset in catalog_response.json()["presets"]}
    assert [variant["id"] for variant in presets_by_id["flightradar24"]["variants"]] == [
        "local-compose",
        "deployed-https",
    ]

    create_response = client.post(
        "/api/mcp/presets/flightradar24/servers",
        json={
            "variant_id": "local-compose",
            "enabled": True,
            "allowed_tools": ["get_live_flights_count"],
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert create_response.status_code == 201
    server_id = create_response.json()["id"]

    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.url == ("http://flightradar24-mcp:8000/mcp")


def test_flightradar24_deployed_preset_accepts_public_https_url(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    response = client.post(
        "/api/mcp/presets/flightradar24/servers",
        json={
            "variant_id": "deployed-https",
            "url": "https://flight-mcp.example.com/custom/mcp",
            "enabled": True,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 201
    assert response.json()["url"] == ("https://flight-mcp.example.com/custom/mcp")


def test_monzo_local_preset_uses_endpoint_bearer_and_brokered_oauth(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
        trusted_endpoint_urls=("http://monzo-mcp:8000/mcp",),
        mcp_settings=McpSettings(
            _env_file=None,
            MCP_OAUTH_STATE_SIGNING_SECRET=(  # noqa: S106
                "test-monzo-oauth-state-signing-secret"
            ),
            MCP_CREDENTIAL_DELEGATION_SIGNING_SECRET=(  # noqa: S106
                "test-monzo-delegation-signing-secret"
            ),
        ),
        http_handler=httpx.MockTransport(monzo_oauth_callback_response),
    )

    create_response = client.post(
        "/api/mcp/presets/monzo/servers",
        json={"variant_id": "local-compose", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert create_response.status_code == 201
    assert create_response.json()["allowed_tools"] == []
    assert create_response.json()["auth_type"] == "static_bearer"
    assert create_response.json()["provider_auth"]["authenticated"] is False
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "test-local-monzo-mcp-endpoint-token"},  # pragma: allowlist secret
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert auth_response.json()["status"] == "needs_auth"

    invalid_oauth_start = client.post(
        f"/api/mcp/servers/{server_id}/auth/start",
        json={
            "redirect_uri": ("https://app.example.com/api/mcp/auth/callback"),
            "client_id": "user_0000000000000000000000",
            "client_secret": "eyJ.invalid.token",  # pragma: allowlist secret
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert invalid_oauth_start.status_code == 400
    assert invalid_oauth_start.json()["detail"] == ("Use the Monzo OAuth client ID, not the API Playground user ID")

    oauth_start = client.post(
        f"/api/mcp/servers/{server_id}/auth/start",
        json={
            "redirect_uri": ("https://app.example.com/api/mcp/auth/callback"),
            "client_id": "monzo-client-id",
            "client_secret": "monzo-client-secret",  # pragma: allowlist secret
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert oauth_start.status_code == 200
    authorization_url = oauth_start.json()["authorization_url"]
    authorization_query = parse_qs(urlparse(authorization_url).query)
    assert authorization_url.startswith("https://auth.monzo.com/?")
    assert "code_challenge" not in authorization_query
    oauth_callback = client.get(
        "/api/mcp/auth/callback",
        params={
            "code": "provider-code",
            "state": authorization_query["state"][0],
        },
    )
    assert oauth_callback.status_code == 200

    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert auth_response.status_code == 200
    assert test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.url == "http://monzo-mcp:8000/mcp"
    assert fake_mcp_client.seen_config.headers == {
        "Authorization": "Bearer test-local-monzo-mcp-endpoint-token",
        "X-MCP-Credential-Delegation": (fake_mcp_client.seen_config.headers["X-MCP-Credential-Delegation"]),
    }
    assert fake_mcp_client.seen_config.headers["X-MCP-Credential-Delegation"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_mcp_servers_endpoint_stores_static_auth_and_tests_connection(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
    )

    create_response = client.post(
        "/api/mcp/servers",
        json={
            "label": "GitHub",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_type": "static_bearer",
            "enabled": True,
            "allowed_tools": ["issues.list"],
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert create_response.status_code == 201
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "github-token-secret"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert auth_response.status_code == 200
    assert auth_response.json()["auth"] == {
        "authenticated": True,
        "needs_reauth": False,
        "expires_at": None,
        "scopes": [],
    }
    assert "github-token-secret" not in auth_response.text
    assert "cret" not in auth_response.text

    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert test_response.status_code == 200
    body = test_response.json()
    assert body["status"] == "connected"
    assert body["discovered_tools"] == ["issues.list"]
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.headers == {
        "Authorization": "Bearer github-token-secret",
    }

    async with sqlite_session_factory() as session:
        metadata_result = await session.exec(select(UserMcpSecretRecord))
        metadata_record = metadata_result.one()
        secret_result = await session.exec(select(UserSecretRecord))
        secret_record = secret_result.one()
    assert metadata_record.last_four is None
    assert secret_record.ciphertext != b"github-token-secret"


def test_mcp_preset_endpoint_creates_direct_server_without_url_entry(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    response = client.post(
        "/api/mcp/presets/exa/servers",
        json={"variant_id": "keyless", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "Exa"
    assert body["url"] == "https://mcp.exa.ai/mcp"
    assert body["auth_type"] == "none"
    assert body["optional_auth"]["auth_type"] == "api_key_header"
    assert body["optional_auth"]["api_key_header_name"] == "x-api-key"  # pragma: allowlist secret
    assert body["optional_auth_configured"] is False
    assert body["safe_metadata"]["preset_id"] == "exa"
    assert body["safe_metadata"]["preset_variant_id"] == "keyless"


def test_exa_keyless_server_can_add_and_remove_optional_api_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
    )
    create_response = client.post(
        "/api/mcp/presets/exa/servers",
        json={"variant_id": "keyless", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "test-exa-api-key"},  # pragma: allowlist secret
        headers={"Authorization": "Bearer firebase-token"},
    )
    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert auth_response.status_code == 200
    assert auth_response.json()["auth_type"] == "none"
    assert auth_response.json()["optional_auth_configured"] is True
    assert "test-exa-api-key" not in auth_response.text
    assert test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.headers == {
        "x-api-key": "test-exa-api-key",
    }

    revoke_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/revoke",
        headers={"Authorization": "Bearer firebase-token"},
    )
    anonymous_test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert revoke_response.status_code == 200
    assert revoke_response.json()["optional_auth_configured"] is False
    assert anonymous_test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.headers == {}


def test_manual_no_auth_server_cannot_store_an_optional_secret(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )
    create_response = client.post(
        "/api/mcp/servers",
        json={
            "label": "Public server",
            "url": "https://public.example.com/mcp",
            "auth_type": "none",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "must-not-be-stored"},  # pragma: allowlist secret
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert auth_response.status_code == 400
    assert "must-not-be-stored" not in auth_response.text


def test_mcp_preset_endpoint_rejects_direct_url_override_and_missing_gateway_url(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )

    direct_response = client.post(
        "/api/mcp/presets/exa/servers",
        json={
            "variant_id": "keyless",
            "url": "https://mcp.example.com/mcp",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    gateway_response = client.post(
        "/api/mcp/presets/mcp-gateway/servers",
        json={"variant_id": "http-bearer"},
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert direct_response.status_code == 400
    assert "do not allow URL overrides" in direct_response.text
    assert gateway_response.status_code == 400
    assert "require a public HTTPS URL" in gateway_response.text


def test_mcp_oauth_static_callback_uses_signed_state_server_id(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    oauth_state_signer = McpOAuthStateSigner(
        secret=SecretStr("test-mcp-oauth-state-secret"),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
        http_handler=httpx.MockTransport(oauth_callback_response),
        oauth_state_signer=oauth_state_signer,
    )

    create_response = client.post(
        "/api/mcp/servers",
        json={
            "label": "OAuth Server",
            "url": "https://example.com/mcp",
            "auth_type": "oauth_authorization_code",
            "enabled": True,
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert create_response.status_code == 201
    server_id = create_response.json()["id"]
    start_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/start",
        json={
            "redirect_uri": "https://app.example.com/api/mcp/auth/callback",
            "authorization_endpoint": "https://example.com/authorize",
            "token_endpoint": "https://example.com/token",
            "client_id": "client-123",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )
    assert start_response.status_code == 200
    callback_state = parse_qs(
        urlparse(start_response.json()["authorization_url"]).query,
    )["state"][0]

    callback_response = client.get(
        "/api/mcp/auth/callback",
        params={"code": "provider-code", "state": callback_state},
    )
    server_response = client.get(
        f"/api/mcp/servers/{server_id}",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert callback_response.status_code == 200
    assert "MCP authentication complete" in callback_response.text
    assert server_response.status_code == 200
    assert server_response.json()["auth"]["authenticated"] is True


def test_mcp_create_endpoints_return_conflict_for_duplicate_label(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
    )
    first_response = client.post(
        "/api/mcp/presets/exa/servers",
        json={"variant_id": "keyless"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    duplicate_preset_response = client.post(
        "/api/mcp/presets/exa/servers",
        json={"variant_id": "keyless"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    duplicate_manual_response = client.post(
        "/api/mcp/servers",
        json={
            "label": "Exa",
            "url": "https://mcp.exa.ai/mcp",
        },
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert first_response.status_code == 201
    assert duplicate_preset_response.status_code == 409
    assert "already in use" in duplicate_preset_response.text
    assert duplicate_manual_response.status_code == 409
    assert "already in use" in duplicate_manual_response.text


def test_mcp_preset_endpoint_uses_api_key_header_default(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
    )
    create_response = client.post(
        "/api/mcp/presets/browser-use-cloud/servers",
        json={"variant_id": "api-key", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "browser-use-secret"},  # pragma: allowlist secret
        headers={"Authorization": "Bearer firebase-token"},
    )
    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert auth_response.status_code == 200
    assert test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.headers == {
        "x-browser-use-api-key": "browser-use-secret",
    }


def test_google_maps_preset_injects_user_vault_api_key_header(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    fake_mcp_client = FakeMcpClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=fake_mcp_client,
    )
    create_response = client.post(
        "/api/mcp/presets/google-maps-grounding-lite/servers",
        json={"variant_id": "api-key", "enabled": True},
        headers={"Authorization": "Bearer firebase-token"},
    )
    server_id = create_response.json()["id"]

    auth_response = client.post(
        f"/api/mcp/servers/{server_id}/auth/static",
        json={"secret": "google-maps-secret"},  # pragma: allowlist secret
        headers={"Authorization": "Bearer firebase-token"},
    )
    test_response = client.post(
        f"/api/mcp/servers/{server_id}/test",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert create_response.status_code == 201
    assert auth_response.status_code == 200
    assert test_response.status_code == 200
    assert fake_mcp_client.seen_config is not None
    assert fake_mcp_client.seen_config.headers == {
        "X-Goog-Api-Key": "google-maps-secret",
    }


def test_mcp_servers_endpoint_enforces_ownership(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
        user_id="user-123",
    )
    other_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        fake_mcp_client=FakeMcpClient(),
        user_id="other-user",
    )
    create_response = user_client.post(
        "/api/mcp/servers",
        json={"label": "Sentry", "url": "https://mcp.sentry.dev/mcp"},
        headers={"Authorization": "Bearer firebase-token"},
    )
    server_id = create_response.json()["id"]

    response = other_client.get(
        f"/api/mcp/servers/{server_id}",
        headers={"Authorization": "Bearer firebase-token"},
    )

    assert response.status_code == 404

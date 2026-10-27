from urllib.parse import parse_qs, urlparse

import httpx
import orjson
import pytest

from voxos.mcp.models import (
    McpOAuthClientInfo,
    McpOAuthStartRequest,
    McpOAuthToken,
    McpTokenEndpointAuthMethod,
)
from voxos.mcp.oauth import McpOAuthHttpClient


@pytest.mark.asyncio
async def test_oauth_discovers_protected_resource_metadata_from_www_authenticate() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(protected_resource_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://example.com/mcp",
            request=oauth_start_request(),
            state="signed-state",
        )

    assert prepared.authorization_url.startswith(
        "https://example.com/oauth/authorize?",
    )
    assert "state=signed-state" in prepared.authorization_url
    assert prepared.client_info.token_endpoint == "https://example.com/oauth/token"
    assert prepared.client_info.registration_endpoint == ("https://example.com/oauth/register")


@pytest.mark.asyncio
async def test_oauth_includes_resource_when_authorization_server_supports_it() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(resource_indicator_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://example.com/mcp",
            request=oauth_start_request(),
            state="signed-state",
        )

        token = await oauth_client.exchange_authorization_code(
            client_info=prepared.client_info,
            code="code-123",
            redirect_uri="https://app.example.com/api/mcp/servers/server-123/auth/callback",
            code_verifier=prepared.code_verifier,
        )

    assert "resource=https%3A%2F%2Fexample.com%2Fmcp" in prepared.authorization_url
    assert prepared.client_info.resource == "https://example.com/mcp"
    assert token.access_token == "token-123"


@pytest.mark.asyncio
async def test_oauth_discovers_origin_authorization_server_metadata() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(origin_metadata_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://example.com/mcp",
            request=oauth_start_request(),
            state="signed-state",
        )

    assert prepared.authorization_url.startswith(
        "https://example.com/oauth/authorize?",
    )
    assert prepared.client_info.token_endpoint == "https://example.com/oauth/token"


@pytest.mark.asyncio
async def test_oauth_uses_manual_endpoints_when_discovery_is_unavailable() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(not_found_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://example.com/mcp",
            request=oauth_start_request(
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",  # noqa: S106
            ),
            state="signed-state",
        )

    assert prepared.authorization_url.startswith("https://auth.example.com/authorize?")
    assert prepared.client_info.token_endpoint == "https://auth.example.com/token"


@pytest.mark.asyncio
async def test_google_oauth_requests_offline_access_and_fresh_consent() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(not_found_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://gmailmcp.googleapis.com/mcp/v1",
            request=oauth_start_request(
                authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint="https://oauth2.googleapis.com/token",  # noqa: S106
            ),
            state="signed-state",
        )

    query = parse_qs(urlparse(prepared.authorization_url).query)
    assert query["access_type"] == ["offline"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["prompt"] == ["consent"]


@pytest.mark.asyncio
async def test_oauth_refresh_preserves_unrotated_refresh_token() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(refresh_without_rotation_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)
        refreshed = await oauth_client.refresh_token(
            client_info=McpOAuthClientInfo(
                client_id="client-123",
                client_secret="client-secret",  # noqa: S106  # pragma: allowlist secret
                token_endpoint="https://1.1.1.1/token",  # noqa: S106
                token_endpoint_auth_method=McpTokenEndpointAuthMethod.CLIENT_SECRET_POST,
            ),
            token=McpOAuthToken(
                access_token="expired-access-token",  # noqa: S106
                refresh_token="durable-refresh-token",  # noqa: S106
            ),
        )

    assert refreshed.access_token == "fresh-access-token"
    assert refreshed.refresh_token == "durable-refresh-token"


@pytest.mark.asyncio
async def test_paypal_oauth_discovers_resource_metadata_and_registers_client() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(paypal_oauth_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://mcp.paypal.com/mcp",
            request=dynamic_oauth_start_request(),
            state="signed-state",
        )

    assert prepared.authorization_url.startswith("https://mcp.paypal.com/authorize?")
    assert prepared.client_info.client_id == "paypal-dynamic-client"
    assert prepared.client_info.token_endpoint == "https://mcp.paypal.com/token"
    assert prepared.client_info.registration_endpoint == "https://mcp.paypal.com/register"


@pytest.mark.asyncio
async def test_openbnb_oauth_uses_origin_metadata_and_registers_client() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(openbnb_oauth_response),
    ) as http_client:
        oauth_client = McpOAuthHttpClient(http_client)

        prepared = await oauth_client.prepare_authorization(
            server_url="https://mcp.openbnb.ai/mcp",
            request=dynamic_oauth_start_request(),
            state="signed-state",
        )

    assert prepared.authorization_url.startswith("https://mcp.openbnb.ai/authorize?")
    assert prepared.client_info.client_id == "openbnb-dynamic-client"
    assert prepared.client_info.token_endpoint == "https://mcp.openbnb.ai/token"
    assert prepared.client_info.registration_endpoint == "https://mcp.openbnb.ai/register"


def oauth_start_request(
    *,
    authorization_endpoint: str | None = None,
    token_endpoint: str | None = None,
) -> McpOAuthStartRequest:
    return McpOAuthStartRequest(
        redirect_uri="https://app.example.com/api/mcp/servers/server-123/auth/callback",
        scopes=("read",),
        client_id="client-123",
        authorization_endpoint=authorization_endpoint,
        token_endpoint=token_endpoint,
    )


def dynamic_oauth_start_request() -> McpOAuthStartRequest:
    return McpOAuthStartRequest(
        redirect_uri="https://app.example.com/api/mcp/auth/callback",
    )


def refresh_without_rotation_response(request: httpx.Request) -> httpx.Response:
    return json_response(
        request,
        {
            "access_token": "fresh-access-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
    )


def protected_resource_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == "https://example.com/mcp":
        return httpx.Response(
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://example.com/.well-known/oauth-protected-resource/mcp"'
                ),
            },
            request=request,
        )
    if str(request.url) == "https://example.com/.well-known/oauth-protected-resource/mcp":
        return json_response(
            request,
            {
                "authorization_servers": ["https://example.com/oauth"],
            },
        )
    if str(request.url) == "https://example.com/.well-known/oauth-authorization-server/oauth":
        return oauth_metadata_response(request)
    return not_found_response(request)


def resource_indicator_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == "https://example.com/mcp":
        return httpx.Response(
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://example.com/.well-known/oauth-protected-resource/mcp"'
                ),
            },
            request=request,
        )
    if str(request.url) == "https://example.com/.well-known/oauth-protected-resource/mcp":
        return json_response(
            request,
            {
                "authorization_servers": ["https://example.com/oauth"],
                "resource": "https://example.com/mcp",
            },
        )
    if str(request.url) == "https://example.com/.well-known/oauth-authorization-server/oauth":
        return oauth_metadata_response(
            request,
            resource_parameter_supported=True,
        )
    if str(request.url) == "https://example.com/oauth/token":
        body = request.content.decode()
        assert "resource=https%3A%2F%2Fexample.com%2Fmcp" in body
        return json_response(request, {"access_token": "token-123"})
    return not_found_response(request)


def origin_metadata_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == "https://example.com/.well-known/oauth-authorization-server":
        return oauth_metadata_response(request)
    return not_found_response(request)


def paypal_oauth_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == "https://mcp.paypal.com/mcp":
        return httpx.Response(
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://mcp.paypal.com/.well-known/oauth-protected-resource"'
                ),
            },
            request=request,
        )
    if str(request.url) == "https://mcp.paypal.com/.well-known/oauth-protected-resource":
        return json_response(
            request,
            {
                "resource": "https://mcp.paypal.com",
                "authorization_servers": ["https://mcp.paypal.com"],
            },
        )
    if str(request.url) == "https://mcp.paypal.com/.well-known/oauth-authorization-server":
        return provider_oauth_metadata_response(request, "https://mcp.paypal.com")
    if str(request.url) == "https://mcp.paypal.com/register":
        return dynamic_registration_response(request, "paypal-dynamic-client")
    return not_found_response(request)


def openbnb_oauth_response(request: httpx.Request) -> httpx.Response:
    if str(request.url) == "https://mcp.openbnb.ai/mcp":
        return httpx.Response(status_code=401, request=request)
    if str(request.url) == "https://mcp.openbnb.ai/.well-known/oauth-authorization-server":
        return provider_oauth_metadata_response(request, "https://mcp.openbnb.ai")
    if str(request.url) == "https://mcp.openbnb.ai/register":
        return dynamic_registration_response(request, "openbnb-dynamic-client")
    return not_found_response(request)


def provider_oauth_metadata_response(
    request: httpx.Request,
    issuer: str,
) -> httpx.Response:
    return json_response(
        request,
        {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "registration_endpoint": f"{issuer}/register",
        },
    )


def dynamic_registration_response(
    request: httpx.Request,
    client_id: str,
) -> httpx.Response:
    payload = orjson.loads(request.content)
    assert payload["redirect_uris"] == ["https://app.example.com/api/mcp/auth/callback"]
    assert payload["token_endpoint_auth_method"] == "none"
    return json_response(
        request,
        {
            "client_id": client_id,
            "token_endpoint_auth_method": "none",
        },
    )


def oauth_metadata_response(
    request: httpx.Request,
    *,
    resource_parameter_supported: bool = False,
) -> httpx.Response:
    return json_response(
        request,
        {
            "authorization_endpoint": "https://example.com/oauth/authorize",
            "token_endpoint": "https://example.com/oauth/token",
            "registration_endpoint": "https://example.com/oauth/register",
            "resource_parameter_supported": resource_parameter_supported,
        },
    )


def json_response(
    request: httpx.Request,
    payload: dict[str, object],
) -> httpx.Response:
    return httpx.Response(
        status_code=200,
        content=orjson.dumps(payload),
        headers={"Content-Type": "application/json"},
        request=request,
    )


def not_found_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code=404, request=request)

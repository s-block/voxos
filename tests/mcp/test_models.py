import pytest
from pydantic import ValidationError

from voxos.mcp.models import (
    McpAuthType,
    McpPresetServerCreateRequest,
    McpServerCreateRequest,
    McpStaticAuthRequest,
    McpTransportType,
)

STATIC_SECRET = "super-secret-token"


def test_mcp_server_create_request_normalizes_safe_inputs() -> None:
    request = McpServerCreateRequest(
        label="  GitLab  ",
        url=" https://gitlab.example.com/api/v4/mcp ",
        transport=McpTransportType.STREAMABLE_HTTP,
        auth_type=McpAuthType.API_KEY_HEADER,
        enabled=True,
        allowed_tools="issues.list, merge_requests.list, issues.list",
        api_key_header_name="PRIVATE-TOKEN",
    )

    assert request.label == "GitLab"
    assert request.url == "https://gitlab.example.com/api/v4/mcp"
    assert request.allowed_tools == ("issues.list", "merge_requests.list")
    assert request.non_admin_tools_enabled is False
    assert request.auth_config().api_key_header_name == "PRIVATE-TOKEN"


def test_mcp_server_create_request_rejects_unsafe_url_and_header() -> None:
    with pytest.raises(ValidationError, match="URL must use http or https"):
        McpServerCreateRequest(label="local", url="file:///tmp/mcp.sock")

    with pytest.raises(ValidationError, match="must not include credentials"):
        McpServerCreateRequest(
            label="Credentials",
            url="https://user:password@mcp.example.com",  # pragma: allowlist secret
        )

    with pytest.raises(ValidationError, match="header name contains invalid"):
        McpServerCreateRequest(
            label="Bad header",
            url="https://mcp.example.com",
            api_key_header_name="Bad Header",
        )

    request = McpServerCreateRequest(label="Local", url="http://localhost:8181/mcp")
    assert request.url == "http://localhost:8181/mcp"


def test_static_auth_request_redacts_secret_repr() -> None:
    request = McpStaticAuthRequest(secret=STATIC_SECRET)

    assert STATIC_SECRET not in repr(request)
    assert request.secret.get_secret_value() == STATIC_SECRET


def test_mcp_preset_server_create_request_normalizes_inputs() -> None:
    request = McpPresetServerCreateRequest(
        label="  MCP Gateway  ",
        variant_id=" http-bearer ",
        enabled=True,
        url=" https://mcp-gateway.example.com/mcp ",
        allowed_tools="duckduckgo_search, brave_web_search, duckduckgo_search",
    )

    assert request.label == "MCP Gateway"
    assert request.variant_id == "http-bearer"
    assert request.url == "https://mcp-gateway.example.com/mcp"
    assert request.allowed_tools == ("duckduckgo_search", "brave_web_search")
    assert request.non_admin_tools_enabled is False


def test_mcp_preset_server_create_request_defers_network_policy() -> None:
    request = McpPresetServerCreateRequest(url="http://localhost:8181/mcp")

    assert request.url == "http://localhost:8181/mcp"

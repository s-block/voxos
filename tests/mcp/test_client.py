from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from voxos.mcp.client import (
    McpClientConnectionConfig,
    McpToolCallResult,
    SdkUserMcpClient,
    discovered_tool_from_sdk_tool,
    hardened_mcp_http_client,
)
from voxos.mcp.errors import McpConnectionTestError, McpToolCallRejectedError
from voxos.mcp.models import McpTransportType
from voxos.security.outbound_endpoints import ResolvedOutboundEndpoint


@dataclass(frozen=True, slots=True)
class AllowAllUrlPolicy:
    async def resolve_url(self, value: str) -> ResolvedOutboundEndpoint:
        return ResolvedOutboundEndpoint(url=value)


def test_discovered_tool_preserves_mcp_read_only_annotation() -> None:
    tool = SimpleNamespace(
        name="lookup_weather",
        description="Look up weather",
        inputSchema={"type": "object"},
        annotations=SimpleNamespace(readOnlyHint=True),
    )

    discovered = discovered_tool_from_sdk_tool(tool)

    assert discovered.read_only_hint is True


@pytest.mark.asyncio
async def test_hardened_mcp_http_client_disables_redirects() -> None:
    client = hardened_mcp_http_client()

    assert client.follow_redirects is False
    await client.aclose()


@pytest.mark.asyncio
async def test_call_tool_classifies_nested_bad_request_as_argument_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request(
        "POST",
        "https://mcp.example.test/tools?credential=sensitive",
    )
    response = httpx.Response(status_code=400, request=request)
    status_error = httpx.HTTPStatusError(
        "bad request",
        request=request,
        response=response,
    )
    client = SdkUserMcpClient()
    monkeypatch.setattr(client, "_url_policy", AllowAllUrlPolicy())
    monkeypatch.setattr(
        client,
        "_call_tool_streamable_http",
        AsyncMock(side_effect=ExceptionGroup("transport failure", [status_error])),
    )

    with pytest.raises(McpToolCallRejectedError) as raised:
        await client.call_tool(
            connection_config(),
            "lookup_weather",
            {"location": "London"},
        )

    assert raised.value.__cause__ is None
    assert "sensitive" not in str(raised.value)
    assert "connection failed" not in str(raised.value).lower()


@pytest.mark.asyncio
async def test_call_tool_preserves_connection_failure_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SdkUserMcpClient()
    monkeypatch.setattr(client, "_url_policy", AllowAllUrlPolicy())
    monkeypatch.setattr(
        client,
        "_call_tool_streamable_http",
        AsyncMock(side_effect=httpx.ConnectError("unreachable")),
    )

    with pytest.raises(McpConnectionTestError, match="connection failed"):
        await client.call_tool(
            connection_config(),
            "lookup_weather",
            {"location": {"address": "London"}},
        )


@pytest.mark.asyncio
async def test_read_only_tool_call_retries_rate_limit_using_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://mcp.example.test/tools")
    response = httpx.Response(
        status_code=429,
        request=request,
        headers={"Retry-After": "2"},
    )
    status_error = httpx.HTTPStatusError(
        "too many requests",
        request=request,
        response=response,
    )
    client = SdkUserMcpClient()
    call_tool = AsyncMock(
        side_effect=(
            ExceptionGroup("transport failure", [status_error]),
            McpToolCallResult(content_text="retried result"),
        ),
    )
    sleep = AsyncMock()
    monkeypatch.setattr(client, "_url_policy", AllowAllUrlPolicy())
    monkeypatch.setattr(client, "_call_tool_streamable_http", call_tool)
    monkeypatch.setattr("voxos.mcp.client.asyncio.sleep", sleep)

    result = await client.call_tool(
        connection_config(retry_safe=True),
        "lookup_weather",
        {"location": "London"},
    )

    assert result.content_text == "retried result"
    assert call_tool.await_count == 2
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_mutating_tool_call_does_not_retry_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = httpx.Request("POST", "https://mcp.example.test/tools")
    response = httpx.Response(status_code=429, request=request)
    status_error = httpx.HTTPStatusError(
        "too many requests",
        request=request,
        response=response,
    )
    client = SdkUserMcpClient()
    call_tool = AsyncMock(
        side_effect=ExceptionGroup("transport failure", [status_error]),
    )
    monkeypatch.setattr(client, "_url_policy", AllowAllUrlPolicy())
    monkeypatch.setattr(client, "_call_tool_streamable_http", call_tool)

    with pytest.raises(McpConnectionTestError, match="rate limit") as raised:
        await client.call_tool(
            connection_config(),
            "create_booking",
            {"date": "2026-09-05"},
        )

    assert raised.value.retryable is True
    assert call_tool.await_count == 1


@pytest.mark.asyncio
async def test_tool_call_applies_request_read_timeout_to_mcp_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_timeout: timedelta | None = None

    class RecordingClientSession:
        def __init__(
            self,
            read_stream: object,
            write_stream: object,
            read_timeout_seconds: timedelta | None = None,
        ) -> None:
            del read_stream, write_stream
            nonlocal observed_timeout
            observed_timeout = read_timeout_seconds

        async def __aenter__(self) -> "RecordingClientSession":
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            del exc_type, exc, traceback

        async def initialize(self) -> None:
            return None

        async def call_tool(
            self,
            tool_name: str,
            arguments: dict[str, object],
        ) -> object:
            del tool_name, arguments
            return SimpleNamespace(content=[])

    monkeypatch.setattr("voxos.mcp.client.ClientSession", RecordingClientSession)
    client = SdkUserMcpClient()

    result = await client._call_tool(  # noqa: SLF001
        AsyncMock(),
        AsyncMock(),
        "lookup_weather",
        {"location": {"address": "London"}},
        read_timeout_seconds=12.5,
    )

    assert result.content_text == ""
    assert observed_timeout == timedelta(seconds=12.5)


def connection_config(*, retry_safe: bool = False) -> McpClientConnectionConfig:
    return McpClientConnectionConfig(
        url="https://mcp.example.test/tools",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        retry_safe=retry_safe,
    )

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, cast, runtime_checkable

import httpx2 as httpx
import orjson
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._stream_protocols import ReadStream, WriteStream
from mcp.shared.message import SessionMessage

from voxos.agent_runtime.models import JsonObject
from voxos.mcp.errors import McpConnectionTestError, McpToolCallRejectedError
from voxos.mcp.models import McpTransportType
from voxos.mcp.url_policy import McpOutboundUrlPolicy
from voxos.security.outbound_endpoints import OutboundEndpointPolicy

CONNECTION_FAILED_MESSAGE = "MCP server connection failed"
RATE_LIMITED_MESSAGE = "MCP server rate limit was exceeded"
DEFAULT_TOOL_RETRY_DELAY_SECONDS = 1.0
MAX_TOOL_RETRY_DELAY_SECONDS = 5.0
MAX_TOOL_CALL_ATTEMPTS = 2
RETRYABLE_HTTP_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class McpClientConnectionConfig:
    url: str
    transport: McpTransportType
    headers: Mapping[str, str]
    timeout_seconds: float = 8.0
    read_timeout_seconds: float = 30.0
    retry_safe: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveredMcpTool:
    name: str
    description: str
    input_schema: JsonObject
    read_only_hint: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveredMcpCapabilities:
    tools: tuple[str, ...]
    tool_definitions: tuple[DiscoveredMcpTool, ...] = ()


@dataclass(frozen=True, slots=True)
class McpToolCallResult:
    content_text: str
    is_error: bool = False
    structured_content: JsonObject | None = None


class UserMcpClient(Protocol):
    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        """Connect to a user MCP server and return safe capability names."""

    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: JsonObject,
    ) -> McpToolCallResult:
        """Connect to a user MCP server and execute one tool."""


class SdkToolLike(Protocol):
    name: str
    description: str | None


@runtime_checkable
class ModelDumpLike(Protocol):
    def model_dump(self, *, mode: str) -> object:
        """Return a JSON-safe model representation."""


class SdkUserMcpClient:
    def __init__(
        self,
        url_policy: McpOutboundUrlPolicy | None = None,
    ) -> None:
        self._url_policy = url_policy or McpOutboundUrlPolicy()

    @classmethod
    def from_settings(cls) -> "SdkUserMcpClient":
        return cls(McpOutboundUrlPolicy(OutboundEndpointPolicy.from_settings()))

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        try:
            prepared_config = await self._prepared_config(config)
            if prepared_config.transport == McpTransportType.SSE:
                return await self._discover_sse(prepared_config)
            return await self._discover_streamable_http(prepared_config)
        except McpConnectionTestError:
            raise
        except Exception as exc:
            raise McpConnectionTestError(CONNECTION_FAILED_MESSAGE) from exc

    async def _discover_streamable_http(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        async with (
            hardened_mcp_http_client(
                headers=dict(config.headers),
                timeout=httpx.Timeout(config.timeout_seconds, read=config.read_timeout_seconds),
            ) as http_client,
            streamable_http_client(config.url, http_client=http_client) as (read_stream, write_stream),
        ):
            return await self._list_tools(
                read_stream,
                write_stream,
                read_timeout_seconds=config.read_timeout_seconds,
            )

    async def _discover_sse(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        async with sse_client(
            config.url,
            headers=dict(config.headers),
            timeout=config.timeout_seconds,
            sse_read_timeout=config.read_timeout_seconds,
            httpx_client_factory=hardened_mcp_http_client,
        ) as (read_stream, write_stream):
            return await self._list_tools(
                read_stream,
                write_stream,
                read_timeout_seconds=config.read_timeout_seconds,
            )

    async def _list_tools(
        self,
        read_stream: ReadStream[SessionMessage | Exception],
        write_stream: WriteStream[SessionMessage],
        *,
        read_timeout_seconds: float,
    ) -> DiscoveredMcpCapabilities:
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=read_timeout_seconds,
        ) as session:
            await session.initialize()
            result = await session.list_tools()
            tool_definitions = tuple(discovered_tool_from_sdk_tool(tool) for tool in result.tools)
            tool_names = tuple(sorted(tool.name for tool in result.tools))
            return DiscoveredMcpCapabilities(
                tools=tool_names,
                tool_definitions=tuple(
                    sorted(tool_definitions, key=lambda tool: tool.name),
                ),
            )

    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: JsonObject,
    ) -> McpToolCallResult:
        prepared_config = await self._prepared_config(config)
        maximum_attempts = MAX_TOOL_CALL_ATTEMPTS if prepared_config.retry_safe else 1
        for attempt in range(maximum_attempts):
            try:
                if prepared_config.transport == McpTransportType.SSE:
                    return await self._call_tool_sse(prepared_config, tool_name, arguments)
                return await self._call_tool_streamable_http(
                    prepared_config,
                    tool_name,
                    arguments,
                )
            except Exception as exc:
                classified_error = classify_tool_call_error(exc)
                if (
                    isinstance(classified_error, McpConnectionTestError)
                    and classified_error.retryable
                    and attempt + 1 < maximum_attempts
                ):
                    await asyncio.sleep(mcp_retry_delay_seconds(classified_error))
                    continue
                if classified_error is exc:
                    raise
                if isinstance(classified_error, McpToolCallRejectedError):
                    raise classified_error from None
                raise classified_error from exc
        message = "MCP tool call attempts were exhausted"
        raise AssertionError(message)

    async def _call_tool_streamable_http(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: JsonObject,
    ) -> McpToolCallResult:
        async with (
            hardened_mcp_http_client(
                headers=dict(config.headers),
                timeout=httpx.Timeout(config.timeout_seconds, read=config.read_timeout_seconds),
            ) as http_client,
            streamable_http_client(config.url, http_client=http_client) as (read_stream, write_stream),
        ):
            return await self._call_tool(
                read_stream,
                write_stream,
                tool_name,
                arguments,
                read_timeout_seconds=config.read_timeout_seconds,
            )

    async def _call_tool_sse(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: JsonObject,
    ) -> McpToolCallResult:
        async with sse_client(
            config.url,
            headers=dict(config.headers),
            timeout=config.timeout_seconds,
            sse_read_timeout=config.read_timeout_seconds,
            httpx_client_factory=hardened_mcp_http_client,
        ) as (read_stream, write_stream):
            return await self._call_tool(
                read_stream,
                write_stream,
                tool_name,
                arguments,
                read_timeout_seconds=config.read_timeout_seconds,
            )

    async def _call_tool(
        self,
        read_stream: ReadStream[SessionMessage | Exception],
        write_stream: WriteStream[SessionMessage],
        tool_name: str,
        arguments: JsonObject,
        *,
        read_timeout_seconds: float,
    ) -> McpToolCallResult:
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=read_timeout_seconds,
        ) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=dict(arguments))
            return mcp_tool_call_result_from_sdk_result(result)

    async def _prepared_config(
        self,
        config: McpClientConnectionConfig,
    ) -> McpClientConnectionConfig:
        endpoint = await self._url_policy.resolve_url(config.url)
        return replace(config, url=endpoint.url)


def hardened_mcp_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
        event_hooks={"response": [raise_mcp_http_status]},
    )


async def raise_mcp_http_status(response: httpx.Response) -> None:
    # Preserve HTTP error classification and reject redirects before the SDK's
    # origin-based redirect handling can issue a second request.
    response.raise_for_status()


def nested_http_status_error(exc: BaseException) -> httpx.HTTPStatusError | None:
    pending = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        if isinstance(current, httpx.HTTPStatusError):
            return current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return None


def contains_http_transport_error(exc: BaseException) -> bool:
    pending = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)
        if isinstance(current, httpx.TransportError):
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return False


def classify_tool_call_error(
    exc: Exception,
) -> McpConnectionTestError | McpToolCallRejectedError:
    if isinstance(exc, McpConnectionTestError | McpToolCallRejectedError):
        return exc
    status_error = nested_http_status_error(exc)
    if status_error is not None:
        status_code = status_error.response.status_code
        if status_code in {400, 422}:
            return McpToolCallRejectedError()
        return McpConnectionTestError(
            RATE_LIMITED_MESSAGE if status_code == 429 else CONNECTION_FAILED_MESSAGE,
            retryable=status_code in RETRYABLE_HTTP_STATUS_CODES,
            retry_after_seconds=retry_after_seconds(status_error.response),
        )
    return McpConnectionTestError(
        CONNECTION_FAILED_MESSAGE,
        retryable=contains_http_transport_error(exc),
    )


def retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max((retry_at - datetime.now(UTC)).total_seconds(), 0.0)


def mcp_retry_delay_seconds(error: McpConnectionTestError) -> float:
    delay = error.retry_after_seconds
    if delay is None:
        delay = DEFAULT_TOOL_RETRY_DELAY_SECONDS
    return min(max(delay, 0.0), MAX_TOOL_RETRY_DELAY_SECONDS)


def discovered_tool_from_sdk_tool(tool: object) -> DiscoveredMcpTool:
    tool_like = cast(SdkToolLike, tool)
    name = str(tool_like.name)
    description_value = tool_like.description
    input_schema = getattr(tool, "inputSchema", None)
    if input_schema is None:
        input_schema = getattr(tool, "input_schema", None)
    annotations = getattr(tool, "annotations", None)
    read_only_hint = bool(
        annotations is not None
        and (
            getattr(annotations, "readOnlyHint", False) is True or getattr(annotations, "read_only_hint", False) is True
        )
    )
    return DiscoveredMcpTool(
        name=name,
        description=description_value if isinstance(description_value, str) else name,
        input_schema=json_object(input_schema),
        read_only_hint=read_only_hint,
    )


def mcp_tool_call_result_from_sdk_result(result: object) -> McpToolCallResult:
    structured_content = json_object_or_none(getattr(result, "structuredContent", None))
    if structured_content is None:
        structured_content = json_object_or_none(getattr(result, "structured_content", None))
    content_text = content_blocks_text(getattr(result, "content", ()))
    if not content_text and structured_content is not None:
        content_text = orjson.dumps(structured_content).decode()
    is_error = bool(getattr(result, "isError", False) or getattr(result, "is_error", False))
    return McpToolCallResult(
        content_text=content_text,
        structured_content=structured_content,
        is_error=is_error,
    )


def content_blocks_text(content: object) -> str:
    if not isinstance(content, list | tuple):
        return ""
    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            text_parts.append(text)
            continue
        block_type = getattr(block, "type", None)
        if isinstance(block_type, str):
            text_parts.append(orjson.dumps(safe_json_value(block)).decode())
    return "\n".join(text_parts)


def safe_json_value(value: object) -> object:
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, ModelDumpLike):
        return value.model_dump(mode="json")
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [safe_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): safe_json_value(item) for key, item in value.items()}
    return str(value)


def json_object(value: object) -> JsonObject:
    if isinstance(value, dict):
        return cast(JsonObject, {str(key): safe_json_value(item) for key, item in value.items()})
    return {}


def json_object_or_none(value: object) -> JsonObject | None:
    if value is None:
        return None
    return json_object(value)

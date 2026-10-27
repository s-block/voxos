import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO

import httpx
import orjson
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, select
from starlette.requests import Request

from voxos.agent_core.integration_tools import integration_tool_schema
from voxos.agent_core.mcp_tools import CompositeAgentToolProvider
from voxos.agent_core.models import AgentEventKind, AgentRunContext, AgentTool, AgentToolProvider, AgentToolResult
from voxos.agent_core.tool_catalog import TOOL_CALL_NAME, TOOL_DESCRIBE_NAME, TOOL_SEARCH_NAME, catalog_tool_id
from voxos.agent_runtime.models import (
    AgentRuntimeEventKind,
    AgentStateEvent,
    AgentToolEvent,
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
    JsonObject,
)
from voxos.agent_runtime.records import AgentScheduleRecord, AgentTranscriptRecord
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.agent_runtime.tool_routing import WEB_SEARCH_ACTION
from voxos.agent_skills.models import SkillCapabilityPreference, SkillCreateRequest
from voxos.agent_skills.services import AgentSkillService
from voxos.api.chats import build_text_chat_service, create_chats_router
from voxos.api.text_settings import create_text_settings_router
from voxos.attachments.settings import AttachmentSettings
from voxos.attachments.storage import AttachmentObjectStore
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.image_generation.providers import (
    GeneratedImage,
    ImageGenerationProviderClient,
    ImageGenerationProviderError,
    ImageGenerationProviderRequest,
)
from voxos.integrations.tool_names import TWILIO_PLACE_CALL_TOOL_NAME
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    McpToolCallResult,
    SdkUserMcpClient,
)
from voxos.mcp.errors import McpConnectionTestError
from voxos.mcp.records import UserMcpServerRecord
from voxos.text.models import ResolvedTextCredentials, TextReasoningMode
from voxos.text.providers import (
    ProviderInputImage,
    TextProviderClient,
    TextProviderEventKind,
    TextProviderIncompleteResponseError,
    TextProviderRequest,
    TextProviderResponseSummary,
    TextProviderStreamEvent,
    TextProviderToolCall,
    TextProviderTransportError,
    TextProviderTransportErrorKind,
    TextToolChoice,
)
from voxos.text.service import (
    MAX_TOOL_DISCOVERY_ROUNDS,
    MAX_TOOL_ROUNDS,
    TOOL_BUDGET_EXHAUSTED_PROMPT,
    TOOL_PROTOCOL_REPAIR_PROMPT,
    TextChatService,
)


@pytest.fixture(name="sqlite_session_factory")
def chat_session_factory(
    sqlite_file_session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    """Isolate the chat runtime's concurrent database transactions."""
    return sqlite_file_session_factory


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    authenticated_user: AuthenticatedUser

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return self.authenticated_user


@dataclass(slots=True)
class FakeTextProviderClient(TextProviderClient):
    use_tool: bool = False
    use_first_attached_tool: bool = False
    tool_route_action: str | None = None
    tool_rounds: int = 0
    tool_name: str = "list_agent_memories"
    tool_names: tuple[str, ...] = ()
    tool_arguments: tuple[dict[str, object], ...] = ()
    tool_argument_batches: tuple[tuple[dict[str, object], ...], ...] = ()
    fail_after_delta: bool = False
    transport_failures_before_events: int = 0
    transport_failure_after_delta: bool = False
    transport_failure_delay_seconds: float = 0.0
    incomplete_after_delta: bool = False
    blank_response_rounds: int = 0
    blank_response_has_reasoning: bool = False
    delta_count: int = 1
    textual_tool_call: bool = False
    xml_tool_call: bool = False
    xml_tool_call_requests: frozenset[int] = frozenset()
    text_before_markup: str = ""
    text_before_tools: str = ""
    requests: list[TextProviderRequest] = field(default_factory=list)
    on_stream_start: Callable[[], None] | None = None

    async def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        if self.on_stream_start is not None:
            self.on_stream_start()
        self.requests.append(request)
        if len(self.requests) <= self.transport_failures_before_events:
            if self.transport_failure_delay_seconds > 0:
                await asyncio.sleep(self.transport_failure_delay_seconds)
            raise TextProviderTransportError(
                provider=request.provider,
                model=request.model,
                kind=TextProviderTransportErrorKind.TIMEOUT,
            )
        if self.textual_tool_call:
            if self.text_before_markup:
                yield TextProviderStreamEvent(kind=TextProviderEventKind.MESSAGE_DELTA, delta=self.text_before_markup)
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.MESSAGE_DELTA,
                delta=f'to=functions.{self.tool_name} {{"request":null}}',
            )
            return
        if self.xml_tool_call or len(self.requests) in self.xml_tool_call_requests:
            if self.text_before_markup:
                yield TextProviderStreamEvent(kind=TextProviderEventKind.MESSAGE_DELTA, delta=self.text_before_markup)
            # Nemotron-style markup, streamed in fragments the way a provider sends it.
            for delta in (
                "\n<tool_call>\n<function=",
                f"{self.tool_name}>\n<parameter=request>\n",
                "null\n</parameter>\n</function>\n</tool_call>\n",
            ):
                yield TextProviderStreamEvent(
                    kind=TextProviderEventKind.MESSAGE_DELTA,
                    delta=delta,
                )
            return
        tool_rounds = max(self.tool_rounds, 1 if self.use_tool else 0)
        if len(self.requests) <= tool_rounds:
            if self.text_before_tools:
                yield TextProviderStreamEvent(kind=TextProviderEventKind.MESSAGE_DELTA, delta=self.text_before_tools)
            tool_round = len(self.requests) - 1
            routed_tool = (
                next(
                    (tool for tool in request.tools if tool.route_action == self.tool_route_action),
                    None,
                )
                if self.tool_route_action is not None
                else None
            )
            tool_name = (
                routed_tool.name
                if routed_tool is not None
                else request.tools[0].name
                if self.use_first_attached_tool and request.tools
                else self.tool_name
            )
            if tool_round < len(self.tool_names):
                tool_name = self.tool_names[tool_round]
            if tool_round < len(self.tool_argument_batches):
                argument_batch = self.tool_argument_batches[tool_round]
            else:
                arguments = self.tool_arguments[tool_round] if tool_round < len(self.tool_arguments) else {}
                argument_batch = (arguments,)
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.TOOL_CALLS,
                tool_calls=tuple(
                    TextProviderToolCall(
                        call_id=(
                            f"call-{len(self.requests)}"
                            if len(argument_batch) == 1
                            else f"call-{len(self.requests)}-{index}"
                        ),
                        name=tool_name,
                        arguments_json=arguments,
                    )
                    for index, arguments in enumerate(argument_batch, start=1)
                ),
            )
            return
        if len(self.requests) <= tool_rounds + self.blank_response_rounds:
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.USAGE,
                usage={
                    "input_tokens": 7,
                    "completion_tokens": 0,
                    "total_tokens": 7,
                },
            )
            if self.blank_response_has_reasoning:
                yield TextProviderStreamEvent(
                    kind=TextProviderEventKind.RESPONSE_SUMMARY,
                    response_summary=TextProviderResponseSummary(
                        saw_reasoning_content=True,
                    ),
                )
            return
        for _ in range(self.delta_count):
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.MESSAGE_DELTA,
                delta="Hello",
            )
        if self.transport_failure_after_delta:
            raise TextProviderTransportError(
                provider=request.provider,
                model=request.model,
                kind=TextProviderTransportErrorKind.TIMEOUT,
            )
        if self.fail_after_delta:
            message = "provider exploded"
            raise RuntimeError(message)
        yield TextProviderStreamEvent(
            kind=TextProviderEventKind.USAGE,
            usage={
                "input_tokens": 7,
                "output_tokens": 3,
                "total_tokens": 10,
            },
        )
        if self.incomplete_after_delta:
            reason = "max_output_tokens"
            raise TextProviderIncompleteResponseError(reason)


@dataclass(slots=True)
class FakeImageGenerationProviderClient(ImageGenerationProviderClient):
    content: bytes
    requests: list[ImageGenerationProviderRequest] = field(default_factory=list)

    async def generate(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        del credentials
        self.requests.append(request)
        return GeneratedImage(content=self.content)


@dataclass(slots=True)
class ContentFilteredImageGenerationProviderClient(
    ImageGenerationProviderClient,
):
    requests: list[ImageGenerationProviderRequest] = field(default_factory=list)

    async def generate(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        del credentials
        self.requests.append(request)
        message = (
            "NVIDIA's content filter rejected the image prompt. Do not retry it "
            "unchanged; revise the prompt to remove or generalize potentially "
            "restricted content."
        )
        raise ImageGenerationProviderError(message)


@dataclass(slots=True)
class CapturedFilteredImageRetryTextProvider(TextProviderClient):
    requests: list[TextProviderRequest] = field(default_factory=list)

    async def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        self.requests.append(request)
        request_number = len(self.requests)
        if request_number == 1:
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.TOOL_CALLS,
                tool_calls=(
                    TextProviderToolCall(
                        call_id="call-image-filtered",
                        name="generate_image",
                        arguments_json={
                            "action": "generate",
                            "prompt": "A recognizable television couple",
                        },
                    ),
                ),
            )
            return
        if request_number == 2:
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.TOOL_CALLS,
                tool_calls=(
                    TextProviderToolCall(
                        call_id="call-image-retry",
                        name="generate_image<|channel|>commentary",
                        arguments_json={
                            "action": "generate",
                            "prompt": "A fictional beach wedding couple",
                        },
                    ),
                ),
            )
            return
        yield TextProviderStreamEvent(
            kind=TextProviderEventKind.MESSAGE_DELTA,
            delta=("The image provider filtered the prompt, so I could not generate the image."),
        )


@dataclass(slots=True)
class BlockingTextProviderClient(TextProviderClient):
    started: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)

    async def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        del request
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()
        if False:
            yield TextProviderStreamEvent(kind=TextProviderEventKind.MESSAGE_DELTA)


STATIC_SPECIALIST_TOOL_EXECUTED = "The test should only invoke the catalog bridge"


async def discover_exa_fixture_capabilities(
    client: SdkUserMcpClient,
    config: McpClientConnectionConfig,
) -> DiscoveredMcpCapabilities:
    del client
    assert config.url == "https://mcp.exa.ai/mcp"
    return DiscoveredMcpCapabilities(
        tools=("web_search_exa",),
        tool_definitions=(
            DiscoveredMcpTool(
                name="web_search_exa",
                description="Search the web in real time.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                read_only_hint=True,
            ),
        ),
    )


async def call_exa_fixture_tool(
    client: SdkUserMcpClient,
    config: McpClientConnectionConfig,
    tool_name: str,
    arguments: JsonObject,
) -> McpToolCallResult:
    del client
    assert config.url == "https://mcp.exa.ai/mcp"
    assert tool_name == "web_search_exa"
    assert isinstance(arguments["query"], str)
    return McpToolCallResult(content_text="Fixture web search result")


@pytest.fixture
def stub_exa_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SdkUserMcpClient, "discover_capabilities", discover_exa_fixture_capabilities)
    monkeypatch.setattr(SdkUserMcpClient, "call_tool", call_exa_fixture_tool)


@dataclass(frozen=True, slots=True)
class StaticSpecialistToolProvider(AgentToolProvider):
    tools: tuple[AgentTool, ...]

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        del context
        return self.tools

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: dict[str, object],
    ) -> AgentToolResult:
        del context, tool_name, call_id, arguments_json
        raise AssertionError(STATIC_SPECIALIST_TOOL_EXECUTED)


@dataclass(slots=True)
class RecordingChatToolProvider(AgentToolProvider):
    tools: tuple[AgentTool, ...]
    calls: list[tuple[str, JsonObject]] = field(default_factory=list)

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        return self.tools

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        self.calls.append((tool_name, arguments_json))
        return AgentToolResult(call_id=call_id, name=tool_name, output_text="Tool completed")


def deferred_phone_tools() -> tuple[AgentTool, ...]:
    return (
        AgentTool(
            name="twilio_place_call",
            description="Call a saved contact with an objective.",
            source=ToolGrantScope.INTERNAL,
            parameters_schema=integration_tool_schema(TWILIO_PLACE_CALL_TOOL_NAME),
        ),
        AgentTool(
            name="search_contacts",
            description="Find saved contacts.",
            source=ToolGrantScope.INTERNAL,
            parameters_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        *(
            AgentTool(name=f"specialist_{index}", description="Specialist capability", source=ToolGrantScope.INTERNAL)
            for index in range(8)
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("correct_arguments", [False, True])
async def test_deferred_phone_call_can_complete_discovery_and_repair_arguments(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    correct_arguments: bool,
) -> None:
    tools = deferred_phone_tools()
    phone_id, contacts_id = (catalog_tool_id(tool) for tool in tools[:2])
    call_arguments: JsonObject = {"contact_id": "contact-ben", "objective": "Introduce yourself as Josh's assistant"}
    steps = [
        (TOOL_SEARCH_NAME, {"query": "phone call"}),
        (TOOL_SEARCH_NAME, {"query": "contacts"}),
    ]
    if correct_arguments:
        steps.append((TOOL_DESCRIBE_NAME, {"tool_id": phone_id}))
    steps.append((TOOL_CALL_NAME, {"tool_id": contacts_id, "arguments": {"query": "Ben"}}))
    if not correct_arguments:
        steps.extend(
            [
                (
                    TOOL_CALL_NAME,
                    {
                        "tool_id": phone_id,
                        "arguments": {"contact_id": "contact-ben", "instruction": "Introduce yourself"},
                    },
                ),
                (TOOL_DESCRIBE_NAME, {"tool_id": phone_id}),
            ]
        )
    steps.append((TOOL_CALL_NAME, {"tool_id": phone_id, "arguments": call_arguments}))
    provider_client = FakeTextProviderClient(
        tool_rounds=len(steps),
        tool_names=tuple(name for name, _ in steps),
        tool_arguments=tuple(arguments for _, arguments in steps),
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    tool_provider = RecordingChatToolProvider(tools)
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=tool_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"text": "Call Ben"})
        assert event_names(response.text)[-2:] == ["message_completed", "run_completed"]
        assert tool_provider.calls == [("search_contacts", {"query": "Ben"}), ("twilio_place_call", call_arguments)]
        assert ("tool_call_failed" in event_names(response.text)) == (not correct_arguments)
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "transport", "incomplete", "markup"])
async def test_failed_chat_preserves_filtered_partial_reply_in_history(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    failure: str,
) -> None:
    provider_client = FakeTextProviderClient(
        fail_after_delta=failure == "provider",
        transport_failure_after_delta=failure == "transport",
        incomplete_after_delta=failure == "incomplete",
        textual_tool_call=failure == "markup",
        text_before_markup="Hello" if failure == "markup" else "",
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"text": "Call Ben"})
    assert event_names(response.text)[-1] == "run_failed"
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["text"] == "Hello"
    assert detail["messages"][-1]["interrupted"] is True
    assert detail["messages"][-1]["is_final"] is False
    assert detail["tool_events"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("exceed_limit", [False, True])
async def test_tool_discovery_is_bounded_without_withdrawing_execution_tools(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    exceed_limit: bool,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=MAX_TOOL_DISCOVERY_ROUNDS + int(exceed_limit),
        tool_name=TOOL_SEARCH_NAME,
        tool_arguments=tuple({"query": "phone"} for _ in range(MAX_TOOL_DISCOVERY_ROUNDS + 1)),
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    tool_provider = RecordingChatToolProvider(deferred_phone_tools())
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=tool_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"text": "Find the phone tool"})
        assert event_names(response.text)[-1] == ("run_failed" if exceed_limit else "run_completed")
        names = {tool.name for tool in provider_client.requests[-1].tools}
        assert TOOL_CALL_NAME in names
        assert not names.intersection({TOOL_SEARCH_NAME, TOOL_DESCRIBE_NAME})
        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert len(detail["tool_events"]) == MAX_TOOL_DISCOVERY_ROUNDS
        assert tool_provider.calls == []
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_delta", [False, True])
async def test_chat_history_retains_visible_prose_from_earlier_tool_rounds(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    fail_after_delta: bool,
) -> None:
    tools = deferred_phone_tools()
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name=TOOL_CALL_NAME,
        tool_arguments=({"tool_id": catalog_tool_id(tools[1]), "arguments": {"query": "Ben"}},),
        text_before_tools="Checking Ben. ",
        fail_after_delta=fail_after_delta,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=RecordingChatToolProvider(tools),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(f"/api/chats/{chat['id']}/messages/stream", json={"text": "Find Ben"})
        detail = client.get(f"/api/chats/{chat['id']}").json()
        assert detail["messages"][-1]["text"] == "Checking Ben. Hello"
        assert detail["messages"][-1]["interrupted"] is fail_after_delta
        assert event_names(response.text)[-1] == ("run_failed" if fail_after_delta else "run_completed")
    finally:
        await http_client.aclose()


@dataclass(slots=True)
class RequiredToolProvider(AgentToolProvider):
    executions: int = 0
    include_specialists: bool = False
    failure_message: str | None = None

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        del context
        specialists = (
            tuple(
                AgentTool(
                    name=f"specialist_{index}",
                    description=f"Specialist capability {index}",
                    source=ToolGrantScope.INTERNAL,
                )
                for index in range(10)
            )
            if self.include_specialists
            else ()
        )
        return (
            AgentTool(
                name="required_browser_task",
                description="Run the exact browser task bound to this scheduled run.",
                source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.HIGH,
                required=True,
            ),
            *specialists,
        )

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: dict[str, object],
    ) -> AgentToolResult:
        del context, arguments_json
        self.executions += 1
        if self.failure_message is not None:
            raise RuntimeError(self.failure_message)
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text="Invoice total: £42",
        )


@dataclass(slots=True)
class PartiallyUnavailableSearchToolProvider(AgentToolProvider):
    executions: list[str] = field(default_factory=list)

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        del context
        return (
            AgentTool(
                name="search_contacts",
                description="Search contacts.",
                parameters_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.LOW,
                parallel_safe=True,
            ),
        )

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: dict[str, object],
    ) -> AgentToolResult:
        del context
        query = str(arguments_json["query"])
        self.executions.append(query)
        if query == "rate limited":
            message = "MCP server rate limit was exceeded"
            raise McpConnectionTestError(
                message,
                retryable=True,
            )
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text="verified search result",
        )


async def install_exa_server(session_factory: AsyncSessionFactory) -> AgentTool:
    """Register an Exa server and return the routed web search tool it provides.

    Route resolution only considers allowed MCP grants, so without a real server record
    the web.search action resolves to nothing and the tool is filtered out of the run.
    """

    server = UserMcpServerRecord(
        user_id="local-user-123",
        label="Exa",
        transport="streamable_http",
        url="https://mcp.exa.ai/mcp",
        auth_type="none",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "exa"},
        allowed_tools=["web_search_exa"],
    )
    async with session_factory() as session:
        session.add(server)
        await session.commit()
        await session.refresh(server)
    return AgentTool(
        name="mcp_hash_web_search_exa",
        source_tool_name="web_search_exa",
        description="Search the web in real time.",
        parameters_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        source=ToolGrantScope.MCP_SERVER,
        risk=ToolRisk.LOW,
        mcp_server_id=server.id,
        catalog_label="Exa",
        route_action=WEB_SEARCH_ACTION,
        automatic_route_priority=10,
    )


async def grant_web_search(
    session_factory: AsyncSessionFactory,
    *,
    agent_id: str,
    tool: AgentTool,
    also_allow_memories: bool = False,
) -> None:
    memory_grants = (
        (
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        )
        if also_allow_memories
        else ()
    )
    await SqlModelToolAuthorizationService(session_factory=session_factory).set_grants(
        user_id="local-user-123",
        agent_id=agent_id,
        grants=(
            ToolGrantUpdate(
                tool_name="web_search_exa",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=tool.mcp_server_id,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
            *memory_grants,
        ),
    )


@dataclass(slots=True)
class MemoryAttachmentStore(AttachmentObjectStore):
    objects: dict[str, bytes] = field(default_factory=dict)
    get_calls: list[str] = field(default_factory=list)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def get(self, key: str) -> bytes:
        self.get_calls.append(key)
        try:
            return self.objects[key]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[key] = content


def authenticated_user(user_id: str = "local-user-123") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        firebase_user_id=f"firebase-{user_id}",
        email="user@example.com",
        email_verified=True,
    )


def create_test_client(
    *,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    provider_client: FakeTextProviderClient,
    attachment_store: AttachmentObjectStore | None = None,
    chat_service: TextChatService | None = None,
    user_id: str = "local-user-123",
    image_input_enabled: bool = True,
    image_generation_client: ImageGenerationProviderClient | None = None,
) -> TestClient:
    app = FastAPI()
    auth_dependency = StaticAuthDependency(authenticated_user(user_id))
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    app.include_router(
        create_text_settings_router(
            auth_dependency=auth_dependency,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            image_input_enabled=image_input_enabled,
        ),
    )
    attachment_settings = AttachmentSettings(
        image_input_enabled=image_input_enabled,
    )
    app.include_router(
        create_chats_router(
            auth_dependency=auth_dependency,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
            chat_service=chat_service,
            attachment_settings=attachment_settings,
            attachment_store=attachment_store,
            image_generation_client=image_generation_client,
        ),
    )
    return TestClient(app)


def empty_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code=404, request=request)


def save_text_key(client: TestClient) -> None:
    response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "openai",
            "api_key": "test-chat-credential-value",  # pragma: allowlist secret
            "activate": True,
        },
    )
    assert response.status_code == 200


def event_names(stream_text: str) -> list[str]:
    names: list[str] = []
    for line in stream_text.splitlines():
        if line.startswith("event: "):
            names.append(line.removeprefix("event: "))
    return names


def event_payloads(stream_text: str) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for line in stream_text.splitlines():
        if line.startswith("data: "):
            payload = orjson.loads(line.removeprefix("data: "))
            assert isinstance(payload, dict)
            payloads.append(payload)
    return payloads


@pytest.mark.asyncio
async def test_chat_stream_startup_query_budget(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    engine = sqlite_session_factory.kw["bind"]
    assert isinstance(engine, AsyncEngine)
    statement_count = 0
    provider_start_statement_count: int | None = None

    def count_statement(
        _connection: object,
        _cursor: object,
        _statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        nonlocal statement_count
        statement_count += 1

    def capture_provider_start() -> None:
        nonlocal provider_start_statement_count
        provider_start_statement_count = statement_count

    provider_client.on_stream_start = capture_provider_start

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": "Hello"},
        )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert provider_start_statement_count is not None
    assert provider_start_statement_count <= 37
    assert statement_count <= 54


@pytest.mark.asyncio
async def test_new_chat_defaults_to_saved_agent_text_model(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
    )
    save_text_key(client)
    settings_response = client.put(
        "/api/text/settings",
        json={
            "provider": "openai",
            "model": "gpt-5.4-nano",
            "preferred_language": "English",
            "agent_instructions": "Be concise.",
            "temperature": None,
            "allowed_subagent_models": ["gpt-5.4-nano"],
            "subagent_model": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200

    response = client.post("/api/chats", json={})

    assert response.status_code == 201
    assert response.json()["provider"] == "openai"
    assert response.json()["model"] == "gpt-5.4-nano"


def test_chat_summaries_apply_image_input_rollout_flag(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        image_input_enabled=False,
    )

    created = client.post("/api/chats", json={})
    listed = client.get("/api/chats")

    assert created.status_code == 201
    assert created.json()["supports_image_input"] is False
    assert listed.status_code == 200
    assert listed.json()["image_input_enabled"] is False
    assert listed.json()["chats"][0]["supports_image_input"] is False


@pytest.mark.asyncio
async def test_chat_stream_persists_messages_usage_and_events(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat_response = client.post("/api/chats", json={"title": "Support"})
    chat = chat_response.json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Hello there"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "message_delta",
        "usage",
        "message_completed",
        "run_completed",
    ]
    stream_payloads = event_payloads(response.text)
    assert isinstance(stream_payloads[0]["run_id"], str)
    assert isinstance(stream_payloads[0]["created_at"], str)
    assert stream_payloads[0]["created_at"].endswith("+00:00")
    assert len(provider_client.requests) == 1
    assert provider_client.requests[0].reasoning_mode == TextReasoningMode.FAST
    assert provider_client.requests[0].messages[-1].content == "Hello there"

    detail_response = client.get(f"/api/chats/{chat['id']}")
    detail = detail_response.json()

    assert detail_response.status_code == 200
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]
    assert detail["messages"][0]["text"] == "Hello there"
    assert detail["messages"][1]["text"] == "Hello"
    assert detail["messages"][1]["place_cards"] == []
    completed_stream_event = next(event for event in stream_payloads if event["kind"] == "message_completed")
    completed_payload = completed_stream_event["payload"]
    assert isinstance(completed_payload, dict)
    assert completed_payload["message_id"] == detail["messages"][1]["id"]
    assert detail["messages"][0]["created_at"].endswith("+00:00")
    assert detail["usage_events"][0]["input_tokens"] == 7
    assert detail["usage_events"][0]["output_tokens"] == 3
    assert detail["usage_events"][0]["total_tokens"] == 10
    assert detail["usage_events"][0]["created_at"].endswith("+00:00")
    assert not any(event["kind"] == "message_delta" for event in detail["runtime_events"])
    assert any(event["kind"] == "message_completed" for event in detail["runtime_events"])
    persisted_completion = next(event for event in detail["runtime_events"] if event["kind"] == "message_completed")
    assert "message_id" not in persisted_completion["payload_json"]
    assert detail["runtime_events"][0]["created_at"].endswith("+00:00")
    runs = await SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
    ).list_runs(user_id="local-user-123")
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.SUCCEEDED
    assert runs[0].chat_id == chat["id"]


@pytest.mark.asyncio
async def test_chat_stream_adds_fresh_location_only_to_current_provider_request(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Find restaurants near me",
            "location": {
                "latitude": 42.4304,
                "longitude": 19.2594,
                "horizontal_accuracy_m": 25,
                "captured_at": utc_now().isoformat(),
            },
        },
    )

    assert response.status_code == 200
    system_messages = [message.content for message in provider_client.requests[0].messages if message.role == "system"]
    assert any("# Current Device Location" in message for message in system_messages)
    assert any("Latitude: 42.4304000" in message for message in system_messages)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert "voxos_location" not in orjson.dumps(detail).decode()


@pytest.mark.asyncio
async def test_chat_stream_discards_invalid_location_without_blocking_message(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Send this even without a valid location",
            "location": {
                "latitude": 42.4304,
                "longitude": 19.2594,
                "horizontal_accuracy_m": -1,
                "captured_at": utc_now().isoformat(),
            },
        },
    )

    assert response.status_code == 200
    assert len(provider_client.requests) == 1
    system_messages = [message.content for message in provider_client.requests[0].messages if message.role == "system"]
    assert not any("# Current Device Location" in message for message in system_messages)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["messages"][0]["text"] == "Send this even without a valid location"


@pytest.mark.asyncio
async def test_required_run_tool_is_the_only_first_round_choice_and_executes_once(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        use_tool=True,
        use_first_attached_tool=True,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    required_tool_provider = RequiredToolProvider()
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=required_tool_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": "Read my latest bill"},
        )
    finally:
        await http_client.aclose()

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert required_tool_provider.executions == 1
    assert provider_client.requests[0].tool_choice == TextToolChoice.REQUIRED
    assert [tool.name for tool in provider_client.requests[0].tools] == [
        "required_browser_task",
    ]
    assert provider_client.requests[1].tool_choice == TextToolChoice.AUTO


@pytest.mark.asyncio
async def test_required_run_tool_failure_still_gets_a_final_agent_response(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        use_tool=True,
        use_first_attached_tool=True,
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    required_tool_provider = RequiredToolProvider(
        failure_message="The scheduled browser session could not start",
    )
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=required_tool_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": "Read my latest bill"},
        )
    finally:
        await http_client.aclose()

    assert response.status_code == 200
    names = event_names(response.text)
    assert "tool_call_failed" in names
    assert "run_failed" not in names
    assert names[-2:] == ["message_completed", "run_completed"]
    assert required_tool_provider.executions == 1
    recovery_output = provider_client.requests[1].continuation_items[-1]
    assert "could not start" in str(recovery_output["output"])
    assert "give the user a final response" in str(recovery_output["output"])


@pytest.mark.asyncio
async def test_required_run_tool_cannot_execute_again_through_large_catalog(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=2,
        tool_name="required_browser_task",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    required_tool_provider = RequiredToolProvider(include_specialists=True)
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=required_tool_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": "Read my latest bill"},
        )
    finally:
        await http_client.aclose()

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert required_tool_provider.executions == 1
    assert [tool.name for tool in provider_client.requests[0].tools] == [
        "required_browser_task",
    ]
    assert "required_browser_task" not in {tool.name for tool in provider_client.requests[1].tools}
    failures = [payload for payload in event_payloads(response.text) if payload["kind"] == "tool_call_failed"]
    assert failures[-1]["payload"]["error_type"] == "ToolNotExposedError"


@pytest.mark.asyncio
async def test_manual_agent_run_uses_agentic_reasoning_mode(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    run = await run_store.enqueue_run(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"task_prompt": "Investigate this"},
        created_by_user_id="local-user-123",
        session_id=chat["id"],
        chat_id=chat["id"],
    )
    started = await run_store.start_run(
        run_id=run.id,
        user_id="local-user-123",
        lease_owner="test-worker",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )
    assert started is not None
    service = build_text_chat_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(empty_response)),
        text_provider_client=provider_client,
    )

    events = [
        event
        async for event in service.stream_message(
            user_id="local-user-123",
            chat_id=chat["id"],
            text="Investigate this",
            run_id=run.id,
            manage_run=True,
        )
    ]

    assert events[0].payload["reasoning_mode"] == TextReasoningMode.AGENTIC.value
    assert provider_client.requests[0].reasoning_mode == TextReasoningMode.AGENTIC


@pytest.mark.asyncio
async def test_chat_stream_fails_textual_tool_syntax_without_executing_a_tool(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(textual_tool_call=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Look this up"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert names[0] == "run_started"
    assert names[-1] == "run_failed"
    assert "message_delta" not in names
    failure = event_payloads(response.text)[-1]
    failure_payload = failure["payload"]
    assert isinstance(failure_payload, dict)
    assert failure_payload["error_type"] == "TextProviderToolProtocolError"
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user"]
    assert detail["tool_events"] == []


@pytest.mark.usefixtures("stub_exa_mcp")
@pytest.mark.asyncio
async def test_chat_stream_keeps_web_search_visible_and_lets_the_model_select_it(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    web_search = await install_exa_server(sqlite_session_factory)
    provider_client = FakeTextProviderClient(
        use_tool=True,
        tool_route_action=WEB_SEARCH_ACTION,
        tool_arguments=({"query": "Parish Station TV series killings"},),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    await grant_web_search(
        sqlite_session_factory,
        agent_id=chat["agent_id"],
        tool=web_search,
        also_allow_memories=True,
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "What happens in the killings at parish station, the series on tv?"},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert all(request.tool_choice == TextToolChoice.AUTO for request in provider_client.requests)
    opening_web_search = next(
        tool for tool in provider_client.requests[0].tools if tool.route_action == WEB_SEARCH_ACTION
    )
    assert {tool.name for tool in provider_client.requests[0].tools} >= {
        "list_agent_memories",
        opening_web_search.name,
    }


@pytest.mark.usefixtures("stub_exa_mcp")
@pytest.mark.asyncio
async def test_chat_stream_keeps_auto_choice_for_conversation_with_web_available(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    web_search = await install_exa_server(sqlite_session_factory)
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    await grant_web_search(sqlite_session_factory, agent_id=chat["agent_id"], tool=web_search)

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "What is your name? Tell me about yourself."},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert provider_client.requests[0].tool_choice == TextToolChoice.AUTO
    assert any(tool.route_action == WEB_SEARCH_ACTION for tool in provider_client.requests[0].tools)
    assert detail["tool_events"] == []
    assert detail["messages"][-1]["text"] == "Hello"


@pytest.mark.asyncio
async def test_chat_stream_leaves_tool_choice_auto_without_a_web_search_tool(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "What happens in the killings at parish station?"},
    )

    assert response.status_code == 200
    # Tool selection remains model-driven whether or not web search is available.
    assert provider_client.requests[0].tool_choice == TextToolChoice.AUTO


@pytest.mark.usefixtures("stub_exa_mcp")
@pytest.mark.asyncio
async def test_chat_stream_allows_the_model_to_answer_with_web_search_visible(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    web_search = await install_exa_server(sqlite_session_factory)
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    await grant_web_search(sqlite_session_factory, agent_id=chat["agent_id"], tool=web_search)

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Who is the current president of France?"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert names[0] == "run_started"
    assert names[-1] == "run_completed"
    assert provider_client.requests[0].tool_choice == TextToolChoice.AUTO
    assert any(tool.route_action == WEB_SEARCH_ACTION for tool in provider_client.requests[0].tools)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["tool_events"] == []
    assert detail["messages"][-1]["text"] == "Hello"


@pytest.mark.usefixtures("stub_exa_mcp")
@pytest.mark.asyncio
async def test_chat_stream_keeps_other_authorized_tools_visible_with_web_search(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    web_search = await install_exa_server(sqlite_session_factory)
    provider_client = FakeTextProviderClient(
        use_tool=True,
        tool_name="list_agent_memories",
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    await grant_web_search(
        sqlite_session_factory,
        agent_id=chat["agent_id"],
        tool=web_search,
        also_allow_memories=True,
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "What did I save about Parish Station?"},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert provider_client.requests[0].tool_choice == TextToolChoice.AUTO
    assert "list_agent_memories" in {tool.name for tool in provider_client.requests[0].tools}
    assert any(tool.route_action == WEB_SEARCH_ACTION for tool in provider_client.requests[0].tools)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [event["name"] for event in detail["tool_events"]] == [
        "list_agent_memories",
    ]


@pytest.mark.asyncio
async def test_chat_stream_fails_xml_tool_syntax_instead_of_answering_with_markup(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(xml_tool_call=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Look this up"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert names[0] == "run_started"
    assert names[-1] == "run_failed"
    failure_payload = event_payloads(response.text)[-1]["payload"]
    assert isinstance(failure_payload, dict)
    assert failure_payload["error_type"] == "TextProviderToolProtocolError"
    # Only the whitespace around the artifact may stream; the markup itself must not.
    streamed = "".join(
        str(payload["payload"].get("delta", ""))
        for name, payload in zip(names, event_payloads(response.text), strict=True)
        if name == "message_delta" and isinstance(payload["payload"], dict)
    )
    assert streamed.strip() == ""
    detail = client.get(f"/api/chats/{chat['id']}").json()
    # The markup must never be persisted or shown as the assistant's answer.
    assert [message["role"] for message in detail["messages"]] == ["user"]
    assert detail["tool_events"] == []


@pytest.mark.asyncio
async def test_chat_stream_prioritizes_skill_matched_from_current_user_turn(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    skill_service = AgentSkillService(sqlite_session_factory)
    await skill_service.create_user_skill(
        user_id="local-user-123",
        request=SkillCreateRequest(
            name="Grounded Local Day Plan",
            slug="grounded-local-day-plan",
            category="tool_use",
            description="Find verified local restaurants and places with Google Maps.",
            body_markdown="Use search_places before making restaurant recommendations.",
            preferred_capabilities=(
                SkillCapabilityPreference(
                    capability="google-maps-grounding-lite",
                    tool_names=("search_places",),
                ),
            ),
        ),
    )
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Find restaurants in Nova Place with Google Maps reviews."},
    )

    assert response.status_code == 200
    request = provider_client.requests[0]
    assert "# Task-Relevant Skills" in request.messages[0].content
    assert "grounded-local-day-plan" in request.messages[0].content
    assert "Use get_agent_skill to read its full body" in request.messages[0].content
    assert "Preferred: google-maps-grounding-lite: search_places." in request.messages[0].content
    assert "get_agent_skill" in {tool.name for tool in request.tools}


@pytest.mark.asyncio
async def test_chat_stream_exposes_google_maps_directory_and_searches_it(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    skill_service = AgentSkillService(sqlite_session_factory)
    await skill_service.create_user_skill(
        user_id="local-user-123",
        request=SkillCreateRequest(
            name="Grounded Local Day Plan",
            slug="grounded-local-day-plan",
            category="tool_use",
            description="Find local places with Google Maps.",
            body_markdown="Use search_places before making restaurant recommendations.",
            preferred_capabilities=(
                SkillCapabilityPreference(
                    capability="google-maps-grounding-lite",
                    tool_names=("search_places",),
                ),
            ),
        ),
    )
    provider_client = FakeTextProviderClient(
        use_tool=True,
        tool_name=TOOL_SEARCH_NAME,
        tool_arguments=({"query": "Google Maps search_places"},),
    )
    base_service = build_text_chat_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(empty_response)),
        text_provider_client=provider_client,
    )
    maps_tools = tuple(
        AgentTool(
            name=f"mcp_google_maps_{index}",
            source_tool_name="search_places" if index == 0 else f"maps_tool_{index}",
            description=f"Google Maps capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id="google-maps-server",
            catalog_label="Google Maps",
            capability_key="google-maps-grounding-lite",
        )
        for index in range(10)
    )
    chat_service = replace(
        base_service,
        tool_provider=CompositeAgentToolProvider(
            (base_service.tool_provider, StaticSpecialistToolProvider(maps_tools)),
        ),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=chat_service,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Find restaurants in Nova Place with Google Maps reviews."},
    )

    assert response.status_code == 200
    assert len(provider_client.requests) == 2
    first_request = provider_client.requests[0]
    assert "google-maps-grounding-lite: search_places" in first_request.messages[0].content
    assert "Google Maps [google-maps-grounding-lite] (10 tools)" in first_request.messages[1].content
    assert "search_places" in first_request.messages[1].content
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["tool_events"][0]["name"] == TOOL_SEARCH_NAME
    assert "search_places" in detail["tool_events"][0]["output_text"]


@pytest.mark.asyncio
async def test_chat_detail_projects_delegated_output_and_tool_activity(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Delegation"}).json()
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    parent = await run_store.enqueue_run(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"text": "Research this"},
        created_by_user_id="local-user-123",
        session_id=chat["id"],
        chat_id=chat["id"],
    )
    child = await run_store.enqueue_run(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        kind=AgentRunKind.SUBAGENT,
        payload_json={
            "task_prompt": "Research current screenings",
            "provider": "openai",
            "model": "gpt-5.4-nano",
        },
        created_by_user_id="local-user-123",
        parent_run_id=parent.id,
        parent_session_id=chat["id"],
        root_run_id=parent.id,
    )
    started = await run_store.start_run(
        run_id=child.id,
        user_id="local-user-123",
        lease_owner="test-worker",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )
    assert started is not None
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    child_chat = await runtime_store.create_session(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        provider="openai",
        model="gpt-5.4-nano",
        channel="text",
        title="Subagent run",
        voice=None,
        room_name=None,
        room_metadata={},
        prompt_version=None,
        prompt_snapshot="Help the user.",
        prompt_hash=None,
    )
    bound = await run_store.bind_run_to_chat(
        run_id=child.id,
        user_id="local-user-123",
        chat_id=child_chat.id,
    )
    assert bound is not None
    event_time = utc_now()
    await runtime_store.append_transcript_item(
        AgentTranscriptItem(
            session_id=child_chat.id,
            user_id="local-user-123",
            agent_id=chat["agent_id"],
            role=AgentTranscriptRole.ASSISTANT,
            text="The current screenings are listed here.",
            source=AgentTranscriptSource.SUBAGENT_OUTPUT,
            created_at=event_time,
        ),
    )
    await runtime_store.append_tool_event(
        AgentToolEvent(
            session_id=child_chat.id,
            user_id="local-user-123",
            agent_id=chat["agent_id"],
            call_id="call-exa",
            name="mcp_exa_web_search_exa",
            arguments_json={"query": "BFI IMAX screenings"},
            output_text="results",
            created_at=event_time,
        ),
    )
    await runtime_store.append_runtime_event(
        AgentStateEvent(
            session_id=child_chat.id,
            user_id="local-user-123",
            agent_id=chat["agent_id"],
            kind=AgentRuntimeEventKind.TOOL_CALL_COMPLETED,
            payload_json={
                "call_id": "call-exa",
                "name": "mcp_exa_web_search_exa",
                "is_error": False,
            },
            created_at=event_time,
        ),
    )
    completed = await run_store.complete_run(
        run_id=child.id,
        user_id="local-user-123",
        result_json={"characters_count": 39},
    )
    assert completed is not None

    response = client.get(f"/api/chats/{chat['id']}")

    assert response.status_code == 200
    delegated = response.json()["delegated_runs"]
    assert len(delegated) == 1
    assert delegated[0]["status"] == "succeeded"
    assert delegated[0]["output_text"] == "The current screenings are listed here."
    assert delegated[0]["tool_events"][0]["name"] == "mcp_exa_web_search_exa"
    assert delegated[0]["runtime_events"][0]["kind"] == "tool_call_completed"

    summary_response = client.get(f"/api/chats/{chat['id']}/delegated-runs")

    assert summary_response.status_code == 200
    summarized = summary_response.json()["delegated_runs"]
    assert summarized[0]["status"] == "succeeded"
    assert summarized[0]["output_text"] == "The current screenings are listed here."
    assert summarized[0]["tool_events"] == []
    assert summarized[0]["runtime_events"] == []


@pytest.mark.asyncio
async def test_chat_stream_cancellation_stops_provider_and_marks_run_cancelled(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = BlockingTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Stop this run"}).json()
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    service = build_text_chat_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=http_client,
        text_provider_client=provider_client,
    )

    stream = service.stream_message(
        user_id="local-user-123",
        chat_id=chat["id"],
        text="Wait forever",
    )
    started_event = await anext(stream)
    assert started_event.kind.value == "run_started"
    assert started_event.run_id is not None
    pending_event = asyncio.create_task(anext(stream))
    await asyncio.wait_for(provider_client.started.wait(), timeout=1)

    pending_event.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending_event

    await asyncio.wait_for(provider_client.cancelled.wait(), timeout=1)
    runs = await SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
    ).list_runs(user_id="local-user-123")
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.CANCELLED
    runtime_events = await service.runtime_store.list_runtime_events(
        session_id=chat["id"],
        user_id="local-user-123",
        agent_id=chat["agent_id"],
    )
    assert [event.kind for event in runtime_events][-2:] == [
        "run_started",
        "run_cancelled",
    ]
    await http_client.aclose()


@pytest.mark.asyncio
async def test_chat_stream_missing_credentials_preserves_submitted_message(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    chat = client.post("/api/chats", json={"title": "No key"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Hello there"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == ["run_failed"]
    assert len(event_payloads(response.text)) == 1
    assert provider_client.requests == []

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["chat"]["status"] == "started"
    assert [message["text"] for message in detail["messages"]] == ["Hello there"]
    assert [event["kind"] for event in detail["runtime_events"]] == ["run_failed"]
    runs = await SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
    ).list_runs(user_id="local-user-123")
    reflections = tuple(run for run in runs if run.kind == AgentRunKind.REFLECTION)
    assert len(reflections) == 1 and reflections[0].status == AgentRunStatus.QUEUED
    runs = tuple(run for run in runs if run.kind == AgentRunKind.TEXT_CHAT_TURN)
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED


@pytest.mark.asyncio
async def test_chat_stream_provider_failure_closes_sse_without_reraising(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(fail_after_delta=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Failure"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Break"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "message_delta",
        "run_failed",
    ]

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["chat"]["status"] == "started"
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["interrupted"] is True
    assert not any(event["kind"] == "message_delta" for event in detail["runtime_events"])
    runs = await SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
    ).list_runs(user_id="local-user-123")
    reflections = tuple(run for run in runs if run.kind == AgentRunKind.REFLECTION)
    assert len(reflections) == 1 and reflections[0].status == AgentRunStatus.QUEUED
    runs = tuple(run for run in runs if run.kind == AgentRunKind.TEXT_CHAT_TURN)
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED


@pytest.mark.asyncio
async def test_chat_stream_retries_transport_timeout_before_provider_events(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxos.text.service.TRANSIENT_PROVIDER_RETRY_DELAY_SECONDS",
        0.0,
    )
    provider_timings: list[dict[str, bool | float | int | str | None]] = []

    def capture_timing(
        event_type: str,
        attributes: Mapping[str, bool | float | int | str | None],
    ) -> None:
        if event_type == "VoxosTextProviderStream":
            provider_timings.append(dict(attributes))

    monkeypatch.setattr("voxos.text.service.record_text_timing", capture_timing)
    provider_client = FakeTextProviderClient(
        transport_failures_before_events=1,
        transport_failure_delay_seconds=0.01,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Recover this turn"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "message_delta",
        "usage",
        "message_completed",
        "run_completed",
    ]
    assert len(provider_client.requests) == 2
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]
    assert provider_timings[0]["attempt"] == 1
    assert provider_timings[0]["status"] == "retrying"
    assert provider_timings[0]["retry_scheduled"] is True
    assert provider_timings[0]["transport_error_kind"] == "timeout"
    assert float(provider_timings[0]["provider_wait_ms"] or 0) >= 5
    assert provider_timings[1]["attempt"] == 2
    assert provider_timings[1]["status"] == "completed"
    assert provider_timings[1]["retry_scheduled"] is False


@pytest.mark.asyncio
async def test_chat_stream_exhausted_transport_retry_is_actionable_provider_failure(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxos.text.service.TRANSIENT_PROVIDER_RETRY_DELAY_SECONDS",
        0.0,
    )
    provider_client = FakeTextProviderClient(transport_failures_before_events=2)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Fail after the retry"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == ["run_started", "run_failed"]
    assert len(provider_client.requests) == 2
    failure_payload = event_payloads(response.text)[-1]["payload"]
    assert isinstance(failure_payload, dict)
    assert failure_payload["error_type"] == "TextProviderTransportError"
    assert failure_payload["error_message"] == (
        f"OpenAI did not respond in time for model '{provider_client.requests[-1].model}'. "
        "Try again or select another model."
    )
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user"]
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    runs = await run_store.list_runs(user_id="local-user-123")
    reflections = tuple(run for run in runs if run.kind == AgentRunKind.REFLECTION)
    assert len(reflections) == 1 and reflections[0].status == AgentRunStatus.QUEUED
    runs = tuple(run for run in runs if run.kind == AgentRunKind.TEXT_CHAT_TURN)
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED
    assert runs[0].error_type == "TextProviderTransportError"
    run_events = await run_store.list_events(
        user_id="local-user-123",
        run_id=runs[0].id,
    )
    assert run_events[-1].payload_json["failure_category"] == "provider"


@pytest.mark.asyncio
async def test_chat_stream_does_not_retry_transport_failure_after_output(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "voxos.text.service.TRANSIENT_PROVIDER_RETRY_DELAY_SECONDS",
        0.0,
    )
    provider_client = FakeTextProviderClient(transport_failure_after_delta=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Do not duplicate partial output"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "message_delta",
        "run_failed",
    ]
    assert len(provider_client.requests) == 1
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["interrupted"] is True


@pytest.mark.asyncio
async def test_chat_stream_incomplete_response_is_not_marked_completed(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(incomplete_after_delta=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Incomplete"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "List everything"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "message_delta",
        "usage",
        "run_failed",
    ]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][-1]["is_final"] is False
    assert not any(event["kind"] == "message_completed" for event in detail["runtime_events"])
    runs = await SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
    ).list_runs(user_id="local-user-123")
    reflections = tuple(run for run in runs if run.kind == AgentRunKind.REFLECTION)
    assert len(reflections) == 1 and reflections[0].status == AgentRunStatus.QUEUED
    runs = tuple(run for run in runs if run.kind == AgentRunKind.TEXT_CHAT_TURN)
    assert len(runs) == 1
    assert runs[0].status == AgentRunStatus.FAILED


@pytest.mark.asyncio
async def test_chat_stream_retries_an_empty_provider_response(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(blank_response_rounds=1)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Can you use Google Places to search?"},
    )

    assert response.status_code == 200
    assert len(provider_client.requests) == 2
    assert provider_client.requests[1].messages[-1].content == (
        "Your last response did not include a final answer or a structured tool call. "
        "Continue this turn with a useful response. When the request requires current "
        "external information or a named specialist capability, use the appropriate "
        "available tool first. Do not return only internal reasoning or an empty response."
    )
    assert event_names(response.text)[-2:] == ["message_completed", "run_completed"]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["messages"][-1]["text"] == "Hello"


@pytest.mark.asyncio
async def test_agentic_run_retries_reasoning_only_response_in_fast_mode(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        blank_response_rounds=1,
        blank_response_has_reasoning=True,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={}).json()
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    run = await run_store.enqueue_run(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"task_prompt": "Investigate this"},
        created_by_user_id="local-user-123",
        session_id=chat["id"],
        chat_id=chat["id"],
    )
    started = await run_store.start_run(
        run_id=run.id,
        user_id="local-user-123",
        lease_owner="test-worker",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )
    assert started is not None
    service = build_text_chat_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(empty_response)),
        text_provider_client=provider_client,
    )

    events = [
        event
        async for event in service.stream_message(
            user_id="local-user-123",
            chat_id=chat["id"],
            text="Investigate this",
            run_id=run.id,
            manage_run=True,
        )
    ]

    assert events[-1].kind == AgentEventKind.RUN_COMPLETED
    assert [request.reasoning_mode for request in provider_client.requests] == [
        TextReasoningMode.AGENTIC,
        TextReasoningMode.FAST,
    ]


@pytest.mark.asyncio
async def test_chat_stream_does_not_persist_each_delta(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(delta_count=100)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Deltas"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Stream many chunks"},
    )

    assert response.status_code == 200
    assert event_names(response.text).count("message_delta") == 100

    detail = client.get(f"/api/chats/{chat['id']}").json()
    runtime_event_names = [event["kind"] for event in detail["runtime_events"]]
    assert "message_delta" not in runtime_event_names
    assert runtime_event_names == [
        "run_started",
        "usage",
        "message_completed",
        "run_completed",
    ]


@pytest.mark.asyncio
async def test_chat_provider_history_and_detail_reads_are_bounded(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Long"}).json()

    for index in range(25):
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": f"Message {index}"},
        )
        assert response.status_code == 200

    last_request = provider_client.requests[-1]
    assert len(last_request.messages) == 43
    assert last_request.messages[0].role == "system"
    assert last_request.messages[1].role == "system"
    assert "Tools available in this run" in last_request.messages[1].content
    assert last_request.messages[2].role == "system"
    assert "# Conversation Timeline" in last_request.messages[2].content
    assert last_request.messages[3].content != "Message 0"
    assert last_request.messages[-1].content == "Message 24"

    detail_response = client.get(
        f"/api/chats/{chat['id']}?message_limit=3&event_limit=2",
    )
    detail = detail_response.json()
    assert detail_response.status_code == 200
    assert len(detail["messages"]) == 3
    assert len(detail["runtime_events"]) == 2


@pytest.mark.asyncio
async def test_chat_provider_request_includes_iso_conversation_timeline(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Dormant"}).json()
    first_response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Old message"},
    )
    assert first_response.status_code == 200

    async with sqlite_session_factory() as session:
        transcript_result = await session.exec(
            select(AgentTranscriptRecord)
            .where(
                col(AgentTranscriptRecord.session_id) == chat["id"],
                col(AgentTranscriptRecord.user_id) == "local-user-123",
                col(AgentTranscriptRecord.agent_id) == chat["agent_id"],
            )
            .order_by(col(AgentTranscriptRecord.created_at).asc()),
        )
        records = transcript_result.all()
        assert len(records) == 2
        records[0].created_at = datetime(2026, 6, 1, 8, 0, 0, tzinfo=UTC)
        records[1].created_at = datetime(2026, 6, 1, 8, 0, 5, tzinfo=UTC)
        await session.commit()

    second_response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Weeks later"},
    )

    assert second_response.status_code == 200
    last_request = provider_client.requests[-1]
    timeline_message = next(
        message
        for message in last_request.messages
        if message.role == "system" and "# Conversation Timeline" in message.content
    )
    assert "- Current datetime (UTC): " in timeline_message.content
    assert "- Chat started at (UTC): " in timeline_message.content
    assert "- Last prior message at (UTC): 2026-06-01T08:00:05+00:00" in timeline_message.content
    assert "- [1] user at 2026-06-01T08:00:00+00:00" in timeline_message.content
    assert "- [2] assistant at 2026-06-01T08:00:05+00:00" in timeline_message.content
    assert "- [3] user at " in timeline_message.content
    assert "Old message" not in timeline_message.content
    assert last_request.messages[-1].content == "Weeks later"


@pytest.mark.asyncio
async def test_chat_stream_executes_internal_tools_with_same_grants(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(use_tool=True)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Tools"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Use a tool"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert "tool_call_started" in names
    assert "tool_call_completed" in names
    assert names[-2:] == ["message_completed", "run_completed"]
    assert len(provider_client.requests) == 2
    assert provider_client.requests[0].tools[0].name == "list_agent_memories"
    assert provider_client.requests[0].messages[1].role == "system"
    assert "Tools available in this run" in provider_client.requests[0].messages[1].content
    assert "list_agent_memories" in provider_client.requests[0].messages[1].content
    assert "Omit optional tool arguments" in provider_client.requests[0].messages[1].content
    assert provider_client.requests[1].continuation_items[0]["type"] == "function_call"
    assert provider_client.requests[1].continuation_items[0]["call_id"] == "call-1"
    assert provider_client.requests[1].continuation_items[1]["type"] == "function_call_output"
    assert provider_client.requests[1].continuation_items[1]["call_id"] == "call-1"

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["tool_events"][0]["name"] == "list_agent_memories"
    assert detail["tool_events"][0]["is_error"] is False
    assert detail["tool_events"][0]["created_at"].endswith("+00:00")

    follow_up = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Was that tool call real?"},
    )

    assert follow_up.status_code == 200
    recent_tool_context = next(
        message
        for message in provider_client.requests[-1].messages
        if "Verified recent tool execution facts" in message.content
    )
    assert "list_agent_memories succeeded" in recent_tool_context.content
    assert "Do not claim that a listed execution was fabricated" in (recent_tool_context.content)


@pytest.mark.asyncio
async def test_chat_stream_creates_task_plan_with_valid_schema_and_scoped_metadata(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="create_task_plan",
        tool_arguments=(
            {
                "request": {
                    "objective": "Follow the race",
                    "steps": ["Check the race when it starts"],
                    "parent_task_id": "model-selected-parent",
                    "source": "user",
                    "priority": "normal",
                    "status": "active",
                },
            },
        ),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Race task"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="create_task_plan",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Create a task plan"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert "tool_call_completed" in names
    assert "tool_call_failed" not in names
    assert names[-2:] == ["message_completed", "run_completed"]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    tool_output = orjson.loads(detail["tool_events"][0]["output_text"])
    assert tool_output["plan"]["session_id"] == chat["id"]
    assert tool_output["plan"]["parent_task_id"] is None
    assert tool_output["plan"]["source"] == "agent"


@pytest.mark.asyncio
async def test_chat_stream_schedules_a_future_turn_in_the_current_chat(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    run_at = (utc_now() + timedelta(hours=1)).replace(microsecond=0)
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="create_agent_schedule",
        tool_arguments=(
            {
                "name": "Race start update",
                "instruction": "Check the race status and summarize it in this chat.",
                "kind": "one_shot",
                "run_at": run_at.isoformat(),
                "timezone": "UTC",
            },
        ),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Race updates"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Check the race when it starts and message me here"},
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert "tool_call_completed" in names
    assert "tool_call_failed" not in names
    assert names[-2:] == ["message_completed", "run_completed"]
    assert "create_agent_schedule" in {tool.name for tool in provider_client.requests[0].tools}
    async with sqlite_session_factory() as session:
        schedules = (await session.exec(select(AgentScheduleRecord))).all()
    assert len(schedules) == 1
    schedule = schedules[0]
    assert schedule.user_id == "local-user-123"
    assert schedule.agent_id == chat["agent_id"]
    assert schedule.chat_id == chat["id"]
    assert schedule.run_at == run_at.replace(tzinfo=None)
    assert schedule.payload_json == {
        "text": "Check the race status and summarize it in this chat.",
    }


@pytest.mark.asyncio
async def test_chat_stream_returns_invalid_tool_arguments_for_bounded_repair(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=2,
        tool_name="search_contacts",
        tool_arguments=({}, {"query": "Missing Person"}),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Repair arguments"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Find a contact"},
    )

    assert response.status_code == 200
    assert event_names(response.text) == [
        "run_started",
        "tool_call_failed",
        "tool_call_started",
        "tool_call_completed",
        "message_delta",
        "usage",
        "message_completed",
        "run_completed",
    ]
    assert len(provider_client.requests) == 3
    repair_item = provider_client.requests[1].continuation_items[-1]
    assert repair_item["type"] == "function_call_output"
    assert repair_item["call_id"] == "call-1"
    assert "Tool argument query is required" in str(repair_item["output"])
    assert "not executed" in str(repair_item["output"])

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert [event["is_error"] for event in detail["tool_events"]] == [True, False]
    assert [event["arguments_json"] for event in detail["tool_events"]] == [
        {},
        {"query": "Missing Person"},
    ]
    assert detail["tool_events"][0]["output_text"] == "Tool argument query is required"
    assert detail["runtime_events"][-1]["kind"] == "run_completed"


@pytest.mark.asyncio
async def test_chat_stream_completes_after_one_parallel_mcp_call_fails(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="search_contacts",
        tool_argument_batches=(
            (
                {"query": "successful"},
                {"query": "rate limited"},
            ),
        ),
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    search_provider = PartiallyUnavailableSearchToolProvider()
    service = replace(
        build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            text_provider_client=provider_client,
        ),
        tool_provider=search_provider,
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        chat_service=service,
    )
    try:
        save_text_key(client)
        chat = client.post("/api/chats", json={}).json()
        await SqlModelToolAuthorizationService(
            session_factory=sqlite_session_factory,
        ).set_grants(
            user_id="local-user-123",
            agent_id=chat["agent_id"],
            grants=(
                ToolGrantUpdate(
                    tool_name="search_contacts",
                    tool_source=ToolGrantScope.INTERNAL,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.LOW,
                ),
            ),
        )
        response = client.post(
            f"/api/chats/{chat['id']}/messages/stream",
            json={"text": "Compare two search queries"},
        )
    finally:
        await http_client.aclose()

    assert response.status_code == 200
    names = event_names(response.text)
    assert names.count("tool_call_completed") == 1
    assert names.count("tool_call_failed") == 1
    assert "run_failed" not in names
    assert names[-2:] == ["message_completed", "run_completed"]
    assert sorted(search_provider.executions) == ["rate limited", "successful"]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["messages"][-1]["text"] == "Hello"
    assert detail["runtime_events"][-1]["kind"] == "run_completed"


@pytest.mark.asyncio
async def test_chat_stream_skips_same_tool_after_first_invalid_parallel_call(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="search_contacts",
        tool_argument_batches=(({}, {}, {}),),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Parallel repair"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Find a contact"},
    )

    assert response.status_code == 200
    assert event_names(response.text).count("tool_call_failed") == 1
    assert event_names(response.text).count("tool_call_started") == 0
    repair_items = provider_client.requests[1].continuation_items[-3:]
    assert len(repair_items) == 3
    assert "Tool argument query is required" in str(repair_items[0]["output"])
    assert "same tool already had invalid arguments" in str(repair_items[1]["output"])
    assert "same tool already had invalid arguments" in str(repair_items[2]["output"])
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(detail["tool_events"]) == 1


@pytest.mark.asyncio
async def test_chat_stream_does_not_repeat_exact_rejected_arguments_across_rounds(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=2,
        tool_name="search_contacts",
        tool_arguments=({}, {}),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Repeated repair"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Find a contact"},
    )

    assert response.status_code == 200
    assert event_names(response.text).count("tool_call_failed") == 1
    repeated_output = provider_client.requests[2].continuation_items[-1]["output"]
    assert "exact request was already rejected" in str(repeated_output)
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(detail["tool_events"]) == 1


@pytest.mark.asyncio
async def test_chat_stream_allows_final_response_after_maximum_tool_rounds(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(tool_rounds=MAX_TOOL_ROUNDS)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Tool limit"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Use all available tool rounds"},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-2:] == ["message_completed", "run_completed"]
    assert len(provider_client.requests) == MAX_TOOL_ROUNDS + 1
    assert all(request.tools for request in provider_client.requests[:-1])
    assert provider_client.requests[-1].tools == ()
    # Withdrawing the tools silently invites a textual tool call, so the final request
    # has to tell the model the budget is spent.
    final_system_messages = [
        message.content for message in provider_client.requests[-1].messages if message.role == "system"
    ]
    assert any(content == TOOL_BUDGET_EXHAUSTED_PROMPT for content in final_system_messages)
    assert all(not message.discard_when_tools_unavailable for message in provider_client.requests[-1].messages)
    assert all(
        TOOL_BUDGET_EXHAUSTED_PROMPT not in [message.content for message in request.messages]
        for request in provider_client.requests[:-1]
    )
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(detail["tool_events"]) == MAX_TOOL_ROUNDS
    assert detail["messages"][-1]["text"] == "Hello"


@pytest.mark.asyncio
async def test_chat_stream_repairs_textual_tool_markup_after_tool_budget_exhaustion(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=MAX_TOOL_ROUNDS,
        xml_tool_call_requests=frozenset({MAX_TOOL_ROUNDS + 1}),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Tool protocol repair"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Use all available tool rounds"},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-2:] == ["message_completed", "run_completed"]
    assert len(provider_client.requests) == MAX_TOOL_ROUNDS + 2
    assert all(request.tools == () for request in provider_client.requests[-2:])
    assert TOOL_PROTOCOL_REPAIR_PROMPT in {
        message.content for message in provider_client.requests[-1].messages if message.role == "system"
    }
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["messages"][-1]["text"] == "Hello"


@pytest.mark.asyncio
async def test_chat_stream_does_not_execute_tool_call_beyond_maximum_rounds(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(tool_rounds=MAX_TOOL_ROUNDS + 1)
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Tool cap"}).json()
    await SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    ).set_grants(
        user_id="local-user-123",
        agent_id=chat["agent_id"],
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Try one tool call too many"},
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_failed"
    assert len(provider_client.requests) == MAX_TOOL_ROUNDS + 1
    assert provider_client.requests[-1].tools == ()
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(detail["tool_events"]) == MAX_TOOL_ROUNDS
    assert detail["runtime_events"][-1]["payload_json"]["error_message"] == ("Maximum tool call depth was exceeded")


def test_chat_sends_multiple_images_as_one_ordered_multimodal_turn(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    store = MemoryAttachmentStore()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=store,
    )
    save_text_key(client)
    chat = client.post(
        "/api/chats",
        json={"model": "gpt-5.6-sol", "provider": "openai"},
    ).json()
    first = upload_image(client, chat["id"], "one.jpg", image_bytes("JPEG", "red"))
    second = upload_image(client, chat["id"], "two.png", image_bytes("PNG", "blue"))

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Compare these images",
            "attachment_ids": [second["id"], first["id"]],
            "client_turn_id": "turn-with-two-images",
        },
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    user_message = next(
        message for message in reversed(provider_client.requests[-1].messages) if message.role == "user"
    )
    assert not isinstance(user_message.content, str)
    image_parts = [part for part in user_message.content if isinstance(part, ProviderInputImage)]
    assert len(image_parts) == 2
    assert all(part.image_url.startswith("data:image/") for part in image_parts)

    detail = client.get(f"/api/chats/{chat['id']}").json()
    user_turns = [message for message in detail["messages"] if message["role"] == "user"]
    assert len(user_turns) == 1
    assert [item["id"] for item in user_turns[0]["attachments"]] == [
        second["id"],
        first["id"],
    ]
    thumbnail = client.get(
        f"/api/chats/{chat['id']}/attachments/{first['id']}/content",
        params={"variant": "thumbnail"},
    )
    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"].startswith("image/jpeg")

    retry = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Compare these images",
            "attachment_ids": [second["id"], first["id"]],
            "client_turn_id": "turn-with-two-images",
        },
    )
    assert retry.status_code == 200
    assert len(provider_client.requests) == 1


def test_nvidia_text_default_routes_image_turns_to_multimodal_model(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=MemoryAttachmentStore(),
    )
    credential_response = client.put(
        "/api/text/settings/provider-credential",
        json={
            "provider": "nvidia",
            "api_key": "nvidia-chat-credential",  # pragma: allowlist secret
            "activate": True,
        },
    )
    assert credential_response.status_code == 200
    chat = client.post("/api/chats", json={}).json()
    assert chat["model"] == "openai/gpt-oss-20b"
    assert chat["supports_image_input"] is True
    attachment = upload_image(
        client,
        chat["id"],
        "route.png",
        image_bytes("PNG", "green"),
    )

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Describe this image",
            "attachment_ids": [attachment["id"]],
            "client_turn_id": "nvidia-image-route",
        },
    )

    assert response.status_code == 200
    assert event_names(response.text)[-1] == "run_completed"
    assert provider_client.requests[-1].model == "stepfun-ai/step-3.7-flash"
    assert any(
        isinstance(part, ProviderInputImage)
        for message in provider_client.requests[-1].messages
        if not isinstance(message.content, str)
        for part in message.content
    )


def test_chat_rejects_images_for_unknown_model_and_hides_content_from_other_user(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    store = MemoryAttachmentStore()
    owner = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        attachment_store=store,
        user_id="owner-user",
    )
    save_text_key(owner)
    chat = owner.post(
        "/api/chats",
        json={"model": "custom-text-model", "provider": "openai"},
    ).json()
    attachment = upload_image(
        owner,
        chat["id"],
        "one.png",
        image_bytes("PNG", "green"),
    )
    unsupported = owner.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "",
            "attachment_ids": [attachment["id"]],
            "client_turn_id": "unsupported-turn",
        },
    )
    assert unsupported.status_code == 409

    other = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        attachment_store=store,
        user_id="other-user",
    )
    hidden = other.get(
        f"/api/chats/{chat['id']}/attachments/{attachment['id']}/content",
    )
    assert hidden.status_code == 404


def test_text_only_model_exposes_historical_images_only_to_image_tool(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient()
    store = MemoryAttachmentStore()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=store,
    )
    save_text_key(client)
    chat = client.post(
        "/api/chats",
        json={"model": "gpt-5.6-sol", "provider": "openai"},
    ).json()
    attachment = upload_image(
        client,
        chat["id"],
        "one.png",
        image_bytes("PNG", "green"),
    )
    multimodal = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "",
            "attachment_ids": [attachment["id"]],
            "client_turn_id": "image-only-turn",
        },
    )
    assert multimodal.status_code == 200
    reads_after_multimodal_turn = len(store.get_calls)

    updated = client.patch(
        f"/api/chats/{chat['id']}",
        json={"model": "custom-text-model", "provider": "openai"},
    )
    assert updated.status_code == 200
    text_only = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={"text": "Continue without the image"},
    )

    assert text_only.status_code == 200
    assert len(store.get_calls) == reads_after_multimodal_turn
    assert any(
        "available to the generate_image tool" in message.content
        for message in provider_client.requests[-1].messages
        if isinstance(message.content, str)
    )
    assert all(
        not any(isinstance(part, ProviderInputImage) for part in message.content)
        for message in provider_client.requests[-1].messages
        if not isinstance(message.content, str)
    )


@pytest.mark.asyncio
async def test_chat_image_generation_persists_private_assistant_attachment(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    image_client = FakeImageGenerationProviderClient(
        content=image_bytes("PNG", "purple"),
    )
    store = MemoryAttachmentStore()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        attachment_store=store,
        image_generation_client=image_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Image studio"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/images/generate",
        json={
            "prompt": "A purple observatory above the clouds",
            "attachment_ids": [],
            "client_turn_id": "image-turn-1",
            "client_source": "web",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-image-2"
    assert body["edited"] is False
    assert body["user_message"]["text"] == ("A purple observatory above the clouds")
    attachment = body["assistant_message"]["attachments"][0]
    content = client.get(
        f"/api/chats/{chat['id']}/attachments/{attachment['id']}/content",
        params={"variant": "full"},
    )
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/jpeg"
    assert image_client.requests[0].provider.value == "openai"
    chat_list = client.get("/api/chats").json()
    assert chat_list["image_generation_enabled"] is True
    assert chat_list["image_editing_enabled"] is True


@pytest.mark.asyncio
async def test_chat_image_tool_generates_and_edits_in_normal_conversation(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="generate_image",
        tool_arguments=(
            {
                "action": "generate",
                "prompt": "A newlywed couple on their wedding day in a garden",
            },
        ),
    )
    image_client = FakeImageGenerationProviderClient(
        content=image_bytes("PNG", "purple"),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=MemoryAttachmentStore(),
        image_generation_client=image_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "Shared image context"}).json()

    generated = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Generate an image of a newlywed couple",
            "client_turn_id": "conversation-image-1",
        },
    )

    assert generated.status_code == 200
    assert event_names(generated.text)[-4:] == [
        "message_delta",
        "usage",
        "message_completed",
        "run_completed",
    ]
    completed_tool = next(
        payload for payload in event_payloads(generated.text) if payload["kind"] == "tool_call_completed"
    )
    tool_payload = completed_tool["payload"]
    assert isinstance(tool_payload, dict)
    assert tool_payload["name"] == "generate_image"
    assert len(tool_payload["attachments"]) == 1

    first_detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(first_detail["messages"]) == 2
    assert first_detail["messages"][0]["role"] == "user"
    assert len(first_detail["messages"][1]["attachments"]) == 1
    assert len(image_client.requests) == 1
    assert image_client.requests[0].inputs == ()

    provider_client.requests.clear()
    provider_client.tool_arguments = (
        {
            "action": "edit",
            "prompt": ("The same newlywed couple, now more muscular, on a beach wedding day"),
        },
    )
    edited = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Make them more muscular and put them on a beach",
            "client_turn_id": "conversation-image-2",
        },
    )

    assert edited.status_code == 200
    assert len(image_client.requests) == 2
    assert len(image_client.requests[1].inputs) == 1
    assert image_client.requests[1].inputs[0].content != b""
    assert image_client.requests[1].prompt == ("The same newlywed couple, now more muscular, on a beach wedding day")
    replayed_text = [
        message.content for message in provider_client.requests[0].messages if isinstance(message.content, str)
    ]
    assert "Generate an image of a newlywed couple" in replayed_text
    assert "Make them more muscular and put them on a beach" in replayed_text
    final_detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(final_detail["messages"]) == 4
    assert len(final_detail["messages"][-1]["attachments"]) == 1

    current_reference = upload_image(
        client,
        chat["id"],
        "current-reference.jpg",
        image_bytes("JPEG", "green"),
    )
    provider_client.requests.clear()
    provider_client.tool_arguments = (
        {
            "action": "edit",
            "prompt": "Keep the current uploaded subject and add a golden sky",
        },
    )
    current_edit = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Make this image's sky golden",
            "attachment_ids": [current_reference["id"]],
            "client_turn_id": "conversation-image-3",
        },
    )

    assert current_edit.status_code == 200
    assert len(image_client.requests) == 3
    current_input = Image.open(BytesIO(image_client.requests[2].inputs[0].content)).convert("RGB")
    red, green, blue = current_input.getpixel((40, 30))
    assert green > red
    assert green > blue
    current_detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(current_detail["messages"]) == 6


@pytest.mark.asyncio
async def test_chat_recovers_when_image_retry_uses_malformed_tool_name(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = CapturedFilteredImageRetryTextProvider()
    image_client = ContentFilteredImageGenerationProviderClient()
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=MemoryAttachmentStore(),
        image_generation_client=image_client,
    )
    save_text_key(client)
    chat = client.post(
        "/api/chats",
        json={"title": "Filtered image retry"},
    ).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Can you try again now?",
            "client_turn_id": "filtered-image-retry",
        },
    )

    assert response.status_code == 200
    names = event_names(response.text)
    assert names.count("tool_call_failed") == 2
    assert "run_failed" not in names
    assert names[-3:] == [
        "message_delta",
        "message_completed",
        "run_completed",
    ]
    failures = [payload for payload in event_payloads(response.text) if payload["kind"] == "tool_call_failed"]
    assert "content filter rejected" in failures[0]["payload"]["error_message"]
    assert failures[1]["payload"]["error_type"] == "ToolNotExposedError"
    assert len(image_client.requests) == 1

    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert detail["messages"][-1]["role"] == "assistant"
    assert detail["messages"][-1]["text"] == (
        "The image provider filtered the prompt, so I could not generate the image."
    )


@pytest.mark.asyncio
async def test_chat_image_edit_without_reference_fails_without_generating(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider_client = FakeTextProviderClient(
        tool_rounds=1,
        tool_name="generate_image",
        tool_arguments=(
            {
                "action": "edit",
                "prompt": "Make the missing source brighter",
            },
        ),
    )
    image_client = FakeImageGenerationProviderClient(
        content=image_bytes("PNG", "purple"),
    )
    client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=provider_client,
        attachment_store=MemoryAttachmentStore(),
        image_generation_client=image_client,
    )
    save_text_key(client)
    chat = client.post("/api/chats", json={"title": "No edit source"}).json()

    response = client.post(
        f"/api/chats/{chat['id']}/messages/stream",
        json={
            "text": "Make it brighter",
            "client_turn_id": "missing-edit-source",
        },
    )

    assert response.status_code == 200
    assert image_client.requests == []
    failed_tool = next(payload for payload in event_payloads(response.text) if payload["kind"] == "tool_call_failed")
    assert "no current or previously generated image" in failed_tool["payload"]["error_message"]
    detail = client.get(f"/api/chats/{chat['id']}").json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][-1]["attachments"] == []


@pytest.mark.asyncio
async def test_chat_delete_is_owner_scoped_and_hides_chat(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    owner_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        user_id="owner-user",
    )
    save_text_key(owner_client)
    chat = owner_client.post("/api/chats", json={"title": "Delete me"}).json()

    other_client = create_test_client(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider_client=FakeTextProviderClient(),
        user_id="other-user",
    )
    other_get = other_client.get(f"/api/chats/{chat['id']}")
    other_delete = other_client.delete(f"/api/chats/{chat['id']}")

    assert other_get.status_code == 404
    assert other_delete.status_code == 404

    delete_response = owner_client.delete(f"/api/chats/{chat['id']}")
    list_response = owner_client.get("/api/chats")
    get_response = owner_client.get(f"/api/chats/{chat['id']}")

    assert delete_response.status_code == 204
    assert list_response.json()["chats"] == []
    assert get_response.status_code == 404


def upload_image(
    client: TestClient,
    chat_id: str,
    filename: str,
    content: bytes,
) -> dict[str, object]:
    response = client.post(
        f"/api/chats/{chat_id}/attachments",
        data={"client_draft_id": "draft-1", "client_source": "web"},
        files={"file": (filename, content, "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    assert isinstance(body, dict)
    return body


def image_bytes(format_name: str, color: str) -> bytes:
    image = Image.new("RGB", (80, 60), color=color)
    buffer = BytesIO()
    image.save(buffer, format=format_name)
    return buffer.getvalue()

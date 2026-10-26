from datetime import UTC, datetime

import orjson
import pytest
from sqlmodel import select

from voxos.agent_core.internal_tools import (
    ContactSearchMatch,
    ContactSearchResolution,
    InternalAgentToolProvider,
    contact_search_resolution,
    internal_agent_tool,
)
from voxos.agent_core.mcp_tools import UserMcpAgentToolProvider
from voxos.agent_core.models import AgentRunContext
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.records import AgentToolAuditEventRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import (
    READ_ONLY_INTERNAL_TOOL_NAMES,
    SqlModelToolAuthorizationService,
)
from voxos.agent_runtime.tool_execution import (
    ToolArgumentSchemaError,
    ToolAuthorityArgumentError,
    prune_unsupported_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.agent_skills.services import AgentSkillService
from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    McpToolCallResult,
)
from voxos.mcp.models import McpTransportType
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import UserMcpToolsetConfig
from voxos.product_docs.models import (
    ProductDocPageResult,
    ProductDocSearchMatch,
    ProductDocSearchRequest,
    ProductDocSearchResult,
)


class StaticProductDocSearchService:
    async def search_docs(self, request: ProductDocSearchRequest) -> ProductDocSearchResult:
        return ProductDocSearchResult(
            matches=(
                ProductDocSearchMatch(
                    chunk_id="chunk-1",
                    title="Voice And LiveKit",
                    source_path="Voice-And-LiveKit.md",
                    heading_path=("Voice And LiveKit", "LiveKit Settings"),
                    heading_slug="livekit-settings",
                    excerpt=f"Matched {request.query}",
                    score=0.9,
                    vector_score=0.8,
                    text_score=1.0,
                ),
            ),
        )

    async def read_page(self, source_path: str) -> ProductDocPageResult:
        _ = source_path
        return ProductDocPageResult(page=None, message="not used")


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC)


def test_only_read_only_internal_tools_are_parallel_safe() -> None:
    assert internal_agent_tool("search_contacts").parallel_safe is True
    assert internal_agent_tool("get_current_datetime").parallel_safe is True
    assert internal_agent_tool("create_task_plan").parallel_safe is False


def test_search_voxos_docs_has_a_flat_query_schema() -> None:
    tool = internal_agent_tool("search_voxos_docs")

    assert tool.parameters_schema["required"] == ["query"]
    assert set(tool.parameters_schema["properties"]) == {
        "query",
        "limit",
        "source_path",
    }
    assert "request" not in tool.parameters_schema["properties"]
    assert "external tools" in (tool.description or "")

    with pytest.raises(ToolArgumentSchemaError, match="unsupported fields"):
        validate_tool_arguments_schema(
            {"request": "weather forecast London UK tomorrow"},
            tool.parameters_schema,
        )


def test_create_task_plan_schema_accepts_explicit_enum_arguments() -> None:
    tool = internal_agent_tool("create_task_plan")
    arguments = prune_unsupported_tool_arguments(
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
        tool.parameters_schema,
    )

    validate_tool_arguments_schema(
        arguments,
        tool.parameters_schema,
    )

    request = arguments["request"]
    assert isinstance(request, dict)
    assert "parent_task_id" not in request
    assert "source" not in request
    assert '"$ref"' not in orjson.dumps(tool.parameters_schema).decode()
    assert '"$defs"' not in orjson.dumps(tool.parameters_schema).decode()


class FakeMcpClient:
    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, str]] = []

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        return DiscoveredMcpCapabilities(
            tools=("search",),
            tool_definitions=(
                DiscoveredMcpTool(
                    name="search",
                    description="Search the connected system.",
                    input_schema={"type": "object", "properties": {}},
                ),
            ),
        )

    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: dict[str, object],
    ) -> McpToolCallResult:
        self.tool_calls.append((config.url, tool_name))
        return McpToolCallResult(content_text="ok")


class StaticUserMcpAgentToolProvider(UserMcpAgentToolProvider):
    _configs: tuple[UserMcpToolsetConfig, ...]

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
        mcp_client: FakeMcpClient,
        configs: tuple[UserMcpToolsetConfig, ...],
        authorization_service: SqlModelToolAuthorizationService | None = None,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            secret_vault=secret_vault,
            mcp_client=mcp_client,
            authorization_service=authorization_service,
        )
        object.__setattr__(self, "_configs", configs)

    async def _toolset_configs(
        self,
        user_id: str,
    ) -> tuple[UserMcpToolsetConfig, ...]:
        return self._configs


@pytest.mark.asyncio
async def test_internal_agent_tool_provider_uses_agent_grants(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )

    default_tools = await provider.list_tools(context)
    available_default_tool_names = set(READ_ONLY_INTERNAL_TOOL_NAMES) - {
        "search_contacts",
        "search_agent_history",
        "read_agent_history",
    }

    assert {tool.name for tool in default_tools} == available_default_tool_names

    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="create_agent_memory",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )

    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Run with tools"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    scoped_context = context.model_copy(
        update={
            "run_id": run.id,
            "root_run_id": run.root_run_id,
            "effective_tool_policy_id": policy.id,
        },
    )
    tools = await provider.list_tools(scoped_context)
    result = await provider.execute_tool(
        context=scoped_context,
        tool_name="list_agent_memories",
        call_id="call-123",
        arguments_json={},
    )

    assert {tool.name for tool in tools} == {
        *available_default_tool_names,
        "create_agent_memory",
    }
    assert result.call_id == "call-123"
    assert result.name == "list_agent_memories"
    assert result.is_error is False
    assert '"memories":[]' in result.output_text
    async with sqlite_session_factory() as session:
        audit_result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = audit_result.all()
    assert audit_events[-1].run_id == run.id


def test_contact_search_resolution_distinguishes_match_cardinality() -> None:
    match = ContactSearchMatch(
        contact_id="contact-123",
        display_name="Ada Lovelace",
        phone_number="+15551234567",
    )

    assert contact_search_resolution(()) == ContactSearchResolution.NOT_FOUND
    assert contact_search_resolution((match,)) == ContactSearchResolution.UNIQUE
    assert contact_search_resolution((match, match.model_copy())) == ContactSearchResolution.AMBIGUOUS


@pytest.mark.asyncio
async def test_default_contact_search_returns_only_tenant_phone_numbers(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        owner_contact = await UserContactService(session).create_contact(
            user_id="user-123",
            request=ContactCreateRequest(
                display_name="Ada Lovelace",
                phone_number="+15551234567",
                email="ada@example.com",
                notes="Private note",
                is_admin=True,
            ),
        )
        await UserContactService(session).create_contact(
            user_id="other-user",
            request=ContactCreateRequest(
                display_name="Ada Other",
                phone_number="+15559876543",
            ),
        )

    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        contact_session_factory=sqlite_session_factory,
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-contacts",
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )

    assert "search_contacts" in {tool.name for tool in await provider.list_tools(context)}
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-contacts",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Find Ada"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    scoped_context = context.model_copy(
        update={
            "run_id": run.id,
            "root_run_id": run.root_run_id,
            "effective_tool_policy_id": policy.id,
        },
    )

    tools = await provider.list_tools(scoped_context)
    result = await provider.execute_tool(
        context=scoped_context,
        tool_name="search_contacts",
        call_id="call-contacts",
        arguments_json={"query": "Ada", "limit": 10},
    )

    assert "search_contacts" in {tool.name for tool in tools}
    assert result.raw_output == {
        "resolution": "unique",
        "matches": [
            {
                "contact_id": owner_contact.id,
                "display_name": "Ada Lovelace",
                "phone_number": "+15551234567",
            },
        ],
    }
    assert "+15551234567" in result.output_text
    assert "ada@example.com" not in result.output_text
    assert "Private note" not in result.output_text


@pytest.mark.asyncio
async def test_internal_agent_tool_provider_executes_default_docs_search(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Search docs"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        product_docs_service=StaticProductDocSearchService(),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )

    tools = await provider.list_tools(context)
    result = await provider.execute_tool(
        context=context,
        tool_name="search_voxos_docs",
        call_id="call-123",
        arguments_json={"query": "LiveKit settings", "limit": 3},
    )

    assert "search_voxos_docs" in {tool.name for tool in tools}
    assert result.call_id == "call-123"
    assert result.is_error is False
    assert "Voice-And-LiveKit.md" in result.output_text
    async with sqlite_session_factory() as session:
        audit_result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = audit_result.all()
    assert audit_events[-1].tool_name == "search_voxos_docs"
    assert audit_events[-1].run_id == run.id


@pytest.mark.asyncio
async def test_internal_agent_tool_provider_executes_default_current_datetime(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "What time is it?"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
        clock=FakeClock(),
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )

    result = await provider.execute_tool(
        context=context,
        tool_name="get_current_datetime",
        call_id="call-123",
        arguments_json={},
    )

    assert result.call_id == "call-123"
    assert result.name == "get_current_datetime"
    assert result.raw_output == {
        "current_datetime_utc": "2026-07-09T12:34:56+00:00",
        "timezone": "UTC",
    }
    assert result.is_error is False
    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name="get_current_datetime",
            call_id="call-456",
            arguments_json={"user_id": "other-user"},
        )
    async with sqlite_session_factory() as session:
        audit_result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = audit_result.all()
    assert audit_events[-2].tool_name == "get_current_datetime"
    assert audit_events[-2].decision == "allowed"
    assert audit_events[-1].tool_name == "get_current_datetime"
    assert audit_events[-1].decision == "denied"


@pytest.mark.asyncio
async def test_user_mcp_agent_tool_provider_disambiguates_duplicate_tool_names(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    mcp_client = FakeMcpClient()
    provider = StaticUserMcpAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        configs=(
            UserMcpToolsetConfig(
                id="toolset-a",
                server_id="server-a",
                label="Search A",
                url="https://a.example/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
            ),
            UserMcpToolsetConfig(
                id="toolset-b",
                server_id="server-b",
                label="Search B",
                url="https://b.example/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
            ),
        ),
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )

    tools = await provider.list_tools(context)
    tool_names = [tool.name for tool in tools]
    assert len(tool_names) == 2
    assert len(set(tool_names)) == 2
    assert all(tool_name.startswith("mcp_") for tool_name in tool_names)
    assert "search from Search A MCP server" in tools[0].description
    assert "search from Search B MCP server" in tools[1].description

    result = await provider.execute_tool(
        context=context,
        tool_name=tool_names[1],
        call_id="call-123",
        arguments_json={},
    )

    assert result.name == tool_names[1]
    assert result.output_text == "ok"
    assert mcp_client.tool_calls == [("https://b.example/mcp", "search")]


@pytest.mark.asyncio
async def test_user_mcp_agent_tool_provider_preserves_run_id_in_audit(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        server = UserMcpServerRecord(
            user_id="user-123",
            label="Search",
            transport=McpTransportType.STREAMABLE_HTTP.value,
            url="https://search.example/mcp",
            auth_type="none",
            allowed_tools=["search"],
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)

    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=server.id,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Run with MCP tools"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    mcp_client = FakeMcpClient()
    provider = StaticUserMcpAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=authorization_service,
        configs=(
            UserMcpToolsetConfig(
                id=f"user-mcp-{server.id}",
                server_id=server.id,
                label=server.label,
                url=server.url,
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
            ),
        ),
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )
    tools = await provider.list_tools(context)

    result = await provider.execute_tool(
        context=context,
        tool_name=tools[0].name,
        call_id="call-123",
        arguments_json={},
    )

    assert result.output_text == "ok"
    async with sqlite_session_factory() as session:
        audit_result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = audit_result.all()
    assert audit_events[-1].tool_name == "search"
    assert audit_events[-1].mcp_server_id == server.id
    assert audit_events[-1].run_id == run.id

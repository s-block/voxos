from dataclasses import dataclass, field

import httpx
import pytest
from livekit.agents.llm.mcp import MCPToolset

from voxos.agent_core.models import AgentTool
from voxos.agent_core.tool_catalog import TOOL_SEARCH_NAME
from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.tool_authorization import (
    READ_ONLY_INTERNAL_TOOL_NAMES,
    SqlModelToolAuthorizationService,
)
from voxos.agent_runtime.tool_execution import ToolAuthorityArgumentError
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolReference,
    ToolRisk,
)
from voxos.agent_runtime.tool_routing import ResolvedToolRoute, ToolRouteMode
from voxos.agent_skills.services import AgentSkillService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    McpToolCallResult,
)
from voxos.mcp.defaults import InitialMcpServerProvisioner
from voxos.mcp.models import (
    McpAuthType,
    McpServerCreateRequest,
    McpStaticAuthRequest,
    McpTransportType,
)
from voxos.mcp.services import UserMcpServerService, UserMcpToolsetConfig
from voxos.persona.mcp import PERSONA_MCP_TOOL_NAMES
from voxos.voice.mcp_tools import (
    INTERNAL_AGENT_MCP_TOOL_NAMES,
    InternalAgentVoiceToolProvider,
    McpVoiceToolProvider,
    UserMcpVoiceToolProvider,
    realtime_tool_description,
    realtime_tool_parameters_schema,
    route_voice_mcp_tools,
)

GITHUB_TOKEN = "github-token"


def test_realtime_tool_search_description_includes_deferred_capability_directory() -> None:
    tool = AgentTool(
        name=TOOL_SEARCH_NAME,
        description="Find authorized capabilities available in this run.",
        source=ToolGrantScope.INTERNAL,
    )
    directory = (
        "Discoverable specialist capabilities (already authorized; schemas are deferred):\n"
        "- Google Maps Grounding Lite (5 tools): search_places, lookup_weather"
    )

    description = realtime_tool_description(
        tool=tool,
        capability_directory=directory,
    )

    assert directory in description
    assert "search_places" in description


def test_realtime_tool_description_does_not_expand_non_search_tools() -> None:
    tool = AgentTool(
        name="web_search",
        description="Search the web.",
        source=ToolGrantScope.MCP_SERVER,
    )

    description = realtime_tool_description(
        tool=tool,
        capability_directory="Discoverable specialist capabilities: maps",
    )

    assert description == tool.description


def test_realtime_tool_parameters_schema_removes_unsupported_root_constraints() -> None:
    source_schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "checkin": {"type": "string"},
        },
        "required": ["location"],
        "additionalProperties": False,
        "oneOf": [
            {"required": ["checkin"]},
            {"not": {"required": ["checkin"]}},
        ],
    }

    provider_schema = realtime_tool_parameters_schema(source_schema)

    assert provider_schema == {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "location": {"type": "string"},
            "checkin": {"type": "string"},
        },
        "required": ["location"],
        "additionalProperties": False,
    }
    assert "oneOf" in source_schema


def test_voice_mcp_routing_keeps_exa_search_and_browserless_browser_tools() -> None:
    exa = voice_config(
        server_id="exa-server",
        preset_id="exa",
        label="Exa",
        tools=("web_search_exa", "web_fetch_exa"),
    )
    browserless = voice_config(
        server_id="browserless-server",
        preset_id="browserless",
        label="Browserless",
        tools=("browserless_search", "browserless_smartscraper"),
    )
    policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        resolved_routes=(
            ResolvedToolRoute(
                action="web.search",
                mode=ToolRouteMode.AUTOMATIC,
                selected_tool=ToolReference(
                    tool_name="web_search_exa",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id="exa-server",
                    risk=ToolRisk.LOW,
                ),
                automatic_priority=10,
            ),
        ),
    )

    routed = route_voice_mcp_tools(
        configs=(exa, browserless),
        allowed_by_server_id={
            exa.server_id: exa.allowed_tools,
            browserless.server_id: browserless.allowed_tools,
        },
        effective_tool_policy=policy,
    )

    assert routed[exa.server_id] == ("web_search_exa", "web_fetch_exa")
    assert routed[browserless.server_id] == ("browserless_smartscraper",)


def test_voice_mcp_routing_keeps_all_authorized_place_tools() -> None:
    google_maps = voice_config(
        server_id="google-maps-server",
        preset_id="google-maps-grounding-lite",
        label="Google Maps",
        tools=("search_places", "lookup_weather"),
    )
    openbnb = voice_config(
        server_id="openbnb-server",
        preset_id="openbnb",
        label="OpenBnB",
        tools=("maps_search_places", "maps_place_details"),
    )
    policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
    )

    routed = route_voice_mcp_tools(
        configs=(google_maps, openbnb),
        allowed_by_server_id={
            google_maps.server_id: google_maps.allowed_tools,
            openbnb.server_id: openbnb.allowed_tools,
        },
        effective_tool_policy=policy,
    )

    assert routed[google_maps.server_id] == ("search_places", "lookup_weather")
    assert routed[openbnb.server_id] == (
        "maps_search_places",
        "maps_place_details",
    )


@dataclass(slots=True)
class StaticBearerTokenResolver:
    bearer_value: str
    seen_user_id: str | None = None

    async def bearer_token_for_user(self, user_id: str) -> str:
        self.seen_user_id = user_id
        return self.bearer_value


def voice_config(
    *,
    server_id: str,
    preset_id: str,
    label: str,
    tools: tuple[str, ...],
) -> UserMcpToolsetConfig:
    return UserMcpToolsetConfig(
        id=f"config-{server_id}",
        server_id=server_id,
        label=label,
        url=f"https://{server_id}.example/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=tools,
        preset_id=preset_id,
    )


@dataclass(slots=True)
class FakeMcpClient:
    discovery_calls: int = 0
    tool_calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        del config
        self.discovery_calls += 1
        return DiscoveredMcpCapabilities(
            tools=("issues.list",),
            tool_definitions=(
                DiscoveredMcpTool(
                    name="issues.list",
                    description="List repository issues",
                    input_schema={"type": "object", "properties": {}},
                    read_only_hint=True,
                ),
            ),
        )

    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: dict[str, object],
    ) -> McpToolCallResult:
        del config
        self.tool_calls.append((tool_name, arguments))
        return McpToolCallResult(content_text="one issue")


@dataclass(slots=True)
class FakeOAuthClient:
    pass


@pytest.mark.asyncio
async def test_mcp_voice_tool_provider_returns_persona_toolset() -> None:
    resolver = StaticBearerTokenResolver(bearer_value="user-token")
    provider = McpVoiceToolProvider(
        mcp_url="https://mcp.example.com/mcp",
        token_resolver=resolver,
    )

    tools = await provider.get_tools("user-123")

    assert resolver.seen_user_id == "user-123"
    assert len(tools) == 1
    assert isinstance(tools[0], MCPToolset)
    assert tools[0].id == "voxos-persona-mcp"
    assert provider.allowed_tools == PERSONA_MCP_TOOL_NAMES


@pytest.mark.asyncio
async def test_mcp_voice_tool_provider_rejects_empty_bearer_token() -> None:
    provider = McpVoiceToolProvider(
        mcp_url="https://mcp.example.com/mcp",
        token_resolver=StaticBearerTokenResolver(bearer_value=" "),
    )

    with pytest.raises(ValueError, match="empty token"):
        await provider.get_tools("user-123")


@pytest.mark.asyncio
async def test_user_mcp_voice_tool_provider_returns_enabled_toolsets(
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
        await service.store_static_auth(
            user_id="user-123",
            server_id=created.id,
            request=McpStaticAuthRequest(secret=GITHUB_TOKEN),
        )
        await service.test_server(user_id="user-123", server_id=created.id)

    provider = UserMcpVoiceToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(empty_response)),
        mcp_client=FakeMcpClient(),
    )

    tools = await provider.get_tools("user-123")

    assert len(tools) == 1
    assert isinstance(tools[0], MCPToolset)
    assert tools[0].id == f"user-mcp-{created.id}"


@pytest.mark.asyncio
async def test_user_mcp_voice_tool_provider_requires_agent_tool_grant(
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
        await service.store_static_auth(
            user_id="user-123",
            server_id=created.id,
            request=McpStaticAuthRequest(secret=GITHUB_TOKEN),
        )
        await service.test_server(user_id="user-123", server_id=created.id)

    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    mcp_client = FakeMcpClient()
    provider = UserMcpVoiceToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        authorization_service=authorization_service,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(empty_response)),
        mcp_client=mcp_client,
    )

    assert await provider.get_tools_for_scope(scope) == ()

    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="issues.list",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=created.id,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )

    tools = await provider.get_tools_for_scope(scope)

    assert len(tools) == 1
    assert not isinstance(tools[0], MCPToolset)
    assert mcp_client.discovery_calls == 0
    assert mcp_client.tool_calls == []

    with pytest.raises(ToolAuthorityArgumentError):
        await tools[0](raw_arguments={"user_id": "attacker"})
    assert mcp_client.tool_calls == []

    result = await tools[0](raw_arguments={})

    assert result["content"] == "one issue"
    assert mcp_client.tool_calls == [("issues.list", {})]

    catalog_tools = await provider.get_catalog_tools_for_scope(scope)
    assert len(catalog_tools) == 1
    assert catalog_tools[0].definition.source_tool_name == "issues.list"
    assert catalog_tools[0].function_tool.info.name == tools[0].info.name


@pytest.mark.asyncio
async def test_user_mcp_voice_tool_provider_includes_platform_default_exa(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    await InitialMcpServerProvisioner(sqlite_session_factory).initialize_user(
        "user-123",
    )
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    provider = UserMcpVoiceToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        authorization_service=SqlModelToolAuthorizationService(
            session_factory=sqlite_session_factory,
        ),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(empty_response),
        ),
        mcp_client=FakeMcpClient(),
    )

    tools = await provider.get_tools_for_scope(scope)

    assert len(tools) == 2
    assert not any(isinstance(tool, MCPToolset) for tool in tools)
    assert {tuple(tool.info.name.rsplit("_", maxsplit=3)[-3:]) for tool in tools} == {
        ("web", "fetch", "exa"),
        ("web", "search", "exa"),
    }


def empty_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code=404, request=request)


@pytest.mark.asyncio
async def test_internal_agent_voice_tool_provider_returns_livekit_tools(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    provider = InternalAgentVoiceToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        contact_session_factory=sqlite_session_factory,
    )

    tools = await provider.get_tools_for_scope(
        AgentScope(
            owner_user_id="user-123",
            agent_id=agent.id,
            session_id="session-123",
            channel="test",
            source="test",
        ),
    )

    assert {tool.info.name for tool in tools} == set(INTERNAL_AGENT_MCP_TOOL_NAMES) - {
        "search_agent_history",
        "read_agent_history",
        "record_agent_improvement",
        "confirm_agent_memory",
        "confirm_skill_proposal",
    }
    create_memory_tool = next(
        tool for tool in tools if getattr(getattr(tool, "info", None), "name", None) == "create_agent_memory"
    )
    raw_schema = create_memory_tool.info.raw_schema
    request_schema = raw_schema["parameters"]["properties"]["request"]
    assert "evidence_session_id" not in request_schema["properties"]
    assert "evidence_transcript_id" not in request_schema["properties"]
    # A provider without the canonical learning service cannot mutate knowledge.
    with pytest.raises(PermissionError):
        await create_memory_tool(raw_arguments={"request": {"kind": "fact", "text": "The user prefers short answers."}})

    with pytest.raises(ToolAuthorityArgumentError):
        await create_memory_tool(
            raw_arguments={
                "request": {
                    "kind": "fact",
                    "text": "The user prefers short answers.",
                    "evidence_session_id": "other-session",
                },
            },
        )
    assert await provider.get_tools("user-123") == ()


@pytest.mark.asyncio
async def test_internal_agent_voice_tool_provider_filters_by_agent_grants(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    provider = InternalAgentVoiceToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
    )

    default_tools = await provider.get_tools_for_scope(scope)
    assert {getattr(tool.info, "name", None) for tool in default_tools} == set(READ_ONLY_INTERNAL_TOOL_NAMES) - {
        "search_contacts",
        "search_agent_history",
        "read_agent_history",
    }

    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    tools = await provider.get_tools_for_scope(scope)

    assert {getattr(tool.info, "name", None) for tool in tools} == set(READ_ONLY_INTERNAL_TOOL_NAMES) - {
        "search_contacts",
        "search_agent_history",
        "read_agent_history",
    }

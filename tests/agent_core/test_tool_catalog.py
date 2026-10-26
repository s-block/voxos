from dataclasses import dataclass, field
from datetime import UTC, datetime

import orjson
import pytest

from voxos.agent_core.mcp_tools import (
    CompositeAgentToolProvider,
    prepare_agent_tool_provider,
)
from voxos.agent_core.models import (
    AgentRunContext,
    AgentTool,
    AgentToolResult,
)
from voxos.agent_core.tool_catalog import (
    TOOL_CALL_NAME,
    TOOL_DESCRIBE_NAME,
    TOOL_SEARCH_NAME,
    CatalogTool,
    RunScopedToolCatalogProvider,
    ToolNotExposedError,
    catalog_tool_id,
    search_catalog,
)
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_execution import ToolArgumentSchemaError
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolReference, ToolRisk
from voxos.agent_runtime.tool_routing import ResolvedToolRoute, ToolRouteMode


@dataclass(slots=True)
class RecordingToolProvider:
    tools: tuple[AgentTool, ...]
    calls: list[tuple[str, JsonObject]] = field(default_factory=list)
    list_calls: int = 0

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        self.list_calls += 1
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
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text="target result",
        )


@pytest.mark.asyncio
async def test_catalog_classifies_model_selected_unexposed_tool() -> None:
    provider = RecordingToolProvider(
        (
            AgentTool(
                name="generate_image",
                description="Generate an image",
                source=ToolGrantScope.INTERNAL,
            ),
        ),
    )
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )

    with pytest.raises(
        ToolNotExposedError,
        match=r"generate_image<\|channel\|>commentary",
    ):
        await catalog.execute_tool(
            context=context,
            tool_name="generate_image<|channel|>commentary",
            call_id="call-retry",
            arguments_json={},
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_catalog_filters_equivalent_search_tools_before_model_exposure() -> None:
    exa = search_tool(
        name="mcp_exa_search",
        source_name="web_search_exa",
        server_id="exa-server",
        priority=10,
    )
    browserless = search_tool(
        name="mcp_browserless_search",
        source_name="browserless_search",
        server_id="browserless-server",
        priority=100,
    )
    browserless_scraper = AgentTool(
        name="mcp_browserless_scraper",
        source_tool_name="browserless_smartscraper",
        description="Render and scrape a web page",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="browserless-server",
    )
    provider = RecordingToolProvider((browserless, exa, browserless_scraper))
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

    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=run_context(policy),
    )

    assert [tool.name for tool in catalog.visible_tools] == [
        "mcp_exa_search",
        "mcp_browserless_scraper",
    ]


@pytest.mark.asyncio
async def test_catalog_keeps_a_selected_alternative_web_search_tool_visible() -> None:
    exa = search_tool(
        name="mcp_exa_search",
        source_name="web_search_exa",
        server_id="exa-server",
        priority=10,
    )
    browserless = search_tool(
        name="mcp_browserless_search",
        source_name="browserless_search",
        server_id="browserless-server",
        priority=100,
    )
    policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        resolved_routes=(
            ResolvedToolRoute(
                action="web.search",
                mode=ToolRouteMode.PINNED,
                selected_tool=ToolReference(
                    tool_name="browserless_search",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id="browserless-server",
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    catalog = await RunScopedToolCatalogProvider.create(
        provider=RecordingToolProvider((exa, browserless)),
        context=run_context(policy),
    )

    assert [tool.name for tool in catalog.visible_tools] == [
        "mcp_browserless_search",
    ]


@pytest.mark.asyncio
async def test_large_catalog_is_searched_described_and_called_by_opaque_id() -> None:
    tools = tuple(
        AgentTool(
            name=f"mcp_tool_{index}",
            source_tool_name=f"provider_tool_{index}",
            description=("Search current weather forecasts" if index == 7 else f"Capability {index}"),
            parameters_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )
    provider = RecordingToolProvider(tools)
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )

    assert catalog.search_enabled is True
    assert {tool.name for tool in catalog.visible_tools} == {
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    }
    search_result = await catalog.execute_tool(
        context=context,
        tool_name=TOOL_SEARCH_NAME,
        call_id="search-call",
        arguments_json={"query": "weather forecast", "limit": 3},
    )
    search_payload = orjson.loads(search_result.output_text)
    tool_id = search_payload["results"][0]["tool_id"]

    describe_result = await catalog.execute_tool(
        context=context,
        tool_name=TOOL_DESCRIBE_NAME,
        call_id="describe-call",
        arguments_json={"tool_id": tool_id},
    )
    assert orjson.loads(describe_result.output_text)["name"] == "mcp_tool_7"

    with pytest.raises(ToolArgumentSchemaError):
        await catalog.execute_tool(
            context=context,
            tool_name=TOOL_CALL_NAME,
            call_id="invalid-target-call",
            arguments_json={"tool_id": tool_id},
        )
    assert provider.calls == []

    call_result = await catalog.execute_tool(
        context=context,
        tool_name=TOOL_CALL_NAME,
        call_id="target-call",
        arguments_json={
            "tool_id": tool_id,
            "arguments": {"location": "London"},
        },
    )

    assert call_result.name == "mcp_tool_7"
    assert call_result.executed_arguments_json == {"location": "London"}
    assert provider.calls == [("mcp_tool_7", {"location": "London"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_tool_name",
    [
        "provider_tool_7",
        "provider_tool_7<|channel|>commentary",
        "mcp_tool_7",
        "mcp_tool_7<|channel|>commentary",
    ],
)
async def test_large_catalog_executes_unique_authorized_deferred_tool_alias(
    model_tool_name: str,
) -> None:
    tools = tuple(
        AgentTool(
            name=f"mcp_tool_{index}",
            source_tool_name=f"provider_tool_{index}",
            description=f"Capability {index}",
            parameters_schema={
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )
    provider = RecordingToolProvider(tools)
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )

    result = await catalog.execute_tool(
        context=context,
        tool_name=model_tool_name,
        call_id="direct-model-call",
        arguments_json={"location": "London"},
    )

    assert result.name == "mcp_tool_7"
    assert result.executed_arguments_json == {"location": "London"}
    assert provider.calls == [("mcp_tool_7", {"location": "London"})]


@pytest.mark.asyncio
async def test_large_catalog_rejects_ambiguous_deferred_source_tool_alias() -> None:
    tools = tuple(
        AgentTool(
            name=f"mcp_tool_{index}",
            source_tool_name="search" if index < 2 else f"provider_tool_{index}",
            description=f"Capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )
    provider = RecordingToolProvider(tools)
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )

    with pytest.raises(ToolNotExposedError, match="search"):
        await catalog.execute_tool(
            context=context,
            tool_name="search",
            call_id="ambiguous-model-call",
            arguments_json={},
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_catalog_call_defaults_omitted_arguments_for_zero_argument_tool() -> None:
    tools = tuple(
        AgentTool(
            name=f"mcp_tool_{index}",
            description=f"Capability {index}",
            parameters_schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )
    provider = RecordingToolProvider(tools)
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )
    tool_id = catalog.catalog_tools[0].id

    result = await catalog.execute_tool(
        context=context,
        tool_name=TOOL_CALL_NAME,
        call_id="zero-argument-target-call",
        arguments_json={"tool_id": tool_id},
    )

    assert result.executed_arguments_json == {}
    assert provider.calls == [("mcp_tool_0", {})]


@pytest.mark.asyncio
async def test_large_catalog_defers_the_long_tail_but_keeps_the_routed_winner_callable() -> None:
    exa = search_tool(
        name="mcp_exa_search",
        source_name="web_search_exa",
        server_id="exa-server",
        priority=10,
    )
    browserless = search_tool(
        name="mcp_browserless_search",
        source_name="browserless_search",
        server_id="browserless-server",
        priority=100,
    )
    browserless_scraper = AgentTool(
        name="mcp_browserless_scraper",
        source_tool_name="browserless_smartscraper",
        description="Render and scrape a web page",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="browserless-server",
    )
    long_tail = tuple(
        AgentTool(
            name=f"mcp_specialized_{index}",
            description=f"Specialized capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(9)
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
    provider = RecordingToolProvider(
        (browserless, exa, browserless_scraper, *long_tail),
    )
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=run_context(policy),
    )

    assert catalog.search_enabled is True
    # The resolved web.search winner stays directly callable; routing already reduced
    # the action to one tool, so deferring it would only add discovery round trips.
    assert [tool.name for tool in catalog.visible_tools] == [
        "mcp_exa_search",
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    ]
    assert "mcp_exa_search" in {item.tool.name for item in catalog.catalog_tools}
    assert "mcp_exa_search" not in {item.tool.name for item in catalog.deferred_catalog_tools}
    # The losing route candidate is dropped entirely, not merely deferred.
    assert "mcp_browserless_search" not in {item.tool.name for item in catalog.catalog_tools}
    assert "mcp_browserless_scraper" in {item.tool.name for item in catalog.deferred_catalog_tools}

    search_result = await catalog.execute_tool(
        context=run_context(policy),
        tool_name=TOOL_SEARCH_NAME,
        call_id="search-call",
        arguments_json={"query": "web search"},
    )
    result_payload = orjson.loads(search_result.output_text)
    exa_result = next(result for result in result_payload["results"] if result["name"] == "mcp_exa_search")
    assert exa_result["callable"] is True
    assert exa_result["call_by_name"] is True
    assert exa_result["invoke_with"] == "mcp_exa_search"

    await catalog.execute_tool(
        context=run_context(policy),
        tool_name=TOOL_CALL_NAME,
        call_id="routed-search-call",
        arguments_json={"tool_id": exa_result["tool_id"]},
    )
    assert provider.calls == [("mcp_exa_search", {})]


@pytest.mark.asyncio
async def test_large_catalog_defers_nonessential_local_tools() -> None:
    local_tools = tuple(
        AgentTool(
            name=name,
            description=f"Local capability {name}",
            source=ToolGrantScope.INTERNAL,
        )
        for name in (
            "list_agent_memories",
            "list_agent_skills",
            "get_agent_skill",
            "create_task_plan",
            "list_task_plans",
        )
    )
    long_tail = tuple(
        AgentTool(
            name=f"mcp_specialized_{index}",
            description=f"Specialized capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )

    catalog = await RunScopedToolCatalogProvider.create(
        provider=RecordingToolProvider((*local_tools, *long_tail)),
        context=run_context(),
    )

    assert catalog.search_enabled is True
    assert [tool.name for tool in catalog.visible_tools] == [
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    ]
    assert {item.tool.name for item in catalog.catalog_tools} == {
        *(tool.name for tool in local_tools),
        *(tool.name for tool in long_tail),
    }
    assert "Voxos tools (5 tools)" in (catalog.deferred_capability_directory() or "")

    search_result = await catalog.execute_tool(
        context=run_context(),
        tool_name=TOOL_SEARCH_NAME,
        call_id="specialist-search",
        arguments_json={"query": "specialized 4"},
    )
    specialist = orjson.loads(search_result.output_text)["results"][0]
    assert specialist["callable"] is True
    assert specialist["call_by_name"] is False
    assert specialist["invoke_with"] == TOOL_CALL_NAME
    assert specialist["describe_with"] == TOOL_DESCRIBE_NAME


@pytest.mark.asyncio
async def test_large_catalog_keeps_required_local_tool_direct() -> None:
    required_tool = AgentTool(
        name="required_browser_task",
        description="Run the exact browser task assigned to this run.",
        source=ToolGrantScope.INTERNAL,
        required=True,
    )
    long_tail = tuple(
        AgentTool(
            name=f"mcp_specialized_{index}",
            description=f"Specialized capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )

    provider = RecordingToolProvider((required_tool, *long_tail))
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=run_context(),
    )

    assert catalog.search_enabled is True
    assert [tool.name for tool in catalog.visible_tools] == [
        required_tool.name,
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    ]
    assert required_tool.name not in {item.tool.name for item in catalog.catalog_tools}
    search_result = await catalog.execute_tool(
        context=run_context(),
        tool_name=TOOL_SEARCH_NAME,
        call_id="required-search",
        arguments_json={"query": "required browser task"},
    )
    assert required_tool.name not in {result["name"] for result in orjson.loads(search_result.output_text)["results"]}
    with pytest.raises(PermissionError, match="not available"):
        await catalog.execute_tool(
            context=run_context(),
            tool_name=TOOL_CALL_NAME,
            call_id="required-call",
            arguments_json={"tool_id": catalog_tool_id(required_tool)},
        )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_large_catalog_keeps_explicitly_visible_provider_tools_direct() -> None:
    browser_account = AgentTool(
        name="use_browser_account_account_handle",
        description="Use the assigned signed-in account.",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="browserless-server",
        always_visible=True,
    )
    long_tail = tuple(
        AgentTool(
            name=f"mcp_specialized_{index}",
            description=f"Specialized capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )

    catalog = await RunScopedToolCatalogProvider.create(
        provider=RecordingToolProvider((browser_account, *long_tail)),
        context=run_context(),
    )

    assert catalog.search_enabled is True
    assert catalog.visible_tools[0].name == browser_account.name
    assert browser_account.name in {item.tool.name for item in catalog.catalog_tools}
    assert browser_account.name not in {item.tool.name for item in catalog.deferred_catalog_tools}


@pytest.mark.asyncio
async def test_large_catalog_exposes_bounded_specialist_capability_directory() -> None:
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
    catalog = await RunScopedToolCatalogProvider.create(
        provider=RecordingToolProvider(maps_tools),
        context=run_context(),
    )

    directory = catalog.deferred_capability_directory()

    assert directory is not None
    assert "Discoverable specialist capabilities" in directory
    assert "Google Maps [google-maps-grounding-lite] (10 tools)" in directory
    assert "search_places" in directory
    assert "+2 more" in directory


def test_catalog_prioritizes_an_explicit_capability_over_generic_keyword_matches() -> None:
    openbnb = AgentTool(
        name="mcp_openbnb_place_details",
        source_tool_name="maps_place_details",
        description="Search Google Places for restaurant reviews, ratings, and local recommendations.",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="openbnb-server",
        catalog_label="OpenBnB",
        capability_key="openbnb",
    )
    google_maps = AgentTool(
        name="mcp_google_maps_search_places",
        source_tool_name="search_places",
        description="Find places and businesses using Google Maps.",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="google-maps-server",
        catalog_label="Google Maps Grounding Lite",
        capability_key="google-maps-grounding-lite",
    )

    matches = search_catalog(
        (
            CatalogTool(id="tool_openbnb", tool=openbnb),
            CatalogTool(id="tool_google_maps", tool=google_maps),
        ),
        query="Google Maps Grounding Lite restaurant reviews in Victoria",
        limit=2,
    )

    assert [match.catalog_tool.tool.name for match in matches] == [
        "mcp_google_maps_search_places",
        "mcp_openbnb_place_details",
    ]


@pytest.mark.asyncio
async def test_catalog_normalizes_google_maps_search_place_aliases_before_validation() -> None:
    google_maps = AgentTool(
        name="mcp_google_maps_search_places",
        source_tool_name="search_places",
        description="Search Google Maps places.",
        parameters_schema={
            "type": "object",
            "properties": {
                "textQuery": {"type": "string"},
                "regionCode": {"type": "string"},
            },
            "required": ["textQuery"],
            "additionalProperties": False,
        },
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id="google-maps-server",
        catalog_label="Google Maps Grounding Lite",
        capability_key="google-maps-grounding-lite",
    )
    filler_tools = tuple(
        AgentTool(
            name=f"mcp_filler_{index}",
            description=f"Filler capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"filler-server-{index}",
        )
        for index in range(9)
    )
    provider = RecordingToolProvider((google_maps, *filler_tools))
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(provider=provider, context=context)
    google_maps_id = next(item.id for item in catalog.catalog_tools if item.tool.name == google_maps.name)

    result = await catalog.execute_tool(
        context=context,
        tool_name=TOOL_CALL_NAME,
        call_id="google-maps-call",
        arguments_json={
            "tool_id": google_maps_id,
            "arguments": {
                "text_query": "restaurants in Victoria, London",
                "region_code": "GB",
                "limit": 5,
            },
        },
    )

    assert result.executed_arguments_json == {
        "textQuery": "restaurants in Victoria, London",
        "regionCode": "GB",
    }
    assert provider.calls == [
        (
            "mcp_google_maps_search_places",
            {
                "textQuery": "restaurants in Victoria, London",
                "regionCode": "GB",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_large_catalog_defers_non_routed_integration_actions() -> None:
    outbound_call = AgentTool(
        name="integration_telnyx_call",
        description="Place an outbound call from the connected Telnyx number.",
        source=ToolGrantScope.INTEGRATION,
        integration_id="integration-123",
    )
    long_tail = tuple(
        AgentTool(
            name=f"mcp_specialized_{index}",
            description=f"Specialized capability {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )

    catalog = await RunScopedToolCatalogProvider.create(
        provider=RecordingToolProvider((outbound_call, *long_tail)),
        context=run_context(),
    )

    assert catalog.search_enabled is True
    assert [tool.name for tool in catalog.visible_tools] == [
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    ]
    assert "integration_telnyx_call" in {item.tool.name for item in catalog.catalog_tools}
    assert "Connected integrations (1 tools)" in (catalog.deferred_capability_directory() or "")


@pytest.mark.asyncio
async def test_prepared_composite_dispatches_without_relisting_providers() -> None:
    first = RecordingToolProvider(
        (AgentTool(name="first", description="First", source=ToolGrantScope.INTERNAL),),
    )
    second = RecordingToolProvider(
        (AgentTool(name="second", description="Second", source=ToolGrantScope.INTERNAL),),
    )
    context = run_context()
    prepared = await prepare_agent_tool_provider(
        CompositeAgentToolProvider((first, second)),
        context,
    )

    await prepared.execute_tool(
        context=context,
        tool_name="second",
        call_id="call-123",
        arguments_json={},
    )

    assert first.list_calls == 1
    assert second.list_calls == 1
    assert first.calls == []
    assert second.calls == [("second", {})]


@pytest.mark.asyncio
async def test_tool_call_cannot_reach_an_id_outside_the_run_catalog() -> None:
    tools = tuple(
        AgentTool(
            name=f"tool_{index}",
            description=f"Tool {index}",
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"server-{index}",
        )
        for index in range(10)
    )
    provider = RecordingToolProvider(tools)
    context = run_context()
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=context,
    )

    with pytest.raises(PermissionError, match="not available"):
        await catalog.execute_tool(
            context=context,
            tool_name=TOOL_CALL_NAME,
            call_id="target-call",
            arguments_json={"tool_id": "tool_not_in_catalog", "arguments": {}},
        )

    assert provider.calls == []


def search_tool(
    *,
    name: str,
    source_name: str,
    server_id: str,
    priority: int,
) -> AgentTool:
    return AgentTool(
        name=name,
        source_tool_name=source_name,
        description="General web search",
        source=ToolGrantScope.MCP_SERVER,
        mcp_server_id=server_id,
        route_action="web.search",
        automatic_route_priority=priority,
    )


def run_context(
    policy: EffectiveToolPolicySnapshot | None = None,
) -> AgentRunContext:
    return AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-test",
        instructions="Help the user.",
        effective_tool_policy_id=None if policy is None else policy.id,
        effective_tool_policy=policy,
        session_started_at=datetime.now(UTC),
    )

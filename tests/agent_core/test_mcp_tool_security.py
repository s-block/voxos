import asyncio

import pytest
from sqlmodel import select

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.agent_core.mcp_tools import (
    UserMcpAgentToolProvider,
    agent_tool_from_mcp_definition,
)
from voxos.agent_core.models import AgentRunContext
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.records import AgentToolAuditEventRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_execution import (
    ToolAuthorityArgumentError,
    ToolExecutionContext,
    validate_llm_tool_arguments,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.logos.models import ActivityIcon, BrandLogo
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    McpToolCallResult,
)
from voxos.mcp.errors import McpToolCallRejectedError
from voxos.mcp.models import (
    McpDiscoveredToolDefinition,
    McpServerSafeMetadata,
    McpTransportType,
)
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import UserMcpToolsetConfig


class RecordingMcpClient:
    def __init__(self) -> None:
        self.discovery_calls: list[str] = []
        self.tool_calls: list[tuple[str, str, dict[str, object]]] = []
        self.tool_call_configs: list[McpClientConnectionConfig] = []

    async def discover_capabilities(
        self,
        config: McpClientConnectionConfig,
    ) -> DiscoveredMcpCapabilities:
        self.discovery_calls.append(config.url)
        return DiscoveredMcpCapabilities(
            tools=("search",),
            tool_definitions=(
                DiscoveredMcpTool(
                    name="search",
                    description="Search",
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
        self.tool_call_configs.append(config)
        self.tool_calls.append((config.url, tool_name, arguments))
        return McpToolCallResult(content_text="ok")


class RejectingMcpClient(RecordingMcpClient):
    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: dict[str, object],
    ) -> McpToolCallResult:
        raise McpToolCallRejectedError


class GoogleMapsMcpClient(RecordingMcpClient):
    def __init__(self, *, is_error: bool = False) -> None:
        super().__init__()
        self.is_error = is_error

    async def call_tool(
        self,
        config: McpClientConnectionConfig,
        tool_name: str,
        arguments: dict[str, object],
    ) -> McpToolCallResult:
        self.tool_calls.append((config.url, tool_name, arguments))
        place_url = "https://www.google.com/maps/place/?q=place_id:ChIJ123"
        if self.is_error:
            return McpToolCallResult(
                content_text=f"Partial failed result: {place_url}",
                is_error=True,
            )
        return McpToolCallResult(
            content_text="A nearby restaurant.",
            structured_content={
                "places": [
                    {
                        "id": "ChIJ123",
                        "googleMapsLinks": {"placeUrl": place_url},
                        "attribution": {
                            "title": "Konoba Stari Grad",
                            "url": place_url,
                        },
                    },
                ],
            },
        )


def google_maps_toolset_config() -> UserMcpToolsetConfig:
    return UserMcpToolsetConfig(
        id="user-mcp-google-maps",
        server_id="google-maps-server",
        label="Google Maps",
        url="https://mapstools.googleapis.com/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("search_places",),
        tool_definitions=(
            McpDiscoveredToolDefinition(
                name="search_places",
                description="Search for places.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "textQuery": {"type": "string"},
                    },
                    "required": ["textQuery"],
                },
            ),
        ),
        preset_id="google-maps-grounding-lite",
    )


def google_maps_run_context() -> AgentRunContext:
    return AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        root_run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
    )


class StaticMcpProvider(UserMcpAgentToolProvider):
    _configs: tuple[UserMcpToolsetConfig, ...]
    _toolset_config_calls: int

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
        mcp_client: RecordingMcpClient,
        authorization_service: SqlModelToolAuthorizationService | None,
        configs: tuple[UserMcpToolsetConfig, ...],
        tool_catalog_cache: McpToolCatalogCache | None = None,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            secret_vault=secret_vault,
            authorization_service=authorization_service,
            mcp_client=mcp_client,
            tool_catalog_cache=tool_catalog_cache,
        )
        object.__setattr__(self, "_configs", configs)
        object.__setattr__(self, "_toolset_config_calls", 0)

    async def _toolset_configs(
        self,
        user_id: str,
    ) -> tuple[UserMcpToolsetConfig, ...]:
        object.__setattr__(
            self,
            "_toolset_config_calls",
            self._toolset_config_calls + 1,
        )
        return self._configs


class ConcurrentAllowedNamesProvider(StaticMcpProvider):
    _active_authorizations: int
    _max_active_authorizations: int

    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
        mcp_client: RecordingMcpClient,
        configs: tuple[UserMcpToolsetConfig, ...],
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            secret_vault=secret_vault,
            mcp_client=mcp_client,
            authorization_service=None,
            configs=configs,
        )
        object.__setattr__(self, "_active_authorizations", 0)
        object.__setattr__(self, "_max_active_authorizations", 0)

    async def _allowed_tool_names(
        self,
        *,
        execution_context: ToolExecutionContext,
        config: UserMcpToolsetConfig,
    ) -> tuple[str, ...]:
        del execution_context
        active = self._active_authorizations + 1
        object.__setattr__(self, "_active_authorizations", active)
        object.__setattr__(
            self,
            "_max_active_authorizations",
            max(self._max_active_authorizations, active),
        )
        await asyncio.sleep(0.01)
        object.__setattr__(self, "_active_authorizations", active - 1)
        return config.allowed_tools


@pytest.mark.asyncio
async def test_mcp_tool_listing_uses_cached_definitions_without_live_discovery(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=None,
        configs=(
            UserMcpToolsetConfig(
                id="user-mcp-search",
                server_id="search-server",
                label="Search",
                url="https://search.example/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
                tool_definitions=(
                    McpDiscoveredToolDefinition(
                        name="search",
                        description="Search the current web",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                ),
            ),
        ),
    )

    tools = await provider.list_tools(
        AgentRunContext(
            session_id="session-123",
            run_id="run-123",
            root_run_id="run-123",
            user_id="user-123",
            agent_id="agent-123",
            provider="openai",
            model="gpt-5.4-nano",
            instructions="Help the user.",
        ),
    )

    assert mcp_client.discovery_calls == []
    assert "Search the current web" in tools[0].description
    assert tools[0].parameters_schema["required"] == ["query"]


@pytest.mark.asyncio
async def test_mcp_tool_listing_reuses_and_invalidates_catalog_cache(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    cache = McpToolCatalogCache(max_entries=10, ttl_seconds=60)
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=RecordingMcpClient(),
        authorization_service=None,
        configs=(
            UserMcpToolsetConfig(
                id="user-mcp-search",
                server_id="search-server",
                label="Search",
                url="https://search.example/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
                tool_definitions=(
                    McpDiscoveredToolDefinition(
                        name="search",
                        description="Search the current web",
                        input_schema={"type": "object"},
                    ),
                ),
            ),
        ),
        tool_catalog_cache=cache,
    )
    context = AgentRunContext(
        session_id="session-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-5.4-mini",
        instructions="Help the user.",
    )

    await provider.list_tools(context)
    await provider.list_tools(context)
    await cache.invalidate_user(context.user_id)
    await provider.list_tools(context)

    assert provider._toolset_config_calls == 2


@pytest.mark.asyncio
async def test_mcp_tool_listing_authorizes_servers_concurrently(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    configs = tuple(
        UserMcpToolsetConfig(
            id=f"user-mcp-{index}",
            server_id=f"server-{index}",
            label=f"Server {index}",
            url=f"https://server-{index}.example/mcp",
            transport=McpTransportType.STREAMABLE_HTTP,
            headers={},
            allowed_tools=("search",),
            tool_definitions=(
                McpDiscoveredToolDefinition(
                    name="search",
                    description="Search the current web",
                    input_schema={"type": "object"},
                ),
            ),
        )
        for index in range(3)
    )
    provider = ConcurrentAllowedNamesProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=RecordingMcpClient(),
        configs=configs,
    )

    tools = await provider.list_tools(
        AgentRunContext(
            session_id="session-123",
            user_id="user-123",
            agent_id="agent-123",
            provider="openai",
            model="gpt-5.4-mini",
            instructions="Help the user.",
        ),
    )

    assert len(tools) == 3
    assert provider._max_active_authorizations == 3


@pytest.mark.asyncio
async def test_mcp_tool_listing_discovers_and_caches_missing_definitions(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with sqlite_session_factory() as session:
        server = UserMcpServerRecord(
            user_id="user-123",
            label="Legacy Search",
            url="https://search.example/mcp",
            transport=McpTransportType.STREAMABLE_HTTP.value,
            auth_type="none",
            enabled=True,
            trust_state="trusted",
            status="connected",
            allowed_tools=["search"],
            discovered_tools=["search"],
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        server_id = server.id

    mcp_client = RecordingMcpClient()
    provider = UserMcpAgentToolProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        root_run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-5.4-nano",
        instructions="Help the user.",
    )

    first_tools = await provider.list_tools(context)
    second_tools = await provider.list_tools(context)

    assert mcp_client.discovery_calls == ["https://search.example/mcp"]
    assert first_tools[0].parameters_schema["type"] == "object"
    assert second_tools[0].description == first_tools[0].description
    async with sqlite_session_factory() as session:
        cached_server = await session.get(UserMcpServerRecord, server_id)
    assert cached_server is not None
    metadata = McpServerSafeMetadata.model_validate(cached_server.safe_metadata)
    assert metadata.tool_definitions[0].name == "search"


def test_mcp_tool_parallel_safety_requires_read_only_metadata() -> None:
    config = UserMcpToolsetConfig(
        id="toolset-1",
        server_id="server-1",
        label="Custom",
        url="https://mcp.example.test",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("read", "unknown"),
    )

    read_tool = agent_tool_from_mcp_definition(
        config,
        "read",
        DiscoveredMcpTool(
            name="read",
            description="Read",
            input_schema={"type": "object"},
            read_only_hint=True,
        ),
    )
    unknown_tool = agent_tool_from_mcp_definition(
        config,
        "unknown",
        DiscoveredMcpTool(
            name="unknown",
            description="Unknown",
            input_schema={"type": "object"},
        ),
    )

    assert read_tool.parallel_safe is True
    assert unknown_tool.parallel_safe is False


@pytest.mark.asyncio
async def test_mcp_mutating_tool_call_is_not_marked_retry_safe(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=None,
        configs=(
            UserMcpToolsetConfig(
                id="user-mcp-custom",
                server_id="custom-server",
                label="Custom",
                url="https://custom.example/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("create_record",),
                tool_definitions=(
                    McpDiscoveredToolDefinition(
                        name="create_record",
                        description="Create a record.",
                        input_schema={"type": "object"},
                    ),
                ),
            ),
        ),
    )
    context = google_maps_run_context()
    tool = (await provider.list_tools(context))[0]

    await provider.execute_tool(
        context=context,
        tool_name=tool.name,
        call_id="call-create",
        arguments_json={},
    )

    assert mcp_client.tool_call_configs[-1].retry_safe is False


def test_mcp_tool_activity_uses_preset_copy_and_server_logo() -> None:
    config = UserMcpToolsetConfig(
        id="toolset-exa",
        server_id="server-exa",
        label="Exa",
        url="https://mcp.exa.ai/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("web_search_exa",),
        preset_id="exa",
        logo=BrandLogo(domain="exa.ai", label="Exa"),
    )

    tool = agent_tool_from_mcp_definition(
        config,
        "web_search_exa",
        DiscoveredMcpTool(
            name="web_search_exa",
            description="Search the web",
            input_schema={"type": "object"},
        ),
    )

    assert tool.activity is not None
    assert tool.activity.label == "Searching the web"
    assert tool.activity.detail == "Exa"
    assert tool.activity.icon == ActivityIcon.WEB
    assert tool.activity.logo is not None
    assert tool.activity.logo.domain == "exa.ai"


def test_custom_mcp_tool_activity_uses_generic_local_icon() -> None:
    config = UserMcpToolsetConfig(
        id="toolset-local",
        server_id="server-local",
        label="Local tools",
        url="http://localhost:8181/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("lookup",),
    )

    tool = agent_tool_from_mcp_definition(
        config,
        "lookup",
        DiscoveredMcpTool(
            name="lookup",
            description="Look up local data",
            input_schema={"type": "object"},
        ),
    )

    assert tool.activity is not None
    assert tool.activity.label == "Using Local tools"
    assert tool.activity.icon == ActivityIcon.MCP
    assert tool.activity.logo is None


@pytest.mark.parametrize(
    "authority_key",
    (
        "profile_id",
        "profileId",
        "browser_profile_id",
        "browserProfileId",
        "context_id",
        "nestedBrowserAccountId",
        "browser-session-id",
    ),
)
def test_browser_identity_selectors_are_authority_arguments(
    authority_key: str,
) -> None:
    with pytest.raises(ToolAuthorityArgumentError):
        validate_llm_tool_arguments({"nested": {authority_key: "attacker-choice"}})


def test_model_visible_mcp_schema_removes_browser_authority_selectors() -> None:
    config = UserMcpToolsetConfig(
        id="browser-use-toolset",
        server_id="browser-use-server",
        label="Browser Use",
        url="https://api.browser-use.com/v3/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("run_session",),
        preset_id="browser-use-cloud",
    )
    tool = agent_tool_from_mcp_definition(
        config,
        "run_session",
        DiscoveredMcpTool(
            name="run_session",
            description="Run a browser session",
            input_schema={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "profileId": {"type": "string"},
                    "options": {
                        "type": "object",
                        "properties": {"context_id": {"type": "string"}},
                        "required": ["context_id"],
                    },
                },
                "required": ["task", "profileId"],
            },
        ),
    )

    assert tool.risk == ToolRisk.HIGH
    assert tool.parameters_schema["required"] == ["task"]
    properties = tool.parameters_schema["properties"]
    assert isinstance(properties, dict)
    assert "profileId" not in properties
    options = properties["options"]
    assert isinstance(options, dict)
    assert options["required"] == []
    assert options["properties"] == {}


def test_model_visible_schema_preserves_curated_authority_paths_in_arrays() -> None:
    config = UserMcpToolsetConfig(
        id="browserless-toolset",
        server_id="browserless-server",
        label="Browserless",
        url="https://production-sfo.browserless.io/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("browserless_agent",),
        preset_id="browserless",
    )
    tool = agent_tool_from_mcp_definition(
        config,
        "browserless_agent",
        DiscoveredMcpTool(
            name="browserless_agent",
            description="Run browser commands",
            input_schema={
                "type": "object",
                "properties": {
                    "commands": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "params": {
                                    "type": "object",
                                    "properties": {"url": {"type": "string"}},
                                    "required": ["url"],
                                },
                            },
                        },
                    },
                },
            },
        ),
    )

    commands = tool.parameters_schema["properties"]
    assert isinstance(commands, dict)
    commands_schema = commands["commands"]
    assert isinstance(commands_schema, dict)
    items_schema = commands_schema["items"]
    assert isinstance(items_schema, dict)
    item_properties = items_schema["properties"]
    assert isinstance(item_properties, dict)
    params_schema = item_properties["params"]
    assert isinstance(params_schema, dict)
    assert params_schema["properties"] == {"url": {"type": "string"}}
    assert params_schema["required"] == ["url"]


def test_curated_weather_tool_is_parallel_safe() -> None:
    config = UserMcpToolsetConfig(
        id="toolset-1",
        server_id="server-1",
        label="Google Maps",
        url="https://mapstools.googleapis.com/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("lookup_weather",),
        preset_id="google-maps-grounding-lite",
    )

    weather_tool = agent_tool_from_mcp_definition(
        config,
        "lookup_weather",
        DiscoveredMcpTool(
            name="lookup_weather",
            description="Look up weather",
            input_schema={"type": "object"},
        ),
    )

    assert weather_tool.parallel_safe is True


def test_curated_search_tool_carries_trusted_route_metadata() -> None:
    config = UserMcpToolsetConfig(
        id="toolset-1",
        server_id="server-1",
        label="Exa",
        url="https://mcp.exa.ai/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("web_search_exa",),
        preset_id="exa",
    )

    search_tool = agent_tool_from_mcp_definition(
        config,
        "web_search_exa",
        DiscoveredMcpTool(
            name="web_search_exa",
            description="Search the web",
            input_schema={"type": "object"},
        ),
    )

    assert search_tool.source_tool_name == "web_search_exa"
    assert search_tool.route_action == "web.search"
    assert search_tool.automatic_route_priority == 10


@pytest.mark.asyncio
async def test_mcp_tool_allows_only_curated_browserless_url_path(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=None,
        configs=(
            UserMcpToolsetConfig(
                id="user-mcp-browserless",
                server_id="browserless-server",
                label="Browserless",
                url="https://mcp.browserless.io/mcp",
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("browserless_smartscraper",),
                tool_definitions=(
                    McpDiscoveredToolDefinition(
                        name="browserless_smartscraper",
                        description="Extract structured content from a URL.",
                        input_schema={"type": "object"},
                    ),
                ),
                preset_id="browserless",
            ),
        ),
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        root_run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
    )
    tools = await provider.list_tools(context)

    result = await provider.execute_tool(
        context=context,
        tool_name=tools[0].name,
        call_id="call-123",
        arguments_json={"url": "https://example.test"},
    )

    assert result.output_text == "ok"
    assert tools[0].authority_argument_allowed_paths == frozenset({("url",)})
    assert mcp_client.tool_calls == [
        (
            "https://mcp.browserless.io/mcp",
            "browserless_smartscraper",
            {"url": "https://example.test"},
        ),
    ]
    assert mcp_client.tool_call_configs[-1].retry_safe is True

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name=tools[0].name,
            call_id="call-456",
            arguments_json={
                "url": "https://example.test",
                "headers": {"Authorization": "Bearer attacker-token"},
            },
        )

    assert len(mcp_client.tool_calls) == 1


@pytest.mark.asyncio
async def test_google_weather_tool_normalizes_resolved_place_resource_and_guides_repair(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    config = UserMcpToolsetConfig(
        id="user-mcp-google-maps",
        server_id="google-maps-server",
        label="Google Maps",
        url="https://mapstools.googleapis.com/mcp",
        transport=McpTransportType.STREAMABLE_HTTP,
        headers={},
        allowed_tools=("lookup_weather",),
        tool_definitions=(
            McpDiscoveredToolDefinition(
                name="lookup_weather",
                description="Look up weather for a resolved place.",
                input_schema={
                    "type": "object",
                    "$defs": {
                        "Location": {
                            "type": "object",
                            "properties": {
                                "address": {"type": "string"},
                                "placeId": {"type": "string"},
                            },
                        },
                    },
                    "properties": {
                        "location": {"$ref": "#/$defs/Location"},
                    },
                    "required": ["location"],
                },
            ),
        ),
        preset_id="google-maps-grounding-lite",
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id="run-123",
        root_run_id="run-123",
        user_id="user-123",
        agent_id="agent-123",
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
    )
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=None,
        configs=(config,),
    )
    tool = (await provider.list_tools(context))[0]

    await provider.execute_tool(
        context=context,
        tool_name=tool.name,
        call_id="call-123",
        arguments_json={
            "location": {
                "placeId": "places/example-place-id",
            },
        },
    )

    assert "do not call resolve_names first" in tool.description
    assert mcp_client.tool_calls[-1][2] == {
        "location": {
            "placeId": "example-place-id",
        },
    }

    rejecting_provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=RejectingMcpClient(),
        authorization_service=None,
        configs=(config,),
    )
    with pytest.raises(McpToolCallRejectedError) as raised:
        await rejecting_provider.execute_tool(
            context=context,
            tool_name=tool.name,
            call_id="call-456",
            arguments_json={"location": {"address": "London, UK"}},
        )

    assert raised.value.recovery_guidance is not None
    assert "location.address only" in raised.value.recovery_guidance


@pytest.mark.asyncio
async def test_google_places_tool_returns_typed_place_cards_and_model_sources(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    config = google_maps_toolset_config()
    context = google_maps_run_context()
    mcp_client = GoogleMapsMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=None,
        configs=(config,),
    )
    tool = (await provider.list_tools(context))[0]

    result = await provider.execute_tool(
        context=context,
        tool_name=tool.name,
        call_id="call-123",
        arguments_json={"textQuery": "restaurants near me", "limit": 5},
    )

    assert mcp_client.tool_calls[-1][2] == {
        "textQuery": "restaurants near me",
    }
    assert result.executed_arguments_json == {
        "textQuery": "restaurants near me",
    }
    assert result.place_card_artifacts[0].place_id == "ChIJ123"
    assert result.place_card_artifacts[0].title == "Konoba Stari Grad"
    assert result.place_card_artifacts[0].place_url in result.output_text
    assert "Google Maps source links" in result.output_text
    assert result.persisted_output_text == "Google Maps place search completed."


@pytest.mark.asyncio
async def test_google_places_tool_redacts_failed_result_for_persistence(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    context = google_maps_run_context()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=GoogleMapsMcpClient(is_error=True),
        authorization_service=None,
        configs=(google_maps_toolset_config(),),
    )
    tool = (await provider.list_tools(context))[0]

    result = await provider.execute_tool(
        context=context,
        tool_name=tool.name,
        call_id="call-failed",
        arguments_json={"textQuery": "restaurants near me"},
    )

    assert result.is_error is True
    assert "Partial failed result" in result.output_text
    assert result.persisted_output_text == "Google Maps place search failed."
    assert result.place_card_artifacts == ()


@pytest.mark.asyncio
async def test_mcp_tool_rejects_llm_supplied_server_and_auth_fields(
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
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
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
        payload_json={"text": "Use MCP"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
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
                headers={"Authorization": "Bearer server-token"},
                allowed_tools=("search",),
            ),
        ),
    )
    tools = await provider.list_tools(
        AgentRunContext(
            session_id="session-123",
            run_id=run.id,
            root_run_id=run.root_run_id,
            user_id="user-123",
            agent_id=agent.id,
            provider="openai",
            model="gpt-4.1-mini",
            instructions="System instructions.",
            effective_tool_policy_id=policy.id,
        ),
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=AgentRunContext(
                session_id="session-123",
                run_id=run.id,
                root_run_id=run.root_run_id,
                user_id="user-123",
                agent_id=agent.id,
                provider="openai",
                model="gpt-4.1-mini",
                instructions="System instructions.",
                effective_tool_policy_id=policy.id,
            ),
            tool_name=tools[0].name,
            call_id="call-123",
            arguments_json={
                "query": "tickets",
                "mcp_server_id": "other-server",
                "headers": {"Authorization": "Bearer attacker-token"},
            },
        )

    assert mcp_client.tool_calls == []
    async with sqlite_session_factory() as session:
        result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = result.all()
    assert audit_events[-1].user_id == "user-123"
    assert audit_events[-1].run_id == run.id
    assert audit_events[-1].mcp_server_id == server.id
    assert audit_events[-1].decision == "denied"
    assert audit_events[-1].metadata_json == {"category": "invalid_arguments"}


@pytest.mark.asyncio
async def test_mcp_tool_cannot_use_another_users_server_alias(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    other_agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("other-user")
    async with sqlite_session_factory() as session:
        owner_server = UserMcpServerRecord(
            user_id="user-123",
            label="Owner",
            transport=McpTransportType.STREAMABLE_HTTP.value,
            url="https://owner.example/mcp",
            auth_type="none",
            allowed_tools=["search"],
        )
        other_server = UserMcpServerRecord(
            user_id="other-user",
            label="Other",
            transport=McpTransportType.STREAMABLE_HTTP.value,
            url="https://other.example/mcp",
            auth_type="none",
            allowed_tools=["search"],
        )
        session.add(owner_server)
        session.add(other_server)
        await session.commit()
        await session.refresh(owner_server)
        await session.refresh(other_server)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=owner_server.id,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    other_authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await other_authorization_service.set_grants(
        user_id="other-user",
        agent_id=other_agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=other_server.id,
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
        payload_json={"text": "Use MCP"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    mcp_client = RecordingMcpClient()
    provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=mcp_client,
        authorization_service=authorization_service,
        configs=(
            UserMcpToolsetConfig(
                id=f"user-mcp-{owner_server.id}",
                server_id=owner_server.id,
                label=owner_server.label,
                url=owner_server.url,
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
            ),
        ),
    )
    other_provider = StaticMcpProvider(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        mcp_client=RecordingMcpClient(),
        authorization_service=other_authorization_service,
        configs=(
            UserMcpToolsetConfig(
                id=f"user-mcp-{other_server.id}",
                server_id=other_server.id,
                label=other_server.label,
                url=other_server.url,
                transport=McpTransportType.STREAMABLE_HTTP,
                headers={},
                allowed_tools=("search",),
            ),
        ),
    )
    other_tools = await other_provider.list_tools(
        AgentRunContext(
            session_id="session-456",
            run_id=None,
            user_id="other-user",
            agent_id=other_agent.id,
            provider="openai",
            model="gpt-4.1-mini",
            instructions="System instructions.",
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

    with pytest.raises(PermissionError):
        await provider.execute_tool(
            context=context,
            tool_name=other_tools[0].name,
            call_id="call-123",
            arguments_json={"query": "tickets"},
        )

    assert mcp_client.tool_calls == []

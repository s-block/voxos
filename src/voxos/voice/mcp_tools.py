import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

import httpx
from livekit.agents import function_tool
from livekit.agents.llm.mcp import MCPServerHTTP, MCPToolset

from voxos.agent_core.internal_tools import (
    INTERNAL_AGENT_MCP_TOOL_NAMES,
    INTERNAL_TOOL_DESCRIPTION_BY_NAME,
    INTERNAL_TOOL_SCHEMA_BY_NAME,
    InternalAgentToolExecutor,
    execute_internal_tool,
    internal_tool_functions,
)
from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.agent_core.mcp_tools import UserMcpAgentToolProvider
from voxos.agent_core.models import AgentRunContext, AgentTool
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_core.tool_catalog import (
    TOOL_SEARCH_NAME,
    RunScopedToolCatalogProvider,
)
from voxos.agent_identity.models import AgentScope
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.history import ACCOUNT_LEARNING_TOOLS, TenantHistoryService
from voxos.agent_runtime.improvements import AgentImprovementService
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_execution import (
    ToolExecutionContext,
    prune_unsupported_tool_arguments,
    validate_llm_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import ToolAuthorizationService, ToolGrantScope
from voxos.agent_skills.services import AgentSkillService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import new_record_id
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.client import SdkUserMcpClient, UserMcpClient
from voxos.mcp.delegation import (
    DELEGATION_PROVIDER_MONZO,
    McpCredentialDelegationHttpAuth,
    McpCredentialDelegationSigner,
    credential_delegation_signer_from_settings,
)
from voxos.mcp.oauth import McpOAuthHttpClient
from voxos.mcp.presets import mcp_preset_tool_route
from voxos.mcp.services import (
    MCP_CREDENTIAL_DELEGATION_HEADER,
    UserMcpServerService,
    UserMcpToolsetConfig,
)
from voxos.mcp.settings import McpSettings
from voxos.persona.mcp import PERSONA_MCP_TOOL_NAMES
from voxos.product_docs.retrieval import ProductDocSearchService
from voxos.voice.tool_catalog import RunScopedVoiceToolCatalog
from voxos.voice.tools import (
    ScopedVoiceCatalogToolProvider,
    ScopedVoiceToolProvider,
    VoiceCatalogFunctionTool,
    VoiceCatalogTool,
    VoiceLiveKitTool,
    VoiceToolProvider,
)

AUTHORIZATION_HEADER_NAME = "Authorization"
USER_MCP_HTTP_CLIENT = httpx.AsyncClient()
REALTIME_UNSUPPORTED_ROOT_SCHEMA_KEYS = frozenset(
    {"allOf", "anyOf", "const", "enum", "not", "oneOf"},
)
__all__ = (
    "CompositeVoiceToolProvider",
    "INTERNAL_AGENT_MCP_TOOL_NAMES",
    "InternalAgentVoiceToolProvider",
    "McpVoiceToolProvider",
    "UserMcpVoiceToolProvider",
)


class VoiceMcpBearerTokenResolver(Protocol):
    async def bearer_token_for_user(self, user_id: str) -> str:
        """Return a bearer token scoped to the given local Voxos user."""


@dataclass(frozen=True, slots=True)
class McpVoiceToolProvider:
    mcp_url: str
    token_resolver: VoiceMcpBearerTokenResolver
    allowed_tools: tuple[str, ...] = PERSONA_MCP_TOOL_NAMES
    toolset_id: str = "voxos-persona-mcp"
    transport_type: Literal["sse", "streamable_http"] = "streamable_http"
    timeout_seconds: float = 5.0
    client_session_timeout_seconds: float = 5.0

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        bearer_token = await self.token_resolver.bearer_token_for_user(user_id)
        stripped_token = bearer_token.strip()
        if not stripped_token:
            message = "MCP bearer token resolver returned an empty token"
            raise ValueError(message)

        server = MCPServerHTTP(
            url=self.mcp_url,
            transport_type=self.transport_type,
            allowed_tools=list(self.allowed_tools),
            headers={AUTHORIZATION_HEADER_NAME: f"Bearer {stripped_token}"},
            timeout=self.timeout_seconds,
            client_session_timeout_seconds=self.client_session_timeout_seconds,
        )
        return (MCPToolset(id=self.toolset_id, mcp_server=server),)

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        return await self.get_tools(scope.owner_user_id)


class CredentialDelegatingMCPServerHTTP(MCPServerHTTP):
    """LiveKit MCP transport that rotates its broker assertion per HTTP request."""

    def __init__(
        self,
        *,
        url: str,
        transport_type: Literal["sse", "streamable_http"],
        allowed_tools: list[str],
        headers: dict[str, str],
        timeout: float,
        client_session_timeout_seconds: float,
        credential_delegation_auth: McpCredentialDelegationHttpAuth,
    ) -> None:
        super().__init__(
            url=url,
            transport_type=transport_type,
            allowed_tools=allowed_tools,
            headers=headers,
            timeout=timeout,
            client_session_timeout_seconds=client_session_timeout_seconds,
        )
        self._credential_delegation_auth = credential_delegation_auth

    def _create_http_client(
        self,
        headers: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return super()._create_http_client(
            headers=headers,
            timeout=timeout,
            auth=self._credential_delegation_auth,
        )


@dataclass(frozen=True, slots=True)
class UserMcpVoiceToolProvider:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    authorization_service: ToolAuthorizationService | None = None
    http_client: httpx.AsyncClient | None = None
    mcp_client: UserMcpClient = field(default_factory=SdkUserMcpClient.from_settings)
    tool_catalog_cache: McpToolCatalogCache | None = None
    credential_delegation_signer: McpCredentialDelegationSigner | None = field(
        default_factory=lambda: credential_delegation_signer_from_settings(McpSettings())
    )
    timeout_seconds: float = 5.0
    client_session_timeout_seconds: float = 5.0

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        return await self._toolsets_for_user(user_id=user_id, scope=None)

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        context, provider = await self._catalog_provider_for_scope(
            scope=scope,
            effective_tool_policy=effective_tool_policy,
        )
        tools = await provider.list_tools(context)
        capability_directory = provider.deferred_capability_directory()
        return tuple(
            self._function_tool(
                tool=tool,
                provider=provider,
                context=context,
                capability_directory=capability_directory,
            )
            for tool in tools
        )

    async def get_catalog_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceCatalogTool]:
        context, provider = await self._catalog_provider_for_scope(
            scope=scope,
            effective_tool_policy=effective_tool_policy,
        )
        return tuple(
            VoiceCatalogTool(
                definition=tool,
                function_tool=self._function_tool(
                    tool=tool,
                    provider=provider,
                    context=context,
                    capability_directory=None,
                ),
            )
            for tool in provider.routed_tools
        )

    async def _catalog_provider_for_scope(
        self,
        *,
        scope: AgentScope,
        effective_tool_policy: EffectiveToolPolicySnapshot | None,
    ) -> tuple[AgentRunContext, RunScopedToolCatalogProvider]:
        context = self._run_context(
            scope=scope,
            effective_tool_policy=effective_tool_policy,
        )
        provider = await RunScopedToolCatalogProvider.create(
            provider=self._agent_tool_provider(),
            context=context,
        )
        return context, provider

    def _agent_tool_provider(self) -> UserMcpAgentToolProvider:
        return UserMcpAgentToolProvider(
            session_factory=self.session_factory,
            secret_vault=self.secret_vault,
            authorization_service=self.authorization_service,
            http_client=self.http_client,
            mcp_client=self.mcp_client,
            tool_catalog_cache=self.tool_catalog_cache,
            credential_delegation_signer=self.credential_delegation_signer,
            execution_source_prefix="voice.mcp_tools",
            execution_channel="voice",
            require_run_for_execution=False,
        )

    @staticmethod
    def _run_context(
        *,
        scope: AgentScope,
        effective_tool_policy: EffectiveToolPolicySnapshot | None,
    ) -> AgentRunContext:
        if scope.session_id is None:
            message = "Voice MCP tools require a durable runtime session"
            raise ValueError(message)
        return AgentRunContext(
            session_id=scope.session_id,
            run_id=scope.run_id,
            user_id=scope.owner_user_id,
            agent_id=scope.agent_id,
            provider="voice",
            model="realtime",
            instructions="Execute authorized voice-session tools.",
            communication_medium=CommunicationMedium.VOICE,
            effective_tool_policy=effective_tool_policy,
        )

    @staticmethod
    def _function_tool(
        *,
        tool: AgentTool,
        provider: RunScopedToolCatalogProvider,
        context: AgentRunContext,
        capability_directory: str | None,
    ) -> VoiceCatalogFunctionTool:
        async def call_mcp_tool(raw_arguments: dict[str, object]) -> object:
            arguments_json: JsonObject = raw_arguments
            validate_llm_tool_arguments(
                arguments_json,
                allowed_paths=tool.authority_argument_allowed_paths,
            )
            accepted_arguments = prune_unsupported_tool_arguments(
                arguments_json,
                tool.parameters_schema,
            )
            validate_tool_arguments_schema(
                accepted_arguments,
                tool.parameters_schema,
            )
            result = await provider.execute_tool(
                context=context,
                tool_name=tool.name,
                call_id=new_record_id(),
                arguments_json=accepted_arguments,
            )
            return {
                "content": result.output_text,
                "is_error": result.is_error,
                "structured_content": result.raw_output,
            }

        call_mcp_tool.__name__ = tool.name
        return cast(
            VoiceCatalogFunctionTool,
            function_tool(
                call_mcp_tool,
                raw_schema={
                    "name": tool.name,
                    "description": realtime_tool_description(
                        tool=tool,
                        capability_directory=capability_directory,
                    ),
                    "parameters": realtime_tool_parameters_schema(
                        tool.parameters_schema,
                    ),
                },
            ),
        )

    async def _toolsets_for_user(
        self,
        *,
        user_id: str,
        scope: AgentScope | None,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        async with self.session_factory() as session:
            service = UserMcpServerService(
                session=session,
                secret_vault=self.secret_vault,
                mcp_client=self.mcp_client,
                oauth_client=McpOAuthHttpClient(
                    self.http_client or USER_MCP_HTTP_CLIENT,
                ),
                credential_delegation_signer=(self.credential_delegation_signer),
            )
            configs = await service.enabled_toolset_configs(user_id)

        allowed_by_server_id: dict[str, tuple[str, ...]] = {}
        for config in configs:
            allowed_tools = config.allowed_tools
            if self.authorization_service is not None and scope is not None:
                allowed_tools = await self.authorization_service.allowed_tool_names(
                    scope=scope,
                    tool_source=ToolGrantScope.MCP_SERVER,
                    tool_names=config.allowed_tools,
                    mcp_server_id=config.server_id,
                )
            allowed_by_server_id[config.server_id] = allowed_tools
        allowed_by_server_id = route_voice_mcp_tools(
            configs=configs,
            allowed_by_server_id=allowed_by_server_id,
            effective_tool_policy=effective_tool_policy,
        )

        toolsets: list[VoiceLiveKitTool] = []
        for config in configs:
            allowed_tools = allowed_by_server_id.get(config.server_id, ())
            if not allowed_tools:
                continue
            server = self._server_for_config(
                user_id=user_id,
                config=config,
                allowed_tools=allowed_tools,
            )
            toolsets.append(MCPToolset(id=config.id, mcp_server=server))
        return tuple(toolsets)

    def _server_for_config(
        self,
        *,
        user_id: str,
        config: UserMcpToolsetConfig,
        allowed_tools: tuple[str, ...],
    ) -> MCPServerHTTP:
        headers = dict(config.headers)
        delegation = headers.pop(MCP_CREDENTIAL_DELEGATION_HEADER, None)
        if delegation is None:
            return MCPServerHTTP(
                url=config.url,
                transport_type=config.transport.value,
                allowed_tools=list(allowed_tools),
                headers=headers,
                timeout=self.timeout_seconds,
                client_session_timeout_seconds=(self.client_session_timeout_seconds),
            )
        if self.credential_delegation_signer is None:
            message = "MCP credential delegation signing is not configured"
            raise ValueError(message)
        return CredentialDelegatingMCPServerHTTP(
            url=config.url,
            transport_type=config.transport.value,
            allowed_tools=list(allowed_tools),
            headers=headers,
            timeout=self.timeout_seconds,
            client_session_timeout_seconds=(self.client_session_timeout_seconds),
            credential_delegation_auth=McpCredentialDelegationHttpAuth(
                signer=self.credential_delegation_signer,
                user_id=user_id,
                server_id=config.server_id,
                provider=DELEGATION_PROVIDER_MONZO,
                header_name=MCP_CREDENTIAL_DELEGATION_HEADER,
            ),
        )


def realtime_tool_description(
    *,
    tool: AgentTool,
    capability_directory: str | None,
) -> str:
    """Expose deferred voice capabilities where the realtime model can see them."""

    if tool.name != TOOL_SEARCH_NAME or capability_directory is None:
        return tool.description
    return f"{tool.description}\n\n{capability_directory}"


@dataclass(frozen=True, slots=True)
class InternalAgentVoiceToolProvider:
    memory_service: AgentMemoryService
    skill_service: AgentSkillService
    planning_service: AgentPlanningService
    contact_session_factory: AsyncSessionFactory | None = None
    product_docs_service: ProductDocSearchService | None = None
    authorization_service: ToolAuthorizationService | None = None
    history_service: TenantHistoryService | None = None
    improvement_service: AgentImprovementService | None = None

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        return await self._tools_for_scope(user_id=user_id, scope=None)

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        return await self._tools_for_scope(user_id=scope.owner_user_id, scope=scope)

    async def _tools_for_scope(
        self,
        *,
        user_id: str,
        scope: AgentScope | None,
    ) -> Sequence[VoiceLiveKitTool]:
        if scope is None:
            return ()
        tools = InternalAgentToolExecutor(
            execution_context=ToolExecutionContext(
                user_id=scope.owner_user_id,
                agent_id=scope.agent_id,
                session_id=scope.session_id,
                run_id=scope.run_id,
                channel=scope.channel,
                source="voice.internal_tools",
            ),
            memory_service=self.memory_service,
            skill_service=self.skill_service,
            planning_service=self.planning_service,
            contact_session_factory=self.contact_session_factory,
            product_docs_service=self.product_docs_service,
            authorization_service=self.authorization_service,
            history_service=self.history_service,
            improvement_service=self.improvement_service,
        )
        tool_names = tuple(
            name
            for name in internal_tool_functions(tools)
            if name not in {"confirm_agent_memory", "confirm_skill_proposal"}
        )
        if self.history_service is None:
            tool_names = tuple(name for name in tool_names if name not in ACCOUNT_LEARNING_TOOLS)
        else:
            async with self.history_service.session_factory() as db:
                for name in ACCOUNT_LEARNING_TOOLS:
                    try:
                        await self.history_service.validate_access_in_session(db, tools.execution_context, name)
                    except PermissionError:
                        tool_names = tuple(tool_name for tool_name in tool_names if tool_name != name)

        if self.authorization_service is not None and scope is not None:
            tool_names = await self.authorization_service.allowed_tool_names(
                scope=scope,
                tool_source=ToolGrantScope.INTERNAL,
                tool_names=tool_names,
            )
        return tuple(_livekit_tool(tool_name, tools) for tool_name in tool_names)


@dataclass(frozen=True, slots=True)
class CompositeVoiceToolProvider:
    providers: tuple[VoiceToolProvider, ...]

    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        toolsets = await asyncio.gather(
            *(provider.get_tools(user_id) for provider in self.providers),
        )
        return tuple(tool for provider_tools in toolsets for tool in provider_tools)

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> Sequence[VoiceLiveKitTool]:
        contributions = await asyncio.gather(
            *(
                self._provider_contribution_for_scope(
                    provider,
                    scope=scope,
                    effective_tool_policy=effective_tool_policy,
                )
                for provider in self.providers
            ),
        )
        direct_tools = tuple(tool for contribution in contributions for tool in contribution.direct_tools)
        catalog_tools = tuple(tool for contribution in contributions for tool in contribution.catalog_tools)
        catalog = RunScopedVoiceToolCatalog.create(catalog_tools)
        return (*direct_tools, *catalog.visible_tools())

    @staticmethod
    async def _provider_contribution_for_scope(
        provider: VoiceToolProvider,
        *,
        scope: AgentScope,
        effective_tool_policy: EffectiveToolPolicySnapshot | None,
    ) -> "VoiceToolContribution":
        if isinstance(provider, ScopedVoiceCatalogToolProvider):
            catalog_tools = await provider.get_catalog_tools_for_scope(
                scope,
                effective_tool_policy=effective_tool_policy,
            )
            return VoiceToolContribution(catalog_tools=tuple(catalog_tools))
        if isinstance(provider, ScopedVoiceToolProvider):
            return VoiceToolContribution(
                direct_tools=tuple(
                    await provider.get_tools_for_scope(
                        scope,
                        effective_tool_policy=effective_tool_policy,
                    ),
                ),
            )
        return VoiceToolContribution(
            direct_tools=tuple(await provider.get_tools(scope.owner_user_id)),
        )


@dataclass(frozen=True, slots=True)
class VoiceToolContribution:
    direct_tools: tuple[VoiceLiveKitTool, ...] = ()
    catalog_tools: tuple[VoiceCatalogTool, ...] = ()


def realtime_tool_parameters_schema(schema: JsonObject) -> JsonObject:
    """Present a Realtime-compatible object schema without weakening execution checks."""

    properties = schema.get("properties")
    safe_properties: JsonObject = dict(properties) if isinstance(properties, dict) else {}
    provider_schema: JsonObject = {
        str(key): value for key, value in schema.items() if key not in REALTIME_UNSUPPORTED_ROOT_SCHEMA_KEYS
    }
    provider_schema["type"] = "object"
    provider_schema["properties"] = safe_properties
    required = provider_schema.get("required")
    if isinstance(required, list):
        provider_schema["required"] = [item for item in required if isinstance(item, str) and item in safe_properties]
    return provider_schema


def route_voice_mcp_tools(
    *,
    configs: tuple[UserMcpToolsetConfig, ...],
    allowed_by_server_id: dict[str, tuple[str, ...]],
    effective_tool_policy: EffectiveToolPolicySnapshot | None,
) -> dict[str, tuple[str, ...]]:
    routed_candidates: dict[
        str,
        list[tuple[int, UserMcpToolsetConfig, str]],
    ] = {}
    for config in configs:
        for tool_name in allowed_by_server_id.get(config.server_id, ()):
            route = mcp_preset_tool_route(config.preset_id, tool_name)
            if route is None:
                continue
            action, priority = route
            routed_candidates.setdefault(action, []).append(
                (priority, config, tool_name),
            )
    resolved_by_action = (
        {}
        if effective_tool_policy is None
        else {route.action: route for route in effective_tool_policy.resolved_routes}
    )
    selected: set[tuple[str, str]] = set()
    for action, candidates in routed_candidates.items():
        resolved = resolved_by_action.get(action)
        if resolved is not None:
            reference = resolved.selected_tool
            if reference is not None and reference.mcp_server_id is not None:
                selected.add((reference.mcp_server_id, reference.tool_name))
            continue
        _, config, tool_name = min(
            candidates,
            key=lambda candidate: (
                candidate[0],
                candidate[1].server_id,
                candidate[2],
            ),
        )
        selected.add((config.server_id, tool_name))

    return {
        config.server_id: tuple(
            tool_name
            for tool_name in allowed_by_server_id.get(config.server_id, ())
            if (
                (route := mcp_preset_tool_route(config.preset_id, tool_name)) is None
                or (config.server_id, tool_name) in selected
            )
        )
        for config in configs
    }


def _livekit_tool(
    tool_name: str,
    executor: InternalAgentToolExecutor,
) -> VoiceLiveKitTool:
    async def call_internal_tool(raw_arguments: dict[str, object]) -> object:
        arguments_json: JsonObject = raw_arguments
        await executor.validate_arguments(
            tool_name=tool_name,
            arguments_json=arguments_json,
        )
        return await execute_internal_tool(
            executor=executor,
            tool_name=tool_name,
            arguments_json=arguments_json,
        )

    call_internal_tool.__name__ = tool_name
    return cast(
        VoiceLiveKitTool,
        function_tool(
            call_internal_tool,
            raw_schema={
                "name": tool_name,
                "description": INTERNAL_TOOL_DESCRIPTION_BY_NAME[tool_name],
                "parameters": INTERNAL_TOOL_SCHEMA_BY_NAME[tool_name],
            },
        ),
    )

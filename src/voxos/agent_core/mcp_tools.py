import asyncio
import logging
import re
from dataclasses import dataclass, field, replace
from hashlib import sha256

import httpx
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_core.mcp_tool_cache import (
    McpToolCatalogCache,
    McpToolCatalogCacheKey,
)
from voxos.agent_core.models import (
    AgentRunContext,
    AgentTool,
    AgentToolActivity,
    AgentToolProvider,
    AgentToolResult,
)
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_execution import (
    ToolExecutionContext,
    ToolExecutionSecurityError,
    is_authority_argument_key,
    prune_unsupported_tool_arguments,
    validate_llm_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.logos.models import ActivityIcon
from voxos.mcp.client import (
    DiscoveredMcpCapabilities,
    DiscoveredMcpTool,
    McpClientConnectionConfig,
    SdkUserMcpClient,
    UserMcpClient,
)
from voxos.mcp.delegation import (
    McpCredentialDelegationSigner,
    credential_delegation_signer_from_settings,
)
from voxos.mcp.errors import McpServerNotFoundError, McpToolCallRejectedError
from voxos.mcp.models import McpDiscoveredToolDefinition
from voxos.mcp.oauth import McpOAuthHttpClient
from voxos.mcp.presets import (
    GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID,
    GOOGLE_MAPS_SEARCH_PLACES_TOOL,
    adapt_mcp_preset_tool_schema,
    mcp_preset_tool_activity,
    mcp_preset_tool_authority_argument_allowed_paths,
    mcp_preset_tool_description_guidance,
    mcp_preset_tool_is_read_only,
    mcp_preset_tool_rejection_guidance,
    mcp_preset_tool_risk,
    mcp_preset_tool_route,
    normalize_mcp_preset_tool_arguments,
)
from voxos.mcp.services import UserMcpServerService, UserMcpToolsetConfig
from voxos.mcp.settings import McpSettings
from voxos.place_cards.google_maps import (
    append_google_maps_sources_for_model,
    google_maps_search_place_cards,
)
from voxos.place_cards.models import PlaceCardArtifact

USER_MCP_HTTP_CLIENT = httpx.AsyncClient()
MCP_PROVIDER_TOOL_PREFIX = "mcp"
MCP_PROVIDER_TOOL_NAME_MAX_LENGTH = 64
UNSAFE_TOOL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
logger = logging.getLogger(__name__)


def mcp_runtime_tool_risk(preset_id: str | None, tool_name: str) -> ToolRisk:
    return ToolRisk(mcp_preset_tool_risk(preset_id, tool_name).value)


@dataclass(frozen=True, slots=True)
class CompositeAgentToolProvider:
    providers: tuple[AgentToolProvider, ...]

    async def prepare(self, context: AgentRunContext) -> "PreparedAgentToolProvider":
        toolsets = await asyncio.gather(
            *(provider.list_tools(context) for provider in self.providers),
        )
        return PreparedAgentToolProvider(
            bindings=tuple(
                PreparedAgentToolBinding(tool=tool, provider=provider)
                for provider, tools in zip(self.providers, toolsets, strict=True)
                for tool in tools
            ),
        )

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        prepared = await self.prepare(context)
        return await prepared.list_tools(context)

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        prepared = await self.prepare(context)
        return await prepared.execute_tool(
            context=context,
            tool_name=tool_name,
            call_id=call_id,
            arguments_json=arguments_json,
        )


@dataclass(frozen=True, slots=True)
class PreparedAgentToolBinding:
    tool: AgentTool
    provider: AgentToolProvider


@dataclass(frozen=True, slots=True)
class PreparedAgentToolProvider:
    bindings: tuple[PreparedAgentToolBinding, ...]

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        del context
        return tuple(binding.tool for binding in self.bindings)

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        binding = next(
            (binding for binding in self.bindings if binding.tool.name == tool_name),
            None,
        )
        if binding is None:
            message = f"Tool is not available to this agent: {tool_name}"
            raise PermissionError(message)
        return await binding.provider.execute_tool(
            context=context,
            tool_name=tool_name,
            call_id=call_id,
            arguments_json=arguments_json,
        )


async def prepare_agent_tool_provider(
    provider: AgentToolProvider,
    context: AgentRunContext,
) -> AgentToolProvider:
    if isinstance(provider, CompositeAgentToolProvider):
        return await provider.prepare(context)
    return provider


@dataclass(frozen=True, slots=True)
class UserMcpAgentToolProvider:
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    authorization_service: ToolAuthorizationService | None = None
    http_client: httpx.AsyncClient | None = None
    mcp_client: UserMcpClient = field(default_factory=SdkUserMcpClient.from_settings)
    tool_catalog_cache: McpToolCatalogCache | None = None
    execution_source_prefix: str = "agent_core.mcp_tools"
    execution_channel: str = "text"
    require_run_for_execution: bool = True
    credential_delegation_signer: McpCredentialDelegationSigner | None = field(
        default_factory=lambda: credential_delegation_signer_from_settings(McpSettings())
    )

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        if self.tool_catalog_cache is None:
            return await self._list_tools_uncached(context)
        return await self.tool_catalog_cache.get_or_load(
            McpToolCatalogCacheKey.from_context(context),
            lambda: self._list_tools_uncached(context),
        )

    async def _list_tools_uncached(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        toolset_configs = await self._toolset_configs(context.user_id)
        execution_context = self._execution_context(
            context=context,
            source=f"{self.execution_source_prefix}.list",
            require_run=False,
        )
        allowed_tool_name_sets = await asyncio.gather(
            *(
                self._allowed_tool_names(
                    execution_context=execution_context,
                    config=config,
                )
                for config in toolset_configs
            ),
        )
        tools: list[AgentTool] = []
        for config, allowed_tool_names in zip(
            toolset_configs,
            allowed_tool_name_sets,
            strict=True,
        ):
            if not allowed_tool_names:
                continue
            definitions_by_name = {
                definition.name: cached_mcp_tool_definition(definition) for definition in config.tool_definitions
            }
            missing_tool_names = tuple(
                tool_name for tool_name in allowed_tool_names if tool_name not in definitions_by_name
            )
            if missing_tool_names:
                capabilities = await self.mcp_client.discover_capabilities(
                    connection_config(config),
                )
                definitions_by_name.update(
                    (definition.name, definition) for definition in capabilities.tool_definitions
                )
                await self._cache_discovered_capabilities(
                    user_id=context.user_id,
                    config=config,
                    capabilities=capabilities,
                )
            for tool_name in allowed_tool_names:
                definition = definitions_by_name.get(tool_name)
                if definition is None:
                    logger.warning(
                        "allowed MCP tool was not returned by capability discovery: server_id=%s tool_name=%s",
                        config.server_id,
                        tool_name,
                    )
                    continue
                tools.append(agent_tool_from_mcp_definition(config, tool_name, definition))
        return tuple(tools)

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        execution_context = self._execution_context(
            context=context,
            source=f"{self.execution_source_prefix}.execute",
            require_run=self.require_run_for_execution,
        )
        descriptor = await self._resolve_tool_descriptor(
            user_id=execution_context.user_id,
            provider_tool_name=tool_name,
        )
        try:
            validate_llm_tool_arguments(
                arguments_json,
                allowed_paths=mcp_preset_tool_authority_argument_allowed_paths(
                    descriptor.config.preset_id,
                    descriptor.original_tool_name,
                ),
            )
        except ToolExecutionSecurityError as exc:
            await self._audit_argument_denial(
                execution_context=execution_context,
                descriptor=descriptor,
                reason=str(exc),
            )
            raise
        await self._authorize_tool(
            execution_context=execution_context,
            config=descriptor.config,
            tool_name=descriptor.original_tool_name,
        )
        normalized_arguments = normalize_mcp_preset_tool_arguments(
            descriptor.config.preset_id,
            descriptor.original_tool_name,
            arguments_json,
        )
        definition = next(
            (
                cached_mcp_tool_definition(candidate)
                for candidate in descriptor.config.tool_definitions
                if candidate.name == descriptor.original_tool_name
            ),
            None,
        )
        parameters_schema = model_safe_mcp_tool_schema(
            descriptor.config,
            descriptor.original_tool_name,
            definition,
        )
        accepted_arguments = prune_unsupported_tool_arguments(
            normalized_arguments,
            parameters_schema,
        )
        validate_tool_arguments_schema(accepted_arguments, parameters_schema)
        try:
            result = await self.mcp_client.call_tool(
                config=replace(
                    connection_config(descriptor.config),
                    retry_safe=(
                        mcp_preset_tool_is_read_only(
                            descriptor.config.preset_id,
                            descriptor.original_tool_name,
                        )
                        or (definition is not None and definition.read_only_hint)
                    ),
                ),
                tool_name=descriptor.original_tool_name,
                arguments=accepted_arguments,
            )
        except McpToolCallRejectedError:
            raise McpToolCallRejectedError(
                recovery_guidance=mcp_preset_tool_rejection_guidance(
                    descriptor.config.preset_id,
                    descriptor.original_tool_name,
                ),
            ) from None
        place_card_artifacts: tuple[PlaceCardArtifact, ...] = ()
        output_text = result.content_text
        persisted_output_text: str | None = None
        is_google_maps_place_search = (
            descriptor.config.preset_id == GOOGLE_MAPS_GROUNDING_LITE_PRESET_ID
            and descriptor.original_tool_name == GOOGLE_MAPS_SEARCH_PLACES_TOOL
        )
        if is_google_maps_place_search:
            persisted_output_text = (
                "Google Maps place search failed." if result.is_error else "Google Maps place search completed."
            )
            if not result.is_error:
                place_card_artifacts = google_maps_search_place_cards(
                    structured_content=result.structured_content,
                    content_text=result.content_text,
                )
                output_text = append_google_maps_sources_for_model(
                    result.content_text,
                    place_card_artifacts,
                )
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=output_text,
            persisted_output_text=persisted_output_text,
            is_error=result.is_error,
            raw_output=result.structured_content or {},
            executed_arguments_json=accepted_arguments,
            place_card_artifacts=place_card_artifacts,
        )

    async def _toolset_configs(
        self,
        user_id: str,
    ) -> tuple[UserMcpToolsetConfig, ...]:
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
            return await service.enabled_toolset_configs(user_id)

    async def _cache_discovered_capabilities(
        self,
        *,
        user_id: str,
        config: UserMcpToolsetConfig,
        capabilities: DiscoveredMcpCapabilities,
    ) -> None:
        try:
            async with self.session_factory() as session:
                service = UserMcpServerService(
                    session=session,
                    secret_vault=self.secret_vault,
                    mcp_client=self.mcp_client,
                    oauth_client=McpOAuthHttpClient(
                        self.http_client or USER_MCP_HTTP_CLIENT,
                    ),
                    credential_delegation_signer=self.credential_delegation_signer,
                )
                await service.cache_discovered_capabilities(
                    user_id=user_id,
                    server_id=config.server_id,
                    expected_url=config.url,
                    expected_transport=config.transport,
                    capabilities=capabilities,
                )
        except (McpServerNotFoundError, SQLAlchemyError):
            logger.exception(
                "failed to cache discovered MCP capabilities: server_id=%s",
                config.server_id,
            )

    async def _allowed_tool_names(
        self,
        *,
        execution_context: ToolExecutionContext,
        config: UserMcpToolsetConfig,
    ) -> tuple[str, ...]:
        if self.authorization_service is None:
            return config.allowed_tools
        return await self.authorization_service.allowed_tool_names(
            scope=execution_context.to_agent_scope(),
            tool_source=ToolGrantScope.MCP_SERVER,
            tool_names=config.allowed_tools,
            mcp_server_id=config.server_id,
        )

    async def _authorize_tool(
        self,
        *,
        execution_context: ToolExecutionContext,
        config: UserMcpToolsetConfig,
        tool_name: str,
    ) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=tool_name,
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=config.server_id,
                risk=mcp_runtime_tool_risk(config.preset_id, tool_name),
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    async def _audit_argument_denial(
        self,
        *,
        execution_context: ToolExecutionContext,
        descriptor: "ResolvedUserMcpTool",
        reason: str,
    ) -> None:
        if self.authorization_service is None:
            return
        await self.authorization_service.record_tool_audit_event(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=descriptor.original_tool_name,
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=descriptor.config.server_id,
                risk=mcp_runtime_tool_risk(
                    descriptor.config.preset_id,
                    descriptor.original_tool_name,
                ),
            ),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_arguments"},
        )

    async def _resolve_tool_descriptor(
        self,
        *,
        user_id: str,
        provider_tool_name: str,
    ) -> "ResolvedUserMcpTool":
        return UserMcpToolCallResolver(
            configs=await self._toolset_configs(user_id),
        ).resolve(provider_tool_name)

    def _execution_context(
        self,
        *,
        context: AgentRunContext,
        source: str,
        require_run: bool,
    ) -> ToolExecutionContext:
        return ToolExecutionContext.from_run_context(
            context,
            source=source,
            require_run=require_run,
            channel=self.execution_channel,
        )


@dataclass(frozen=True, slots=True)
class ResolvedUserMcpTool:
    config: UserMcpToolsetConfig
    provider_tool_name: str
    original_tool_name: str


@dataclass(frozen=True, slots=True)
class UserMcpToolCallResolver:
    configs: tuple[UserMcpToolsetConfig, ...]

    def resolve(self, provider_tool_name: str) -> ResolvedUserMcpTool:
        for config in self.configs:
            original_tool_name = original_mcp_tool_name(
                config=config,
                provider_tool_name=provider_tool_name,
            )
            if original_tool_name is not None:
                return ResolvedUserMcpTool(
                    config=config,
                    provider_tool_name=provider_tool_name,
                    original_tool_name=original_tool_name,
                )
        message = f"MCP tool is not enabled for this user: {provider_tool_name}"
        raise PermissionError(message)


def connection_config(config: UserMcpToolsetConfig) -> McpClientConnectionConfig:
    return McpClientConnectionConfig(
        url=config.url,
        transport=config.transport,
        headers=config.headers,
    )


def agent_tool_from_mcp_definition(
    config: UserMcpToolsetConfig,
    tool_name: str,
    definition: DiscoveredMcpTool | None,
) -> AgentTool:
    provider_name = provider_mcp_tool_name(config=config, tool_name=tool_name)
    route = mcp_preset_tool_route(config.preset_id, tool_name)
    activity_label, activity_icon = mcp_preset_tool_activity(
        config.preset_id,
        tool_name,
    )
    return AgentTool(
        name=provider_name,
        description=tool_description(config=config, tool_name=tool_name, definition=definition),
        parameters_schema=model_safe_mcp_tool_schema(config, tool_name, definition),
        source=ToolGrantScope.MCP_SERVER,
        risk=mcp_runtime_tool_risk(config.preset_id, tool_name),
        parallel_safe=(
            mcp_preset_tool_is_read_only(config.preset_id, tool_name)
            or (definition is not None and definition.read_only_hint)
        ),
        mcp_server_id=config.server_id,
        source_tool_name=tool_name,
        catalog_label=config.label,
        capability_key=config.preset_id,
        activity=AgentToolActivity(
            label=(activity_label or f"Using {config.label}")[:128],
            detail=config.label if activity_label is not None else tool_name,
            icon=activity_icon or ActivityIcon.MCP,
            logo=config.logo,
        ),
        route_action=None if route is None else route[0],
        automatic_route_priority=None if route is None else route[1],
        authority_argument_allowed_paths=(
            mcp_preset_tool_authority_argument_allowed_paths(
                config.preset_id,
                tool_name,
            )
        ),
    )


def model_safe_mcp_tool_schema(
    config: UserMcpToolsetConfig,
    tool_name: str,
    definition: DiscoveredMcpTool | None,
) -> JsonObject:
    if definition is None:
        return {}
    adapted = adapt_mcp_preset_tool_schema(
        config.preset_id,
        tool_name,
        definition.input_schema,
    )
    allowed_paths = mcp_preset_tool_authority_argument_allowed_paths(
        config.preset_id,
        tool_name,
    )
    sanitized = _sanitize_schema_value(adapted, (), allowed_paths)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_schema_value(
    value: object,
    path: tuple[str, ...],
    allowed_paths: frozenset[tuple[str, ...]],
) -> object:
    if isinstance(value, list):
        return [_sanitize_schema_value(item, path, allowed_paths) for item in value]
    if not isinstance(value, dict):
        return value
    properties = value.get("properties")
    sanitized_properties: dict[str, object] | None = None
    removed_properties: set[str] = set()
    if isinstance(properties, dict):
        sanitized_properties = {}
        for key, child in properties.items():
            key_text = str(key)
            child_path = (*path, key_text)
            if is_authority_argument_key(key_text) and child_path not in allowed_paths:
                removed_properties.add(key_text)
                continue
            sanitized_properties[key_text] = _sanitize_schema_value(
                child,
                child_path,
                allowed_paths,
            )
    sanitized: dict[str, object] = {}
    for key, child in value.items():
        if key == "properties" and sanitized_properties is not None:
            sanitized[key] = sanitized_properties
        elif key == "required" and isinstance(child, list):
            sanitized[key] = [item for item in child if item not in removed_properties]
        elif key == "items":
            sanitized[key] = _sanitize_schema_value(
                child,
                (*path, "*"),
                allowed_paths,
            )
        elif key == "prefixItems" and isinstance(child, list):
            sanitized[key] = [_sanitize_schema_value(item, (*path, "*"), allowed_paths) for item in child]
        else:
            sanitized[key] = _sanitize_schema_value(child, path, allowed_paths)
    return sanitized


def cached_mcp_tool_definition(
    definition: McpDiscoveredToolDefinition,
) -> DiscoveredMcpTool:
    guidance = definition.guidance
    return DiscoveredMcpTool(
        name=definition.name,
        description=definition.description,
        input_schema=definition.input_schema,
        read_only_hint=False if guidance is None else guidance.read_only,
    )


def original_mcp_tool_name(
    *,
    config: UserMcpToolsetConfig,
    provider_tool_name: str,
) -> str | None:
    for tool_name in config.allowed_tools:
        if provider_mcp_tool_name(config=config, tool_name=tool_name) == provider_tool_name:
            return tool_name
    return None


def provider_mcp_tool_name(*, config: UserMcpToolsetConfig, tool_name: str) -> str:
    safe_tool_name = safe_provider_tool_suffix(tool_name)
    digest = sha256(f"{config.server_id}:{tool_name}".encode("utf-8")).hexdigest()[:10]
    prefix = f"{MCP_PROVIDER_TOOL_PREFIX}_{digest}_"
    suffix_length = MCP_PROVIDER_TOOL_NAME_MAX_LENGTH - len(prefix)
    return f"{prefix}{safe_tool_name[:suffix_length]}"


def safe_provider_tool_suffix(tool_name: str) -> str:
    safe_name = UNSAFE_TOOL_NAME_CHARS.sub("_", tool_name).strip("_")
    return safe_name or "tool"


def tool_description(
    *,
    config: UserMcpToolsetConfig,
    tool_name: str,
    definition: DiscoveredMcpTool | None,
) -> str:
    description = tool_name if definition is None else definition.description
    server_label = config.label.strip() or config.server_id
    guidance = mcp_preset_tool_description_guidance(config.preset_id, tool_name)
    guidance_prefix = "" if guidance is None else f"{guidance} "
    return f"{tool_name} from {server_label} MCP server: {guidance_prefix}{description}"[:1024]

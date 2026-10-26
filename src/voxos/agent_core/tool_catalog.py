import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

import orjson

from voxos.agent_core.models import (
    AgentRunContext,
    AgentTool,
    AgentToolProvider,
    AgentToolResult,
)
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_execution import (
    prune_unsupported_tool_arguments,
    validate_llm_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolReference, ToolRisk
from voxos.mcp.presets import normalize_mcp_preset_tool_arguments

TOOL_SEARCH_NAME = "tool_search"
TOOL_DESCRIBE_NAME = "tool_describe"
TOOL_CALL_NAME = "tool_call"
TOOL_SEARCH_ACTIVATION_COUNT = 10
TOOL_SEARCH_ACTIVATION_SCHEMA_BYTES = 40_000
TOOL_SEARCH_MAX_RESULTS = 10
TOOL_CATALOG_DIRECTORY_MAX_CHARACTERS = 4_000
TOOL_CATALOG_DIRECTORY_MAX_TOOL_NAMES_PER_GROUP = 8
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
TOOL_CALL_ARGUMENTS_ERROR = "tool_call arguments must be a JSON object"
TOOL_ID_UNAVAILABLE_ERROR = "Tool ID is not available in this run"
TOOL_LIMIT_TYPE_ERROR = "limit must be an integer"
HARMONY_TOOL_NAME_CHANNEL_SUFFIXES = (
    "<|channel|>analysis",
    "<|channel|>commentary",
    "<|channel|>final",
)


class ToolNotExposedError(PermissionError):
    """A model-selected tool name is not available in the current run catalog."""


@dataclass(frozen=True, slots=True)
class CatalogTool:
    id: str
    tool: AgentTool


@dataclass(frozen=True, slots=True)
class RunScopedToolCatalogProvider:
    provider: AgentToolProvider
    routed_tools: tuple[AgentTool, ...]
    visible_tools: tuple[AgentTool, ...]
    catalog_tools: tuple[CatalogTool, ...]
    deferred_catalog_tools: tuple[CatalogTool, ...]
    catalog_tools_by_id: Mapping[str, CatalogTool]
    search_enabled: bool

    @classmethod
    async def create(
        cls,
        *,
        provider: AgentToolProvider,
        context: AgentRunContext,
    ) -> "RunScopedToolCatalogProvider":
        available_tools = await provider.list_tools(context)
        routed_tools = filter_tools_by_resolved_routes(
            available_tools,
            context.effective_tool_policy,
        )
        search_enabled = should_enable_tool_search(routed_tools)
        catalog_tools = tuple(
            CatalogTool(id=catalog_tool_id(tool), tool=tool) for tool in routed_tools if not tool.required
        )
        if search_enabled:
            eager_tools = tuple(tool for tool in routed_tools if is_eager_tool(tool))
            deferred_catalog_tools = tuple(
                catalog_tool for catalog_tool in catalog_tools if not is_eager_tool(catalog_tool.tool)
            )
            visible_tools = (*eager_tools, *tool_catalog_bridge_tools())
        else:
            deferred_catalog_tools = ()
            visible_tools = routed_tools
        return cls(
            provider=provider,
            routed_tools=routed_tools,
            visible_tools=visible_tools,
            catalog_tools=catalog_tools,
            deferred_catalog_tools=deferred_catalog_tools,
            catalog_tools_by_id=MappingProxyType(
                {catalog_tool.id: catalog_tool for catalog_tool in catalog_tools},
            ),
            search_enabled=search_enabled,
        )

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        return self.visible_tools

    def deferred_capability_directory(self) -> str | None:
        if not self.search_enabled:
            return None
        return render_deferred_capability_directory(self.deferred_catalog_tools)

    def activity_tool_for_call(
        self,
        *,
        tool_name: str,
        arguments_json: JsonObject,
    ) -> AgentTool | None:
        """Resolve presentation metadata without executing or rediscovering tools."""

        if not self.search_enabled:
            return None
        if tool_name == TOOL_CALL_NAME:
            tool_id = arguments_json.get("tool_id")
            if not isinstance(tool_id, str):
                return None
            catalog_tool = self.catalog_tools_by_id.get(tool_id)
            return None if catalog_tool is None else catalog_tool.tool
        catalog_tool = self._resolve_catalog_tool_alias(tool_name)
        return None if catalog_tool is None else catalog_tool.tool

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        if self.search_enabled and tool_name == TOOL_SEARCH_NAME:
            return self._search(call_id=call_id, arguments_json=arguments_json)
        if self.search_enabled and tool_name == TOOL_DESCRIBE_NAME:
            return self._describe(call_id=call_id, arguments_json=arguments_json)
        if self.search_enabled and tool_name == TOOL_CALL_NAME:
            return await self._call(
                context=context,
                call_id=call_id,
                arguments_json=arguments_json,
            )
        if self.search_enabled:
            catalog_tool = self._resolve_catalog_tool_alias(tool_name)
            if catalog_tool is not None:
                return await self._execute_catalog_tool(
                    context=context,
                    call_id=call_id,
                    catalog_tool=catalog_tool,
                    arguments_json=arguments_json,
                )
        if not any(tool.name == tool_name for tool in self.visible_tools):
            message = f"Tool is not exposed in this run: {tool_name}"
            raise ToolNotExposedError(message)
        return await self.provider.execute_tool(
            context=context,
            tool_name=tool_name,
            call_id=call_id,
            arguments_json=arguments_json,
        )

    def _search(
        self,
        *,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        query = required_string(arguments_json, "query")
        limit = bounded_int(
            arguments_json.get("limit"),
            default=5,
            minimum=1,
            maximum=TOOL_SEARCH_MAX_RESULTS,
        )
        matches = search_catalog(self.catalog_tools, query=query, limit=limit)
        results: list[JsonObject] = []
        for match in matches:
            tool = match.catalog_tool.tool
            call_by_name = any(visible.name == tool.name for visible in self.visible_tools)
            result: JsonObject = {
                "tool_id": match.catalog_tool.id,
                "name": tool.name,
                "action": tool.route_action,
                "description": compact_text(tool.description, limit=320),
                "callable": True,
                "call_by_name": call_by_name,
                "invoke_with": tool.name if call_by_name else TOOL_CALL_NAME,
                "score": round(match.score, 6),
            }
            if not call_by_name:
                result["describe_with"] = TOOL_DESCRIBE_NAME
            results.append(result)
        payload: JsonObject = {
            "query": query,
            "results": results,
        }
        return json_tool_result(
            call_id=call_id,
            name=TOOL_SEARCH_NAME,
            payload=payload,
        )

    def _describe(
        self,
        *,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        catalog_tool = self._require_catalog_tool(
            required_string(arguments_json, "tool_id"),
        )
        tool = catalog_tool.tool
        payload: JsonObject = {
            "tool_id": catalog_tool.id,
            "name": tool.name,
            "description": tool.description,
            "action": tool.route_action,
            "parameters_schema": tool.parameters_schema,
        }
        return json_tool_result(
            call_id=call_id,
            name=TOOL_DESCRIBE_NAME,
            payload=payload,
        )

    async def _call(
        self,
        *,
        context: AgentRunContext,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        catalog_tool = self._require_catalog_tool(
            required_string(arguments_json, "tool_id"),
        )
        target_arguments = arguments_json.get("arguments", {})
        if not isinstance(target_arguments, dict):
            raise ValueError(TOOL_CALL_ARGUMENTS_ERROR)
        target_arguments_json: JsonObject = {str(key): value for key, value in target_arguments.items()}
        return await self._execute_catalog_tool(
            context=context,
            call_id=call_id,
            catalog_tool=catalog_tool,
            arguments_json=target_arguments_json,
        )

    async def _execute_catalog_tool(
        self,
        *,
        context: AgentRunContext,
        call_id: str,
        catalog_tool: CatalogTool,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        target = catalog_tool.tool
        target_arguments_json = normalize_mcp_preset_tool_arguments(
            target.capability_key,
            target.source_tool_name or target.name,
            arguments_json,
        )
        validate_llm_tool_arguments(
            target_arguments_json,
            allowed_paths=target.authority_argument_allowed_paths,
        )
        target_arguments_json = prune_unsupported_tool_arguments(
            target_arguments_json,
            target.parameters_schema,
        )
        validate_tool_arguments_schema(
            target_arguments_json,
            target.parameters_schema,
        )
        result = await self.provider.execute_tool(
            context=context,
            tool_name=target.name,
            call_id=call_id,
            arguments_json=target_arguments_json,
        )
        return result.model_copy(
            update={"executed_arguments_json": target_arguments_json},
        )

    def _resolve_catalog_tool_alias(self, tool_name: str) -> CatalogTool | None:
        normalized_name = strip_harmony_tool_name_channel_suffix(tool_name)
        exact = next(
            (item for item in self.catalog_tools if item.tool.name == normalized_name),
            None,
        )
        if exact is not None:
            return exact
        source_matches = tuple(item for item in self.catalog_tools if item.tool.source_tool_name == normalized_name)
        if len(source_matches) == 1:
            return source_matches[0]
        return None

    def _require_catalog_tool(self, tool_id: str) -> CatalogTool:
        catalog_tool = self.catalog_tools_by_id.get(tool_id)
        if catalog_tool is None:
            raise PermissionError(TOOL_ID_UNAVAILABLE_ERROR)
        return catalog_tool


@dataclass(frozen=True, slots=True)
class ToolSearchMatch:
    catalog_tool: CatalogTool
    score: float


def filter_tools_by_resolved_routes(
    tools: tuple[AgentTool, ...],
    policy: EffectiveToolPolicySnapshot | None,
) -> tuple[AgentTool, ...]:
    routed_actions = {tool.route_action for tool in tools if tool.route_action is not None}
    if not routed_actions:
        return tools
    resolved_by_action = {} if policy is None else {route.action: route for route in policy.resolved_routes}
    fallback_by_action = {
        action: automatic_tool_for_action(tools, action=action)
        for action in routed_actions
        if action not in resolved_by_action
    }
    filtered: list[AgentTool] = []
    for tool in tools:
        action = tool.route_action
        if action is None:
            filtered.append(tool)
            continue
        route = resolved_by_action.get(action)
        if route is not None:
            if route.selected_tool is not None and agent_tool_matches_reference(
                tool,
                route.selected_tool,
            ):
                filtered.append(tool)
            continue
        fallback = fallback_by_action.get(action)
        if fallback is not None and fallback.name == tool.name:
            filtered.append(tool)
    return tuple(filtered)


def automatic_tool_for_action(
    tools: tuple[AgentTool, ...],
    *,
    action: str,
) -> AgentTool | None:
    candidates = tuple(
        tool for tool in tools if tool.route_action == action and tool.automatic_route_priority is not None
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda tool: (
            tool.automatic_route_priority or 0,
            tool.source.value,
            tool.mcp_server_id or "",
            tool.integration_id or "",
            tool.source_tool_name or tool.name,
        ),
    )


def agent_tool_matches_reference(tool: AgentTool, reference: ToolReference) -> bool:
    return (
        (tool.source_tool_name or tool.name) == reference.tool_name
        and tool.source == reference.tool_source
        and tool.mcp_server_id == reference.mcp_server_id
        and tool.integration_id == reference.integration_id
    )


def is_eager_tool(tool: AgentTool) -> bool:
    """Report whether a tool stays directly callable instead of deferring behind tool_search.

    Explicitly visible and required tools are always exposed. Routed tools are too:
    a route action names a canonical capability such as ``web.search``, and the
    routing filter has already reduced each action to a single winner, so hiding it
    behind discovery would add hops without shrinking the schema payload.
    """

    return tool.always_visible or tool.required or tool.route_action is not None


def should_enable_tool_search(tools: tuple[AgentTool, ...]) -> bool:
    deferred_tools = tuple(tool for tool in tools if not is_eager_tool(tool))
    if len(deferred_tools) >= TOOL_SEARCH_ACTIVATION_COUNT:
        return True
    schema_bytes = sum(len(orjson.dumps(tool.parameters_schema)) for tool in deferred_tools)
    return schema_bytes >= TOOL_SEARCH_ACTIVATION_SCHEMA_BYTES


def tool_catalog_bridge_tools() -> tuple[AgentTool, ...]:
    return (
        AgentTool(
            name=TOOL_SEARCH_NAME,
            description=(
                "Find authorized capabilities available in this run. This searches tools, "
                "not external facts or web content. "
                "When you are unsure whether a capability exists or which tool can perform "
                "the request, always use this before answering. "
                "When the user names a service, tool family, or specialized capability, "
                "or asks whether one is available, use this before claiming it is unavailable "
                "or substituting a generic tool. Every returned result is authorized and "
                "callable. Follow invoke_with: call a result by name when call_by_name is true; "
                "otherwise use its tool_id with tool_describe and tool_call."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
            parallel_safe=True,
        ),
        AgentTool(
            name=TOOL_DESCRIBE_NAME,
            description=("Return the description and full input schema for one tool_search result."),
            parameters_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "minLength": 1},
                },
                "required": ["tool_id"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
            parallel_safe=True,
        ),
        AgentTool(
            name=TOOL_CALL_NAME,
            description=(
                "Execute one authorized tool_search result. Supply its tool_id and "
                "arguments matching the schema returned by tool_describe."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "tool_id": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object"},
                },
                "required": ["tool_id"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        ),
    )


def render_deferred_capability_directory(
    catalog_tools: tuple[CatalogTool, ...],
) -> str | None:
    if not catalog_tools:
        return None
    groups: dict[tuple[str, str | None], list[CatalogTool]] = {}
    for catalog_tool in catalog_tools:
        tool = catalog_tool.tool
        label = compact_text(
            tool.catalog_label or default_catalog_label(tool),
            limit=128,
        )
        groups.setdefault((label, tool.mcp_server_id), []).append(catalog_tool)

    lines = [
        "Discoverable specialist capabilities (already authorized; schemas are deferred):",
        (
            "Use tool_search with the capability or tool name below, then use "
            "tool_describe and tool_call. Prefer a matching specialist over a generic tool."
        ),
    ]
    remaining_tools = len(catalog_tools)
    for group_key in sorted(groups, key=lambda key: key[0].lower()):
        label, _server_id = group_key
        group_tools = groups[group_key]
        tool_names = tuple(
            dict.fromkeys(compact_text(item.tool.source_tool_name or item.tool.name, limit=96) for item in group_tools),
        )
        displayed_names = tool_names[:TOOL_CATALOG_DIRECTORY_MAX_TOOL_NAMES_PER_GROUP]
        remainder_count = len(tool_names) - len(displayed_names)
        suffix = "" if remainder_count == 0 else f", +{remainder_count} more"
        capability_key = next(
            (item.tool.capability_key for item in group_tools if item.tool.capability_key is not None),
            None,
        )
        capability_suffix = "" if capability_key is None else f" [{capability_key}]"
        line = f"- {label}{capability_suffix} ({len(group_tools)} tools): {', '.join(displayed_names)}{suffix}"
        if len("\n".join((*lines, line))) > TOOL_CATALOG_DIRECTORY_MAX_CHARACTERS:
            break
        lines.append(line)
        remaining_tools -= len(group_tools)

    if remaining_tools:
        lines.append(
            f"- {remaining_tools} additional authorized specialist tools are available through tool_search.",
        )
    return "\n".join(lines)


def default_catalog_label(tool: AgentTool) -> str:
    if tool.source == ToolGrantScope.INTERNAL:
        return "Voxos tools"
    if tool.source == ToolGrantScope.INTEGRATION:
        return "Connected integrations"
    return "Specialist MCP tools"


def catalog_tool_id(tool: AgentTool) -> str:
    identity = ":".join(
        (
            tool.source.value,
            tool.mcp_server_id or "",
            tool.integration_id or "",
            tool.source_tool_name or tool.name,
        ),
    )
    return f"tool_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def strip_harmony_tool_name_channel_suffix(tool_name: str) -> str:
    for suffix in HARMONY_TOOL_NAME_CHANNEL_SUFFIXES:
        if tool_name.endswith(suffix):
            return tool_name.removesuffix(suffix)
    return tool_name


def search_catalog(
    catalog_tools: tuple[CatalogTool, ...],
    *,
    query: str,
    limit: int,
) -> tuple[ToolSearchMatch, ...]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return ()
    documents = tuple(tool_search_tokens(item) for item in catalog_tools)
    average_length = sum(len(document) for document in documents) / len(documents) if documents else 0.0
    document_frequencies = Counter(token for document in documents for token in set(document))
    matches: list[ToolSearchMatch] = []
    normalized_query = " ".join(query.lower().split())
    for catalog_tool, document in zip(catalog_tools, documents, strict=True):
        score = bm25_score(
            query_tokens=query_tokens,
            document_tokens=document,
            document_count=len(documents),
            average_document_length=average_length,
            document_frequencies=document_frequencies,
        )
        tool = catalog_tool.tool
        score += capability_identity_match_bonus(
            query_tokens=query_tokens,
            tool=tool,
        )
        if normalized_query in {
            tool.name.lower(),
            (tool.source_tool_name or "").lower(),
            catalog_tool.id.lower(),
        }:
            score += 100.0
        if score > 0:
            matches.append(ToolSearchMatch(catalog_tool=catalog_tool, score=score))
    matches.sort(
        key=lambda match: (-match.score, match.catalog_tool.tool.name),
    )
    return tuple(matches[:limit])


def tool_search_tokens(catalog_tool: CatalogTool) -> tuple[str, ...]:
    tool = catalog_tool.tool
    parameter_names = schema_property_names(tool.parameters_schema)
    return tokenize(
        " ".join(
            (
                catalog_tool.id,
                tool.name,
                tool.source_tool_name or "",
                tool.catalog_label or "",
                tool.capability_key or "",
                tool.route_action or "",
                tool.description,
                " ".join(parameter_names),
            ),
        ),
    )


def capability_identity_match_bonus(
    *,
    query_tokens: tuple[str, ...],
    tool: AgentTool,
) -> float:
    identity_tokens = tuple(
        token for value in (tool.catalog_label, tool.capability_key) if value is not None for token in tokenize(value)
    )
    longest_match = longest_contiguous_token_match(
        query_tokens=query_tokens,
        identity_tokens=identity_tokens,
    )
    return 100.0 * longest_match if longest_match >= 2 else 0.0


def longest_contiguous_token_match(
    *,
    query_tokens: tuple[str, ...],
    identity_tokens: tuple[str, ...],
) -> int:
    longest_match = 0
    for query_start in range(len(query_tokens)):
        for identity_start in range(len(identity_tokens)):
            match_length = 0
            while (
                query_start + match_length < len(query_tokens)
                and identity_start + match_length < len(identity_tokens)
                and query_tokens[query_start + match_length] == identity_tokens[identity_start + match_length]
            ):
                match_length += 1
            longest_match = max(longest_match, match_length)
    return longest_match


def bm25_score(
    *,
    query_tokens: tuple[str, ...],
    document_tokens: tuple[str, ...],
    document_count: int,
    average_document_length: float,
    document_frequencies: Counter[str],
) -> float:
    if not document_tokens or document_count == 0 or average_document_length == 0:
        return 0.0
    frequencies = Counter(document_tokens)
    score = 0.0
    k1 = 1.5
    b = 0.75
    for token in query_tokens:
        term_frequency = frequencies[token]
        if term_frequency == 0:
            continue
        document_frequency = document_frequencies[token]
        inverse_document_frequency = math.log(
            1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5),
        )
        denominator = term_frequency + k1 * (1 - b + b * len(document_tokens) / average_document_length)
        score += inverse_document_frequency * term_frequency * (k1 + 1) / denominator
    return score


def schema_property_names(schema: JsonObject) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(str(name) for name in properties)


def tokenize(value: str) -> tuple[str, ...]:
    return tuple(TOKEN_PATTERN.findall(value.lower()))


def json_tool_result(
    *,
    call_id: str,
    name: str,
    payload: JsonObject,
) -> AgentToolResult:
    return AgentToolResult(
        call_id=call_id,
        name=name,
        output_text=orjson.dumps(payload).decode("utf-8"),
        raw_output=payload,
    )


def required_string(arguments: JsonObject, key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"{key} must be a non-empty string"
        raise ValueError(message)
    return value.strip()


def bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(TOOL_LIMIT_TYPE_ERROR)
    return max(minimum, min(value, maximum))


def compact_text(value: str, *, limit: int) -> str:
    compacted = " ".join(value.split())
    if len(compacted) <= limit:
        return compacted
    return f"{compacted[: limit - 3]}..."

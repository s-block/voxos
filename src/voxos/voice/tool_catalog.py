from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import orjson
from livekit.agents import RunContext, function_tool
from livekit.agents.llm import FunctionToolCall, ToolContext, execute_function_call

from voxos.agent_core.models import AgentTool
from voxos.agent_core.tool_catalog import (
    TOOL_CALL_ARGUMENTS_ERROR,
    TOOL_CALL_NAME,
    TOOL_DESCRIBE_NAME,
    TOOL_ID_UNAVAILABLE_ERROR,
    TOOL_SEARCH_ACTIVATION_COUNT,
    TOOL_SEARCH_ACTIVATION_SCHEMA_BYTES,
    TOOL_SEARCH_MAX_RESULTS,
    TOOL_SEARCH_NAME,
    CatalogTool,
    catalog_tool_id,
    render_deferred_capability_directory,
    search_catalog,
    tool_catalog_bridge_tools,
)
from voxos.agent_runtime.models import JsonObject
from voxos.voice.tools import VoiceCatalogTool, VoiceLiveKitTool

TOOL_SEARCH_QUERY_EMPTY_ERROR = "query must not be empty"
TOOL_SEARCH_QUERY_TYPE_ERROR = "query must be a string"
TOOL_SEARCH_LIMIT_TYPE_ERROR = "limit must be an integer"
TOOL_CATALOG_ID_TYPE_ERROR = "tool_id must be a string"
TOOL_CATALOG_DUPLICATE_ID_ERROR = "Voice tool catalog contains duplicate tool identities"
TOOL_CATALOG_DUPLICATE_NAME_ERROR = "Voice tool catalog contains duplicate function names"


@dataclass(frozen=True, slots=True)
class RunScopedVoiceToolCatalog:
    tools: tuple[VoiceCatalogTool, ...]
    catalog_tools: tuple[CatalogTool, ...]
    deferred_catalog_tools: tuple[CatalogTool, ...]
    tools_by_id: Mapping[str, VoiceCatalogTool]
    search_enabled: bool

    @classmethod
    def create(
        cls,
        tools: tuple[VoiceCatalogTool, ...],
    ) -> "RunScopedVoiceToolCatalog":
        _validate_unique_tools(tools)
        catalog_tools = tuple(
            CatalogTool(id=catalog_tool_id(item.definition), tool=item.definition)
            for item in tools
            if not item.definition.required
        )
        deferred_catalog_tools = tuple(item for item in catalog_tools if not _is_direct_voice_tool(item.tool))
        search_enabled = _should_enable_voice_tool_search(
            deferred_catalog_tools,
        )
        return cls(
            tools=tools,
            catalog_tools=catalog_tools,
            deferred_catalog_tools=(deferred_catalog_tools if search_enabled else ()),
            tools_by_id=MappingProxyType(
                {catalog_tool_id(item.definition): item for item in tools if not item.definition.required},
            ),
            search_enabled=search_enabled,
        )

    def visible_tools(self) -> tuple[VoiceLiveKitTool, ...]:
        if not self.search_enabled:
            return tuple(item.function_tool for item in self.tools)
        direct_tools = tuple(item.function_tool for item in self.tools if _is_direct_voice_tool(item.definition))
        return (*direct_tools, *self._bridge_tools())

    def capability_directory(self) -> str | None:
        if not self.search_enabled:
            return None
        return render_deferred_capability_directory(
            self.deferred_catalog_tools,
        )

    def search(self, *, query: str, limit: int = 5) -> JsonObject:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError(TOOL_SEARCH_QUERY_EMPTY_ERROR)
        bounded_limit = max(1, min(limit, TOOL_SEARCH_MAX_RESULTS))
        results: list[JsonObject] = []
        for match in search_catalog(
            self.catalog_tools,
            query=normalized_query,
            limit=bounded_limit,
        ):
            definition = match.catalog_tool.tool
            call_by_name = _is_direct_voice_tool(definition)
            result: JsonObject = {
                "tool_id": match.catalog_tool.id,
                "name": definition.name,
                "action": definition.route_action,
                "description": _compact_text(definition.description, limit=320),
                "callable": True,
                "call_by_name": call_by_name,
                "invoke_with": definition.name if call_by_name else TOOL_CALL_NAME,
                "score": round(match.score, 6),
            }
            if not call_by_name:
                result["describe_with"] = TOOL_DESCRIBE_NAME
            results.append(result)
        return {"query": normalized_query, "results": results}

    def describe(self, *, tool_id: str) -> JsonObject:
        target = self._require_tool(tool_id)
        definition = target.definition
        return {
            "tool_id": tool_id,
            "name": definition.name,
            "description": definition.description,
            "action": definition.route_action,
            "parameters_schema": definition.parameters_schema,
        }

    async def call(
        self,
        *,
        context: RunContext[object],
        tool_id: str,
        arguments: object,
    ) -> object:
        if not isinstance(arguments, dict):
            raise ValueError(TOOL_CALL_ARGUMENTS_ERROR)
        target = self._require_tool(tool_id)
        arguments_json: JsonObject = {str(key): value for key, value in arguments.items()}
        result = await execute_function_call(
            FunctionToolCall(
                name=target.function_tool.info.name,
                arguments=orjson.dumps(arguments_json).decode("utf-8"),
                call_id=context.function_call.call_id,
            ),
            ToolContext((target.function_tool,)),
            call_ctx=context,
        )
        if result.raw_exception is not None:
            raise result.raw_exception
        return result.raw_output

    def _bridge_tools(self) -> tuple[VoiceLiveKitTool, ...]:
        directory = self.capability_directory()
        return tuple(
            self._bridge_tool(
                definition=definition,
                description=(
                    f"{definition.description}\n\n{directory}"
                    if definition.name == TOOL_SEARCH_NAME and directory is not None
                    else definition.description
                ),
            )
            for definition in tool_catalog_bridge_tools()
        )

    def _bridge_tool(
        self,
        *,
        definition: AgentTool,
        description: str,
    ) -> VoiceLiveKitTool:
        async def call_catalog_tool(
            context: RunContext[object],
            raw_arguments: dict[str, object],
        ) -> object:
            if definition.name == TOOL_SEARCH_NAME:
                query = raw_arguments.get("query")
                if not isinstance(query, str):
                    raise ValueError(TOOL_SEARCH_QUERY_TYPE_ERROR)
                limit = raw_arguments.get("limit", 5)
                if isinstance(limit, bool) or not isinstance(limit, int):
                    raise ValueError(TOOL_SEARCH_LIMIT_TYPE_ERROR)
                return self.search(query=query, limit=limit)
            tool_id = raw_arguments.get("tool_id")
            if not isinstance(tool_id, str):
                raise ValueError(TOOL_CATALOG_ID_TYPE_ERROR)
            if definition.name == TOOL_DESCRIBE_NAME:
                return self.describe(tool_id=tool_id)
            return await self.call(
                context=context,
                tool_id=tool_id,
                arguments=raw_arguments.get("arguments", {}),
            )

        return function_tool(
            call_catalog_tool,
            raw_schema={
                "name": definition.name,
                "description": description,
                "parameters": definition.parameters_schema,
            },
        )

    def _require_tool(self, tool_id: str) -> VoiceCatalogTool:
        target = self.tools_by_id.get(tool_id)
        if target is None:
            raise PermissionError(TOOL_ID_UNAVAILABLE_ERROR)
        return target


def _is_direct_voice_tool(tool: AgentTool) -> bool:
    return tool.always_visible or tool.required


def _validate_unique_tools(tools: tuple[VoiceCatalogTool, ...]) -> None:
    tool_ids = tuple(catalog_tool_id(item.definition) for item in tools)
    if len(set(tool_ids)) != len(tool_ids):
        raise ValueError(TOOL_CATALOG_DUPLICATE_ID_ERROR)
    function_names = tuple(item.function_tool.info.name for item in tools)
    if len(set(function_names)) != len(function_names):
        raise ValueError(TOOL_CATALOG_DUPLICATE_NAME_ERROR)


def _should_enable_voice_tool_search(
    tools: tuple[CatalogTool, ...],
) -> bool:
    if len(tools) >= TOOL_SEARCH_ACTIVATION_COUNT:
        return True
    schema_bytes = sum(len(orjson.dumps(item.tool.parameters_schema)) for item in tools)
    return schema_bytes >= TOOL_SEARCH_ACTIVATION_SCHEMA_BYTES


def _compact_text(value: str, *, limit: int) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(0, limit - 1)].rstrip()}…"

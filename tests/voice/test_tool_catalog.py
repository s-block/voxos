from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, cast

import orjson
import pytest
from livekit.agents import RunContext, function_tool
from livekit.agents.llm import (
    FunctionCall,
    FunctionTool,
    FunctionToolCall,
    RawFunctionTool,
    ToolContext,
    execute_function_call,
)

from voxos.agent_core.models import AgentTool
from voxos.agent_core.tool_catalog import (
    TOOL_CALL_NAME,
    TOOL_DESCRIBE_NAME,
    TOOL_SEARCH_NAME,
    catalog_tool_id,
)
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.tool_policy import ToolGrantScope
from voxos.voice.mcp_tools import CompositeVoiceToolProvider
from voxos.voice.tool_catalog import RunScopedVoiceToolCatalog
from voxos.voice.tools import VoiceCatalogTool, VoiceLiveKitTool


@dataclass(slots=True)
class FakeSpeechHandle:
    num_steps: int = 1


@dataclass(slots=True)
class FakeCatalogToolProvider:
    tools: tuple[VoiceCatalogTool, ...]

    async def get_tools(self, user_id: str) -> tuple[()]:
        del user_id
        return ()

    async def get_catalog_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> tuple[VoiceCatalogTool, ...]:
        del scope, effective_tool_policy
        return self.tools


@dataclass(slots=True)
class FakeDirectToolProvider:
    tools: tuple[VoiceLiveKitTool, ...]

    async def get_tools(self, user_id: str) -> tuple[VoiceLiveKitTool, ...]:
        del user_id
        return self.tools

    async def get_tools_for_scope(
        self,
        scope: AgentScope,
        *,
        effective_tool_policy: EffectiveToolPolicySnapshot | None = None,
    ) -> tuple[VoiceLiveKitTool, ...]:
        del scope, effective_tool_policy
        return self.tools


def voice_context(*, function_name: str) -> RunContext[object]:
    return RunContext(
        session=cast(Any, object()),
        speech_handle=cast(Any, FakeSpeechHandle()),
        function_call=FunctionCall(
            call_id="outer-call",
            name=function_name,
            arguments="{}",
        ),
    )


def catalog_tool(
    *,
    name: str,
    label: str,
    calls: list[tuple[str, dict[str, object]]],
) -> VoiceCatalogTool:
    async def execute(raw_arguments: dict[str, object]) -> dict[str, object]:
        calls.append((name, raw_arguments))
        return {"tool": name, "arguments": raw_arguments}

    execute.__name__ = name
    livekit_tool = cast(
        RawFunctionTool[..., Awaitable[dict[str, object]]],
        function_tool(
            execute,
            raw_schema={
                "name": name,
                "description": f"Execute {name}",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        ),
    )
    return VoiceCatalogTool(
        definition=AgentTool(
            name=name,
            source_tool_name=name,
            description=f"Execute {name}",
            parameters_schema=livekit_tool.info.raw_schema["parameters"],
            source=ToolGrantScope.MCP_SERVER,
            mcp_server_id=f"{label.lower().replace(' ', '-')}-server",
            catalog_label=label,
            capability_key=label.lower().replace(" ", "-"),
        ),
        function_tool=livekit_tool,
    )


def context_catalog_tool(
    *,
    calls: list[tuple[str, dict[str, object]]],
) -> VoiceCatalogTool:
    async def use_browser(
        context: RunContext[object],
        instruction: str,
    ) -> str:
        calls.append(
            (
                context.function_call.name,
                {"instruction": instruction},
            ),
        )
        return "browser result"

    livekit_tool = cast(
        FunctionTool[..., Awaitable[str]],
        function_tool(
            use_browser,
            name="use_browser",
            description="Use a general browser.",
        ),
    )
    return VoiceCatalogTool(
        definition=AgentTool(
            name="use_browser",
            description="Use a general browser.",
            parameters_schema={
                "type": "object",
                "properties": {"instruction": {"type": "string"}},
                "required": ["instruction"],
                "additionalProperties": False,
            },
            source=ToolGrantScope.INTERNAL,
            catalog_label="Browser",
            capability_key="browser",
        ),
        function_tool=livekit_tool,
    )


@pytest.mark.asyncio
async def test_voice_catalog_enforces_discovery_and_executes_returned_tool_id() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    tools = (
        catalog_tool(
            name="search_places",
            label="Google Maps Grounding Lite",
            calls=calls,
        ),
        catalog_tool(name="use_browser", label="Browser", calls=calls),
        *tuple(
            catalog_tool(
                name=f"specialist_{index}",
                label=f"Specialist {index}",
                calls=calls,
            )
            for index in range(8)
        ),
    )
    catalog = RunScopedVoiceToolCatalog.create(tools)

    visible_tools = catalog.visible_tools()
    visible_names = {tool.info.name for tool in visible_tools if hasattr(tool, "info")}

    assert catalog.search_enabled is True
    assert visible_names == {
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    }
    assert "use_browser" not in visible_names
    assert "search_places" not in visible_names
    directory = catalog.capability_directory()
    assert directory is not None
    assert "Google Maps Grounding Lite" in directory
    assert "search_places" in directory

    search_result = catalog.search(
        query="Google Maps Grounding Lite search_places",
    )
    results = search_result["results"]
    assert isinstance(results, list)
    first_result = results[0]
    assert isinstance(first_result, dict)
    assert first_result["name"] == "search_places"
    assert first_result["invoke_with"] == TOOL_CALL_NAME
    tool_id = first_result["tool_id"]
    assert isinstance(tool_id, str)

    tool_call = next(tool for tool in visible_tools if hasattr(tool, "info") and tool.info.name == TOOL_CALL_NAME)
    execution = await execute_function_call(
        FunctionToolCall(
            name=TOOL_CALL_NAME,
            arguments=orjson.dumps(
                {
                    "tool_id": tool_id,
                    "arguments": {"query": "lunch nearby"},
                },
            ).decode("utf-8"),
            call_id="outer-call",
        ),
        ToolContext((tool_call,)),
        call_ctx=voice_context(function_name=TOOL_CALL_NAME),
    )

    assert execution.raw_exception is None
    assert execution.raw_output == {
        "tool": "search_places",
        "arguments": {"query": "lunch nearby"},
    }
    assert calls == [("search_places", {"query": "lunch nearby"})]


@pytest.mark.asyncio
async def test_voice_catalog_keeps_context_aware_browser_callable_after_discovery() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    browser = context_catalog_tool(calls=calls)
    tools = (
        browser,
        *tuple(
            catalog_tool(
                name=f"specialist_{index}",
                label=f"Specialist {index}",
                calls=calls,
            )
            for index in range(9)
        ),
    )
    catalog = RunScopedVoiceToolCatalog.create(tools)
    tool_id = catalog_tool_id(browser.definition)

    result = await catalog.call(
        context=voice_context(function_name=TOOL_CALL_NAME),
        tool_id=tool_id,
        arguments={"instruction": "Open the account page"},
    )

    assert result == "browser result"
    assert calls == [
        (
            TOOL_CALL_NAME,
            {"instruction": "Open the account page"},
        ),
    ]


def test_voice_catalog_keeps_small_tool_sets_direct() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    tool = catalog_tool(name="single_specialist", label="Specialist", calls=calls)

    catalog = RunScopedVoiceToolCatalog.create((tool,))

    assert catalog.search_enabled is False
    assert catalog.visible_tools() == (tool.function_tool,)


def test_voice_catalog_rejects_duplicate_tool_identity() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    first = catalog_tool(name="duplicate", label="Specialist", calls=calls)
    second = catalog_tool(name="duplicate", label="Specialist", calls=calls)

    with pytest.raises(ValueError, match="duplicate tool identities"):
        RunScopedVoiceToolCatalog.create((first, second))


def test_voice_catalog_rejects_duplicate_function_name() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    first = catalog_tool(name="duplicate", label="Specialist One", calls=calls)
    second = catalog_tool(name="duplicate", label="Specialist Two", calls=calls)

    with pytest.raises(ValueError, match="duplicate function names"):
        RunScopedVoiceToolCatalog.create((first, second))


@pytest.mark.asyncio
async def test_composite_voice_provider_enforces_one_catalog_boundary() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    direct_tool = catalog_tool(
        name="internal_memory_tool",
        label="Internal",
        calls=calls,
    ).function_tool
    candidates = tuple(
        catalog_tool(
            name=("use_browser" if index == 0 else f"external_tool_{index}"),
            label=("Browser" if index == 0 else f"External {index}"),
            calls=calls,
        )
        for index in range(10)
    )
    provider = CompositeVoiceToolProvider(
        providers=(
            FakeDirectToolProvider((direct_tool,)),
            FakeCatalogToolProvider(candidates),
        ),
    )

    tools = await provider.get_tools_for_scope(
        AgentScope(
            owner_user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            channel="livekit",
            source="test",
        ),
        effective_tool_policy=EffectiveToolPolicySnapshot(
            user_id="user-1",
            agent_id="agent-1",
        ),
    )
    names = {tool.info.name for tool in tools if hasattr(tool, "info")}

    assert names == {
        "internal_memory_tool",
        TOOL_SEARCH_NAME,
        TOOL_DESCRIBE_NAME,
        TOOL_CALL_NAME,
    }
    assert "use_browser" not in names

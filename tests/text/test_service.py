import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from voxos.agent_core.models import (
    AgentEventKind,
    AgentRunContext,
    AgentTool,
    AgentToolActivity,
    AgentToolProvider,
    AgentToolResult,
)
from voxos.agent_core.tool_catalog import (
    TOOL_CALL_NAME,
    TOOL_SEARCH_NAME,
    RunScopedToolCatalogProvider,
    ToolNotExposedError,
)
from voxos.agent_runtime.models import AgentTranscriptRole, JsonObject
from voxos.agent_runtime.records import AgentToolEventRecord, AgentTranscriptRecord
from voxos.agent_runtime.tool_execution import (
    ToolArgumentSchemaError,
    ToolAuthorityArgumentError,
    ToolSchemaDefinitionError,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolRisk
from voxos.agent_runtime.tool_routing import WEB_SEARCH_ACTION
from voxos.db.models import utc_now
from voxos.logos.models import ActivityIcon
from voxos.mcp.errors import McpConnectionTestError, McpToolCallRejectedError
from voxos.place_cards.models import PlaceCardArtifact
from voxos.text.models import TextProvider, TextReasoningMode
from voxos.text.providers import ProviderMessage, TextProviderToolCall
from voxos.text.service import (
    MAX_PARALLEL_TOOL_CALLS,
    PreparedToolCall,
    TextChatService,
    ToolProtocolArtifactFilter,
    append_tool_call_output,
    available_tools_message,
    parallel_tool_call_batches,
    recent_tool_executions_message,
    run_tool_call_batch,
    text_reasoning_mode_for_run_kind,
    tool_call_fingerprint,
    transcript_tail_within_character_limit,
    validate_tool_call_arguments,
)


def test_tool_protocol_artifact_filter_removes_literal_tool_markup_and_multilingual_garbage() -> None:
    output_filter = ToolProtocolArtifactFilter(
        frozenset({"tool_search", "tool_describe", "tool_call"}),
    )
    chunks = (
        "I found some options. to=funct",
        "ions.tool_call  ภาษาไทย中文ქართული",
        '{"tool_id":"tool_123","arguments":{"textQuery":"restaurants"}}Yes — here are the results.',
    )

    text = "".join(output_filter.feed(chunk) for chunk in chunks) + output_filter.flush()

    assert text == "I found some options. Yes — here are the results."
    assert output_filter.removed_tool_call_artifact is True


def test_tool_protocol_artifact_filter_removes_streamed_xml_style_tool_markup() -> None:
    output_filter = ToolProtocolArtifactFilter(
        frozenset({"tool_search", "tool_describe", "tool_call"}),
    )
    # Verbatim Nemotron output captured from a text chat that leaked it to the user.
    artifact = (
        "<tool_call>\n<function=tool_search>\n<parameter=limit>\n5\n</parameter>\n"
        "<parameter=query>\nParish Station episode summary\n</parameter>\n"
        "</function>\n</tool_call>"
    )

    # Fed one character at a time, because providers stream these token by token.
    text = "".join(output_filter.feed(character) for character in artifact) + output_filter.flush()

    assert text == ""
    assert output_filter.removed_tool_call_artifact is True


def test_tool_protocol_artifact_filter_removes_truncated_xml_style_tool_markup() -> None:
    output_filter = ToolProtocolArtifactFilter(frozenset({"tool_search"}))

    text = output_filter.feed("<tool_call>\n<function=tool_search>\n<parameter") + output_filter.flush()

    assert text == ""
    assert output_filter.removed_tool_call_artifact is True


def test_tool_protocol_artifact_filter_keeps_markup_that_names_no_exposed_tool() -> None:
    output_filter = ToolProtocolArtifactFilter(frozenset({"tool_search", "tool_call"}))
    content = "Wrap the payload in <tool_call> and </tool_call> when the template asks for it."

    text = output_filter.feed(content) + output_filter.flush()

    assert text == content
    assert output_filter.removed_tool_call_artifact is False


def test_tool_protocol_artifact_filter_does_not_match_tool_name_substrings() -> None:
    output_filter = ToolProtocolArtifactFilter(frozenset({"get"}))
    content = "<tool_call>The target remains unchanged.</tool_call>"

    text = output_filter.feed(content) + output_filter.flush()

    assert text == content
    assert output_filter.removed_tool_call_artifact is False


def test_tool_protocol_artifact_filter_preserves_ordinary_text() -> None:
    output_filter = ToolProtocolArtifactFilter(frozenset({"tool_call"}))

    text = output_filter.feed("No tool call is needed for this question.") + output_filter.flush()

    assert text == "No tool call is needed for this question."
    assert output_filter.removed_tool_call_artifact is False


@pytest.mark.parametrize(
    ("run_kind", "expected_mode"),
    [
        ("text_chat_turn", TextReasoningMode.FAST),
        ("communication_reply", TextReasoningMode.FAST),
        ("manual_agent_task", TextReasoningMode.AGENTIC),
        ("scheduled_agent_turn", TextReasoningMode.AGENTIC),
        ("subagent", TextReasoningMode.AGENTIC),
        ("future_run_kind", TextReasoningMode.FAST),
    ],
)
def test_text_reasoning_mode_is_agentic_only_for_agent_runs(
    run_kind: str,
    expected_mode: TextReasoningMode,
) -> None:
    assert text_reasoning_mode_for_run_kind(run_kind) == expected_mode


@pytest.mark.asyncio
async def test_transient_place_cards_are_not_persisted_with_runtime_events() -> None:
    service = text_chat_service(RecordingToolProvider())
    place_url = "https://www.google.com/maps/place/?q=place_id:ChIJ123"

    event = await service._append_runtime_event(  # noqa: SLF001
        context=run_context(),
        kind=AgentEventKind.MESSAGE_COMPLETED,
        payload={
            "text": "Try Konoba Stari Grad.",
            "place_cards": [
                {
                    "provider": "google_maps",
                    "place_id": "ChIJ123",
                    "title": "Konoba Stari Grad",
                    "place_url": place_url,
                    "attribution_url": place_url,
                    "directions_url": None,
                    "latitude": None,
                    "longitude": None,
                    "position": 0,
                },
            ],
        },
        persisted_payload={"text": "Try Konoba Stari Grad."},
    )

    assert event.payload["place_cards"] != []
    stored_event = service.runtime_store.append_runtime_event.await_args.args[0]
    assert stored_event.payload_json == {"text": "Try Konoba Stari Grad."}


def test_nvidia_tool_output_uses_chat_completions_continuation_message() -> None:
    continuation_items: list[JsonObject] = []

    append_tool_call_output(
        messages=[],
        continuation_items=continuation_items,
        provider=TextProvider.NVIDIA,
        call_id="call-1",
        output="Saved",
    )

    assert continuation_items == [
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "Saved",
        },
    ]


def test_transcript_tail_keeps_complete_recent_turns() -> None:
    records = (
        transcript(role=AgentTranscriptRole.USER, text="old question"),
        transcript(role=AgentTranscriptRole.ASSISTANT, text="old answer"),
        transcript(role=AgentTranscriptRole.USER, text="recent question"),
        transcript(role=AgentTranscriptRole.ASSISTANT, text="recent answer"),
    )

    selected = transcript_tail_within_character_limit(
        records,
        character_limit=len("recent questionrecent answer") + 1,
    )

    assert [record.text for record in selected] == ["recent question", "recent answer"]


def test_transcript_tail_keeps_latest_complete_turn_when_it_exceeds_budget() -> None:
    records = (
        transcript(role=AgentTranscriptRole.USER, text="question"),
        transcript(role=AgentTranscriptRole.ASSISTANT, text="answer" * 20),
    )

    selected = transcript_tail_within_character_limit(records, character_limit=10)

    assert selected == records


def test_available_tools_message_describes_late_mcp_tools_authoritatively() -> None:
    leading_tools = tuple(tool(f"tool_{index}") for index in range(35))
    monzo_tools = tuple(
        tool(
            f"mcp_hash_{name}",
            description=(
                f"{name} from Monzo MCP server: Use the canonical {name} capability for the connected Monzo account."
            ),
        )
        for name in (
            "monzo_connection_status",
            "monzo_get_balance",
            "monzo_get_transaction",
            "monzo_list_accounts",
            "monzo_list_pots",
            "monzo_list_transactions",
        )
    )
    trailing_tools = tuple(tool(f"tool_{index}") for index in range(41, 48))
    tools = (*leading_tools, *monzo_tools, *trailing_tools)

    message = available_tools_message(tools)

    assert message is not None
    assert "current list is authoritative" in message.content
    assert "overrides historical assistant claims" in message.content
    assert "Additional tools with compact descriptions" in message.content
    assert "- mcp_hash_monzo_get_balance: monzo_get_balance from Monzo MCP server:" in message.content
    assert "- mcp_hash_monzo_list_transactions: monzo_list_transactions from Monzo MCP server:" in message.content
    assert "- tool_47: Description for tool_47" in message.content


def test_available_tools_message_requires_catalog_search_for_named_specialists() -> None:
    message = available_tools_message((tool(TOOL_SEARCH_NAME),))

    assert message is not None
    assert "you must call tool_search before answering" in message.content
    assert "tool_search discovers callable capabilities" in message.content
    assert "it does not search the web" in message.content
    assert "not merely because a research topic names a product" in message.content
    assert "Prefer a matching authorized specialist over a generic web search" in message.content


def test_available_tools_message_makes_the_routed_web_search_tool_the_default() -> None:
    web_search = AgentTool(
        name="mcp_1e3332a95c_web_search_exa",
        source_tool_name="web_search_exa",
        description="Search the web in real time.",
        source=ToolGrantScope.MCP_SERVER,
        risk=ToolRisk.LOW,
        mcp_server_id="exa-server",
        route_action=WEB_SEARCH_ACTION,
        automatic_route_priority=10,
    )

    message = available_tools_message((web_search, tool("list_agent_memories")))

    assert message is not None
    # The exact tool name has to appear, because collision-safe prefixes make it
    # unguessable from a generic instruction to "use a web search tool".
    assert "- web.search -> mcp_1e3332a95c_web_search_exa" in message.content


def test_available_tools_message_marks_tool_context_as_removable() -> None:
    message = available_tools_message((tool("list_agent_memories"),))

    assert message is not None
    assert message.discard_when_tools_unavailable is True


def test_available_tools_message_omits_web_search_default_without_a_routed_tool() -> None:
    message = available_tools_message((tool("list_agent_memories"),))

    assert message is not None
    assert "Canonical capability routes" not in message.content


def test_recent_tool_executions_message_uses_verified_metadata_only() -> None:
    event = AgentToolEventRecord(
        session_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        call_id="call-1",
        name="monzo_list_transactions",
        output_text='{"sensitive": "result"}',
        is_error=False,
        created_at=utc_now(),
    )

    message = recent_tool_executions_message(
        (event,),
        tools=(tool("monzo_list_transactions"),),
    )

    assert message is not None
    assert "monzo_list_transactions succeeded" in message.content
    assert "sensitive" not in message.content
    assert "call it" in message.content


def test_text_tool_validation_uses_only_the_selected_tools_allowed_paths() -> None:
    browserless_tool = tool(
        "browserless_smartscraper",
        authority_argument_allowed_paths=frozenset({("url",)}),
    )

    validate_tool_call_arguments(
        browserless_tool,
        {"url": "https://example.test"},
    )

    with pytest.raises(ToolAuthorityArgumentError):
        validate_tool_call_arguments(
            browserless_tool,
            {
                "url": "https://example.test",
                "headers": {"Authorization": "Bearer attacker-token"},
            },
        )
    with pytest.raises(ToolAuthorityArgumentError):
        validate_tool_call_arguments(
            None,
            {"url": "https://example.test"},
        )


def test_text_tool_validation_enforces_nested_mcp_schema_without_echoing_values() -> None:
    weather_tool = tool(
        "lookup_weather",
        parameters_schema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "object",
                    "properties": {"address": {"type": "string"}},
                    "required": ["address"],
                },
                "date": {"$ref": "#/$defs/date"},
            },
            "required": ["location"],
            "$defs": {
                "date": {
                    "type": "object",
                    "properties": {
                        "year": {"type": "integer"},
                        "month": {"type": "integer"},
                        "day": {"type": "integer"},
                    },
                    "required": ["year", "month", "day"],
                },
            },
        },
    )

    validate_tool_call_arguments(
        weather_tool,
        {
            "location": {"address": "London, UK"},
            "date": {"year": 2026, "month": 7, "day": 28},
        },
    )

    with pytest.raises(
        ToolArgumentSchemaError,
        match="Tool argument location must be an object",
    ) as location_error:
        validate_tool_call_arguments(
            weather_tool,
            {"location": "London, UK"},
        )
    assert "London" not in str(location_error.value)

    with pytest.raises(
        ToolArgumentSchemaError,
        match="Tool argument date must be an object",
    ) as date_error:
        validate_tool_call_arguments(
            weather_tool,
            {
                "location": {"address": "London, UK"},
                "date": "tomorrow",
            },
        )
    assert "tomorrow" not in str(date_error.value)


def test_text_tool_validation_reports_missing_required_argument() -> None:
    weather_tool = tool(
        "lookup_weather",
        parameters_schema={
            "type": "object",
            "properties": {"location": {"type": "object"}},
            "required": ["location"],
        },
    )

    with pytest.raises(
        ToolArgumentSchemaError,
        match="Tool argument location is required",
    ):
        validate_tool_call_arguments(weather_tool, {})


def test_text_tool_validation_reports_an_unresolvable_reference_as_invalid_schema() -> None:
    broken_tool = tool(
        "broken",
        parameters_schema={
            "type": "object",
            "properties": {"source": {"$ref": "#/$defs/Missing"}},
        },
    )

    with pytest.raises(ToolSchemaDefinitionError, match="Tool input schema is invalid"):
        validate_tool_call_arguments(broken_tool, {"source": "user"})


def test_text_tool_validation_drops_schema_declared_unsupported_fields() -> None:
    search_tool = tool(
        "search_places",
        parameters_schema={
            "type": "object",
            "properties": {"textQuery": {"type": "string"}},
            "required": ["textQuery"],
            "additionalProperties": False,
        },
    )

    accepted_arguments = validate_tool_call_arguments(
        search_tool,
        {"textQuery": "restaurants nearby", "limit": 5},
    )

    assert accepted_arguments == {"textQuery": "restaurants nearby"}


def test_parallel_tool_call_batches_apply_the_concurrency_limit() -> None:
    seven_calls = tuple(prepared_tool_call(index) for index in range(7))
    bounded_calls = tuple(prepared_tool_call(index) for index in range(MAX_PARALLEL_TOOL_CALLS + 3))

    seven_call_batches = parallel_tool_call_batches(seven_calls)
    bounded_batches = parallel_tool_call_batches(bounded_calls)

    assert [len(batch) for batch in seven_call_batches] == [7]
    assert [len(batch) for batch in bounded_batches] == [
        MAX_PARALLEL_TOOL_CALLS,
        3,
    ]
    assert [prepared_call.tool_call.call_id for batch in bounded_batches for prepared_call in batch] == [
        f"call-{index}" for index in range(MAX_PARALLEL_TOOL_CALLS + 3)
    ]


@pytest.mark.asyncio
async def test_tool_call_batch_runs_concurrently_and_returns_original_order() -> None:
    provider = CoordinatedToolProvider(expected_starts=MAX_PARALLEL_TOOL_CALLS)
    prepared_calls = tuple(prepared_tool_call(index) for index in range(MAX_PARALLEL_TOOL_CALLS))

    batch_task = asyncio.create_task(
        run_tool_call_batch(
            tool_provider=provider,
            context=run_context(),
            prepared_calls=prepared_calls,
        )
    )
    await asyncio.wait_for(provider.all_started.wait(), timeout=1)

    assert provider.maximum_active == MAX_PARALLEL_TOOL_CALLS
    provider.release.set()
    outcomes = await asyncio.wait_for(batch_task, timeout=1)

    assert [outcome.result.call_id if outcome.result is not None else None for outcome in outcomes] == [
        f"call-{index}" for index in range(MAX_PARALLEL_TOOL_CALLS)
    ]


@pytest.mark.asyncio
async def test_tool_call_batch_cancellation_cancels_every_in_flight_call() -> None:
    provider = CoordinatedToolProvider(expected_starts=3)
    prepared_calls = tuple(prepared_tool_call(index) for index in range(3))
    batch_task = asyncio.create_task(
        run_tool_call_batch(
            tool_provider=provider,
            context=run_context(),
            prepared_calls=prepared_calls,
        )
    )
    await asyncio.wait_for(provider.all_started.wait(), timeout=1)

    batch_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await batch_task

    assert provider.cancelled_call_ids == {prepared_call.tool_call.call_id for prepared_call in prepared_calls}
    assert provider.active == 0


@pytest.mark.asyncio
async def test_mcp_connection_failure_does_not_fail_other_parallel_calls() -> None:
    provider = PartiallyUnavailableMcpToolProvider()
    service = text_chat_service(provider)
    tools = (
        tool("available-search", parallel_safe=True),
        tool("rate-limited-search", parallel_safe=True),
    )
    tool_calls = tuple(
        TextProviderToolCall(
            call_id=f"call-{index}",
            name=tool_definition.name,
            arguments_json={},
        )
        for index, tool_definition in enumerate(tools)
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=tools,
            tool_calls=tool_calls,
        )
    ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_started",
        "tool_call_completed",
        "tool_call_failed",
    ]
    assert messages[0].content == "verified search result"
    assert "rate limit was exceeded" in messages[1].content
    assert "Continue with any successful tool results" in messages[1].content


@pytest.mark.asyncio
async def test_unexpected_tool_failure_is_returned_to_the_agent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = UnexpectedFailureToolProvider()
    service = text_chat_service(provider)
    messages: list[ProviderMessage] = []

    with caplog.at_level(logging.ERROR, logger="voxos.text.service"):
        events = [
            event
            async for event in service._execute_tool_calls(  # noqa: SLF001
                context=run_context(),
                messages=messages,
                tools=(tool("create_task_plan"),),
                tool_calls=(
                    TextProviderToolCall(
                        call_id="call-task",
                        name="create_task_plan",
                        arguments_json={},
                    ),
                ),
            )
        ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_failed",
    ]
    assert events[-1].payload["error_type"] == "RuntimeError"
    assert "task store unavailable" in messages[-1].content
    assert "give the user a final response" in messages[-1].content
    assert "Do not claim that the tool succeeded" in messages[-1].content
    failure_record = next(
        record for record in caplog.records if record.message == "Unexpected agent tool execution failure"
    )
    assert failure_record.exc_info is not None


@pytest.mark.asyncio
async def test_invalid_tool_schema_is_returned_to_the_agent() -> None:
    provider = RecordingToolProvider()
    service = text_chat_service(provider)
    broken_tool = tool(
        "create_task_plan",
        parameters_schema={
            "type": "object",
            "properties": {"source": {"$ref": "#/$defs/Missing"}},
        },
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(broken_tool,),
            tool_calls=(
                TextProviderToolCall(
                    call_id="call-task",
                    name="create_task_plan",
                    arguments_json={"source": "user"},
                ),
            ),
        )
    ]

    assert [event.kind.value for event in events] == ["tool_call_failed"]
    assert events[-1].payload["error_type"] == "ToolSchemaDefinitionError"
    assert provider.execution_log == []
    assert "Tool input schema is invalid" in messages[-1].content
    assert "give the user a final response" in messages[-1].content


@pytest.mark.asyncio
async def test_text_tool_execution_parallelizes_reads_around_serial_write() -> None:
    provider = RecordingToolProvider()
    service = text_chat_service(provider)
    tools = (
        tool("read-one", parallel_safe=True),
        tool("read-two", parallel_safe=True),
        tool("write"),
        tool("read-three", parallel_safe=True),
        tool("read-four", parallel_safe=True),
    )
    tool_calls = tuple(
        TextProviderToolCall(
            call_id=f"call-{index}",
            name=tool_definition.name,
            arguments_json={},
        )
        for index, tool_definition in enumerate(tools)
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=tools,
            tool_calls=tool_calls,
        )
    ]

    assert provider.execution_log.index("start:read-two") < provider.execution_log.index("end:read-one")
    assert provider.execution_log.index("start:write") > provider.execution_log.index("end:read-two")
    assert provider.execution_log.index("start:read-three") > provider.execution_log.index("end:write")
    assert provider.execution_log.index("start:read-four") < provider.execution_log.index("end:read-three")
    assert [message.tool_call_id for message in messages] == [f"call-{index}" for index in range(5)]
    assert [event.payload["call_id"] for event in events if "call_id" in event.payload] == [
        "call-0",
        "call-1",
        "call-0",
        "call-1",
        "call-2",
        "call-2",
        "call-3",
        "call-4",
        "call-3",
        "call-4",
    ]


@pytest.mark.asyncio
async def test_rejected_parallel_wave_skips_later_same_tool_calls() -> None:
    provider = RejectingToolProvider()
    service = text_chat_service(provider)
    weather_tool = tool("weather", parallel_safe=True)
    tool_calls = tuple(
        TextProviderToolCall(
            call_id=f"call-{index}",
            name=weather_tool.name,
            arguments_json={"date": index},
        )
        for index in range(MAX_PARALLEL_TOOL_CALLS + 2)
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(weather_tool,),
            tool_calls=tool_calls,
        )
    ]

    assert provider.call_ids == [f"call-{index}" for index in range(MAX_PARALLEL_TOOL_CALLS)]
    assert len(messages) == MAX_PARALLEL_TOOL_CALLS + 2
    assert sum(event.kind.value == "tool_call_started" for event in events) == MAX_PARALLEL_TOOL_CALLS
    assert sum(event.kind.value == "tool_call_failed" for event in events) == MAX_PARALLEL_TOOL_CALLS
    assert all(
        "same tool already had invalid arguments" in message.content for message in messages[MAX_PARALLEL_TOOL_CALLS:]
    )


@pytest.mark.asyncio
async def test_catalog_target_schema_error_does_not_disable_other_target_calls() -> None:
    provider = SchemaRejectingCatalogProvider()
    service = text_chat_service(provider)
    catalog_tool = tool(
        TOOL_CALL_NAME,
        parameters_schema={
            "type": "object",
            "properties": {
                "tool_id": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["tool_id", "arguments"],
        },
    )
    tool_calls = tuple(
        TextProviderToolCall(
            call_id=f"call-{index}",
            name=TOOL_CALL_NAME,
            arguments_json={"tool_id": f"tool-{index}", "arguments": {}},
        )
        for index in range(2)
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            tool_provider=provider,
            messages=messages,
            tools=(catalog_tool,),
            tool_calls=tool_calls,
        )
    ]

    assert provider.call_ids == ["call-0", "call-1"]
    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_failed",
        "tool_call_started",
        "tool_call_failed",
    ]
    assert all("Use tool_describe" in message.content for message in messages)


@pytest.mark.asyncio
async def test_tool_error_result_is_failed_and_preserves_arguments() -> None:
    provider = ErrorResultToolProvider()
    service = text_chat_service(provider)
    arguments = {
        "location": "London",
        "checkin": "2026-08-07",
        "checkout": "2026-08-09",
        "flexibleTripLengths": ["weekend_trip"],
    }
    tool_call = TextProviderToolCall(
        call_id="call-error",
        name="airbnb_search",
        arguments_json=arguments,
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(tool("airbnb_search"),),
            tool_calls=(tool_call,),
        )
    ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_failed",
    ]
    assert events[-1].payload["error_message"] == ("checkin/checkout cannot be combined with flexible date filters.")
    stored_event = service.runtime_store.append_tool_event.await_args.args[0]
    assert stored_event.arguments_json == arguments
    assert stored_event.is_error is True
    assert "checkin/checkout cannot be combined" in messages[-1].content
    assert "give the user a final response" in messages[-1].content
    assert "Do not claim that the tool succeeded" in messages[-1].content


@pytest.mark.asyncio
async def test_tool_events_include_configured_activity_presentation() -> None:
    provider = RecordingToolProvider()
    service = text_chat_service(provider)
    search_tool = tool(
        "web_search",
        activity=AgentToolActivity(label="Searching the web", detail="Exa"),
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(search_tool,),
            tool_calls=(
                TextProviderToolCall(
                    call_id="call-search",
                    name=search_tool.name,
                    arguments_json={"query": "current news"},
                ),
            ),
        )
    ]

    assert [event.payload["activity"] for event in events] == [
        {"label": "Searching the web", "detail": "Exa"},
        {"label": "Searching the web", "detail": "Exa"},
    ]


@pytest.mark.asyncio
async def test_tool_execution_collects_place_cards_for_the_completed_message() -> None:
    place_url = "https://www.google.com/maps/place/?q=place_id:ChIJ123"
    provider = PlaceCardToolProvider(
        PlaceCardArtifact(
            provider="google_maps",
            place_id="ChIJ123",
            title="Konoba Stari Grad",
            place_url=place_url,
            attribution_url=place_url,
            position=0,
        ),
    )
    service = text_chat_service(provider)
    messages: list[ProviderMessage] = []
    place_card_batches: list[tuple[PlaceCardArtifact, ...]] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(tool("search_places"),),
            tool_calls=(
                TextProviderToolCall(
                    call_id="call-places",
                    name="search_places",
                    arguments_json={"textQuery": "restaurants near me"},
                ),
            ),
            place_card_batches=place_card_batches,
        )
    ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_completed",
    ]
    assert place_card_batches == [(provider.card,)]
    stored_tool_event = service.runtime_store.append_tool_event.await_args.args[0]
    assert stored_tool_event.output_text == "Google Maps place search completed."
    stored_runtime_event = service.runtime_store.append_runtime_event.await_args.args[0]
    assert stored_runtime_event.payload_json["output"] == ("Google Maps place search completed.")


@pytest.mark.asyncio
async def test_failed_place_search_redacts_durable_tool_and_runtime_events() -> None:
    place_url = "https://www.google.com/maps/place/?q=place_id:ChIJ123"
    provider = PlaceCardToolProvider(
        PlaceCardArtifact(
            provider="google_maps",
            place_id="ChIJ123",
            title="Konoba Stari Grad",
            place_url=place_url,
            attribution_url=place_url,
            position=0,
        ),
        is_error=True,
    )
    service = text_chat_service(provider)
    messages: list[ProviderMessage] = []
    place_card_batches: list[tuple[PlaceCardArtifact, ...]] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(tool("search_places"),),
            tool_calls=(
                TextProviderToolCall(
                    call_id="call-places-failed",
                    name="search_places",
                    arguments_json={"textQuery": "restaurants near me"},
                ),
            ),
            place_card_batches=place_card_batches,
        )
    ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_failed",
    ]
    assert "private partial place result" in events[-1].payload["output"]
    assert place_card_batches == []
    stored_tool_event = service.runtime_store.append_tool_event.await_args.args[0]
    assert stored_tool_event.output_text == "Google Maps place search failed."
    stored_runtime_event = service.runtime_store.append_runtime_event.await_args.args[0]
    assert stored_runtime_event.payload_json["output"] == "Google Maps place search failed."
    assert stored_runtime_event.payload_json["error_message"] == "Google Maps place search failed."


@pytest.mark.asyncio
async def test_deferred_catalog_call_uses_target_activity_presentation() -> None:
    search_tool = tool(
        "deferred_search",
        activity=AgentToolActivity(
            label="Searching the archive",
            detail="Research MCP",
            icon=ActivityIcon.MCP,
        ),
    )
    provider = RecordingToolProvider(
        tools=(
            search_tool,
            *(tool(f"specialist_{index}") for index in range(9)),
        ),
    )
    catalog = await RunScopedToolCatalogProvider.create(
        provider=provider,
        context=run_context(),
    )
    catalog_target = next(item for item in catalog.catalog_tools if item.tool.name == search_tool.name)
    messages: list[ProviderMessage] = []
    service = text_chat_service(provider)

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tool_provider=catalog,
            tools=catalog.visible_tools,
            tool_calls=(
                TextProviderToolCall(
                    call_id="call-deferred-search",
                    name=TOOL_CALL_NAME,
                    arguments_json={
                        "tool_id": catalog_target.id,
                        "arguments": {},
                    },
                ),
            ),
        )
    ]

    assert [event.payload["activity"] for event in events] == [
        {
            "label": "Searching the archive",
            "detail": "Research MCP",
            "icon": "mcp",
        },
        {
            "label": "Searching the archive",
            "detail": "Research MCP",
            "icon": "mcp",
        },
    ]
    assert provider.execution_log == [
        "start:deferred_search",
        "end:deferred_search",
    ]
    assert provider.list_calls == 1


@pytest.mark.asyncio
async def test_model_selected_unavailable_tool_is_recoverable() -> None:
    provider = UnavailableToolProvider()
    service = text_chat_service(provider)
    tool_call = TextProviderToolCall(
        call_id="call-unknown",
        name="generate_image<|channel|>commentary",
        arguments_json={
            "action": "generate",
            "prompt": "A fictional beach wedding",
        },
    )
    messages: list[ProviderMessage] = []

    events = [
        event
        async for event in service._execute_tool_calls(  # noqa: SLF001
            context=run_context(),
            messages=messages,
            tools=(tool("generate_image"),),
            tool_calls=(tool_call,),
        )
    ]

    assert [event.kind.value for event in events] == [
        "tool_call_started",
        "tool_call_failed",
    ]
    assert events[-1].payload["error_type"] == "ToolNotExposedError"
    assert "generate_image<|channel|>commentary" in events[-1].payload["error_message"]
    assert messages[-1].content == (
        "That tool name is not available in this run. Use exactly one of the listed "
        "tool names, or answer without calling a tool."
    )


def transcript(*, role: AgentTranscriptRole, text: str) -> AgentTranscriptRecord:
    return AgentTranscriptRecord(
        session_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        role=role.value,
        text=text,
        source="text_input",
        created_at=utc_now(),
    )


def tool(
    name: str,
    *,
    description: str | None = None,
    parameters_schema: dict[str, object] | None = None,
    authority_argument_allowed_paths: frozenset[tuple[str, ...]] = frozenset(),
    activity: AgentToolActivity | None = None,
    parallel_safe: bool = False,
) -> AgentTool:
    return AgentTool(
        name=name,
        description=description or f"Description for {name}",
        parameters_schema=parameters_schema or {"type": "object", "properties": {}},
        source=ToolGrantScope.INTERNAL,
        risk=ToolRisk.LOW,
        activity=activity,
        parallel_safe=parallel_safe,
        authority_argument_allowed_paths=authority_argument_allowed_paths,
    )


def prepared_tool_call(index: int) -> PreparedToolCall:
    tool_call = TextProviderToolCall(
        call_id=f"call-{index}",
        name="read",
        arguments_json={"index": index},
    )
    return PreparedToolCall(
        tool_call=tool_call,
        fingerprint=tool_call_fingerprint(tool_call),
    )


def run_context() -> AgentRunContext:
    return AgentRunContext(
        session_id="session-1",
        user_id="user-1",
        agent_id="agent-1",
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
    )


def text_chat_service(tool_provider: AgentToolProvider) -> TextChatService:
    runtime_store = Mock()
    runtime_store.append_runtime_event = AsyncMock()
    runtime_store.append_tool_event = AsyncMock()
    return TextChatService(
        session_factory=Mock(),
        secret_vault=Mock(),
        runtime_store=runtime_store,
        run_store=Mock(),
        run_chat_service=Mock(),
        effective_tool_policy_service=Mock(),
        instruction_provider=Mock(),
        tool_provider=tool_provider,
        provider_client=Mock(),
    )


class CoordinatedToolProvider:
    def __init__(self, *, expected_starts: int) -> None:
        self.expected_starts = expected_starts
        self.started = 0
        self.active = 0
        self.maximum_active = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled_call_ids: set[str] = set()

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, arguments_json
        self.started += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.started == self.expected_starts:
            self.all_started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled_call_ids.add(call_id)
            raise
        finally:
            self.active -= 1
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=call_id,
        )


class RecordingToolProvider:
    def __init__(self, *, tools: tuple[AgentTool, ...] = ()) -> None:
        self.execution_log: list[str] = []
        self.list_calls = 0
        self.tools = tools

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
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
        del context, arguments_json
        self.execution_log.append(f"start:{tool_name}")
        if tool_name != "write":
            await asyncio.sleep(0.01)
        self.execution_log.append(f"end:{tool_name}")
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=tool_name,
        )


class PartiallyUnavailableMcpToolProvider:
    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, arguments_json
        if tool_name == "rate-limited-search":
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


class UnexpectedFailureToolProvider:
    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, tool_name, call_id, arguments_json
        message = "task store unavailable"
        raise RuntimeError(message)


class PlaceCardToolProvider:
    def __init__(self, card: PlaceCardArtifact, *, is_error: bool = False) -> None:
        self.card = card
        self.is_error = is_error

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, arguments_json
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=("private partial place result" if self.is_error else "A nearby restaurant."),
            persisted_output_text=(
                "Google Maps place search failed." if self.is_error else "Google Maps place search completed."
            ),
            is_error=self.is_error,
            place_card_artifacts=() if self.is_error else (self.card,),
        )


class RejectingToolProvider:
    def __init__(self) -> None:
        self.call_ids: list[str] = []

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, tool_name, arguments_json
        self.call_ids.append(call_id)
        raise McpToolCallRejectedError


class ErrorResultToolProvider:
    error_output = '{"error":"checkin/checkout cannot be combined with flexible date filters."}'

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, arguments_json
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=self.error_output,
            is_error=True,
        )


class UnavailableToolProvider:
    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, call_id, arguments_json
        message = f"Tool is not exposed in this run: {tool_name}"
        raise ToolNotExposedError(message)


class SchemaRejectingCatalogProvider:
    def __init__(self) -> None:
        self.call_ids: list[str] = []

    async def list_tools(
        self,
        context: AgentRunContext,
    ) -> tuple[AgentTool, ...]:
        del context
        return ()

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        del context, tool_name, arguments_json
        self.call_ids.append(call_id)
        raise ToolArgumentSchemaError(
            path=("location",),
            validation_keyword="required",
            message="A required target argument is missing",
        )

import httpx
import orjson
import pytest
from pydantic import SecretStr

from voxos.agent_core.models import AgentTool
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolRisk
from voxos.agent_runtime.tool_routing import WEB_SEARCH_ACTION
from voxos.providers.model_catalog import NVIDIA_TEXT_MODEL_OPTIONS
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
    TrustedOutboundEndpoint,
    TrustedOutboundEndpointKind,
)
from voxos.text.inference_profiles import text_model_inference_profile
from voxos.text.models import TextProvider, TextProviderConfig, TextReasoningMode
from voxos.text.providers import (
    ChatCompletionsStreamAccumulator,
    OpenAICompatibleTextProvider,
    ProviderInputImage,
    ProviderInputText,
    ProviderMessage,
    ResponsesStreamAccumulator,
    TextProviderEventKind,
    TextProviderIncompleteResponseError,
    TextProviderRequest,
    TextProviderRequestError,
    TextProviderToolCall,
    TextProviderTransportError,
    TextProviderTransportErrorKind,
    TextToolChoice,
    base_url,
    chat_completions_events_from_chunk,
    request_payload,
    responses_events_from_chunk,
)


def local_text_endpoint_policy(url: str) -> OutboundEndpointPolicy:
    return OutboundEndpointPolicy(
        trusted_endpoints=(
            TrustedOutboundEndpoint(
                purpose=OutboundEndpointPurpose.TEXT_PROVIDER,
                kind=TrustedOutboundEndpointKind.LOCAL_CONTAINER,
                url=url,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_responses_events_accumulate_delta_usage_and_tool_calls() -> None:
    accumulator = ResponsesStreamAccumulator()
    function_call = {
        "type": "function_call",
        "id": "fc-1",
        "call_id": "call-123",
        "name": "list_agent_memories",
        "arguments": '{"request":null}',
        "status": "completed",
    }

    delta_events = [
        event
        async for event in responses_events_from_chunk(
            orjson.dumps(
                {"type": "response.output_text.delta", "delta": "Hel"},
            ).decode(),
            accumulator,
        )
    ]
    usage_events = [
        event
        async for event in responses_events_from_chunk(
            orjson.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "output": [function_call],
                        "usage": {
                            "input_tokens": 4,
                            "output_tokens": 2,
                            "total_tokens": 6,
                        },
                    },
                },
            ).decode(),
            accumulator,
        )
    ]

    assert delta_events[0].kind == TextProviderEventKind.MESSAGE_DELTA
    assert delta_events[0].delta == "Hel"
    assert usage_events[0].usage["total_tokens"] == 6
    tool_calls = accumulator.tool_calls()
    assert tool_calls[0].call_id == "call-123"
    assert tool_calls[0].name == "list_agent_memories"
    assert tool_calls[0].arguments_json == {"request": None}


def test_request_payload_includes_streaming_and_tool_schema() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.OPENAI,
            model="gpt-4.1-mini",
            credentials=SecretStr("sk-test"),
            config=TextProviderConfig(temperature=0.4),
            messages=(
                ProviderMessage(role="system", content="System"),
                ProviderMessage(role="user", content="Hello"),
            ),
            tools=(
                AgentTool(
                    name="list_agent_memories",
                    description="List memories.",
                    parameters_schema={"type": "object", "properties": {}},
                    source=ToolGrantScope.INTERNAL,
                    risk=ToolRisk.LOW,
                ),
            ),
            max_output_tokens=180,
        ),
    )

    assert payload["stream"] is True
    assert payload["store"] is False
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["input"] == [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "Hello"},
    ]
    assert payload["temperature"] == 0.4
    assert payload["tools"][0]["name"] == "list_agent_memories"
    assert payload["max_output_tokens"] == 180


@pytest.mark.parametrize("provider", [TextProvider.OPENAI, TextProvider.XAI])
def test_multimodal_request_payload_uses_ordered_responses_parts(
    provider: TextProvider,
) -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=provider,
            model="gpt-5.6-sol" if provider == TextProvider.OPENAI else "grok-4.5",
            credentials=SecretStr("provider-test"),
            config=TextProviderConfig(),
            messages=(
                ProviderMessage(
                    role="user",
                    content=(
                        ProviderInputText(text="Compare these"),
                        ProviderInputImage(
                            image_url="data:image/png;base64,aW1hZ2U=",
                        ),
                        ProviderInputImage(
                            image_url="data:image/jpeg;base64,aW1hZ2Uy",
                        ),
                    ),
                ),
            ),
        ),
    )

    assert payload["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Compare these"},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                },
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,aW1hZ2Uy",
                },
            ],
        },
    ]
    assert payload["store"] is False


def test_responses_request_payload_replays_continuation_after_transcript() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.OPENAI,
            model="gpt-5.6-sol",
            credentials=SecretStr("sk-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Remember this"),),
            continuation_items=(
                {
                    "type": "reasoning",
                    "id": "rs-1",
                    "encrypted_content": "encrypted-reasoning",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "Saved",
                },
            ),
        ),
    )

    assert payload["input"] == [
        {"role": "user", "content": "Remember this"},
        {
            "type": "reasoning",
            "id": "rs-1",
            "encrypted_content": "encrypted-reasoning",
        },
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "Saved",
        },
    ]


@pytest.mark.asyncio
async def test_openai_provider_streams_responses_and_preserves_tool_context() -> None:
    reasoning_item = {
        "type": "reasoning",
        "id": "rs-1",
        "encrypted_content": "encrypted-reasoning",
        "summary": [],
    }
    function_call_item = {
        "type": "function_call",
        "id": "fc-1",
        "call_id": "call-1",
        "name": "list_agent_memories",
        "arguments": "{}",
        "status": "completed",
    }
    chunks = (
        {"type": "response.output_text.delta", "delta": "Checking"},
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": reasoning_item,
        },
        {
            "type": "response.output_item.done",
            "output_index": 1,
            "item": function_call_item,
        },
        {
            "type": "response.completed",
            "response": {
                "output": [reasoning_item, function_call_item],
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "total_tokens": 12,
                },
            },
        },
    )
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        body = "".join(f"data: {orjson.dumps(chunk).decode()}\n\n" for chunk in chunks)
        return httpx.Response(status_code=200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        events = [
            event
            async for event in provider.stream_chat(
                TextProviderRequest(
                    provider=TextProvider.OPENAI,
                    model="gpt-5.6-sol",
                    credentials=SecretStr("sk-test"),
                    config=TextProviderConfig(),
                    messages=(ProviderMessage(role="user", content="Remember this"),),
                ),
            )
        ]

    assert captured_requests[0].url.path == "/v1/responses"
    request_body = orjson.loads(captured_requests[0].content)
    assert request_body["store"] is False
    assert request_body["include"] == ["reasoning.encrypted_content"]
    assert [event.kind for event in events] == [
        TextProviderEventKind.MESSAGE_DELTA,
        TextProviderEventKind.USAGE,
        TextProviderEventKind.TOOL_CALLS,
        TextProviderEventKind.RESPONSE_SUMMARY,
    ]
    assert events[-2].tool_calls == (
        TextProviderToolCall(
            call_id="call-1",
            name="list_agent_memories",
            arguments_json={},
        ),
    )
    assert events[-2].continuation_items == (reasoning_item, function_call_item)
    assert events[-1].response_summary is not None
    assert events[-1].response_summary.saw_content_delta is True
    assert events[-1].response_summary.saw_structured_tool_call is True


@pytest.mark.asyncio
async def test_openai_provider_reports_incomplete_response_with_usage() -> None:
    chunks = (
        {"type": "response.output_text.delta", "delta": "Partial"},
        {
            "type": "response.incomplete",
            "response": {
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {
                    "input_tokens": 8,
                    "output_tokens": 4,
                    "total_tokens": 12,
                },
            },
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(f"data: {orjson.dumps(chunk).decode()}\n\n" for chunk in chunks)
        return httpx.Response(status_code=200, text=body, request=request)

    events = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        with pytest.raises(
            TextProviderIncompleteResponseError,
            match="max_output_tokens",
        ):
            async for event in provider.stream_chat(
                TextProviderRequest(
                    provider=TextProvider.OPENAI,
                    model="gpt-5.4-nano",
                    credentials=SecretStr("sk-test"),
                    config=TextProviderConfig(),
                    messages=(ProviderMessage(role="user", content="List everything"),),
                ),
            ):
                events.append(event)

    assert [event.kind for event in events] == [
        TextProviderEventKind.MESSAGE_DELTA,
        TextProviderEventKind.USAGE,
    ]
    assert events[-1].usage["total_tokens"] == 12


@pytest.mark.asyncio
async def test_xai_provider_uses_responses_endpoint() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(status_code=200, text="data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        events = [
            event
            async for event in provider.stream_chat(
                TextProviderRequest(
                    provider=TextProvider.XAI,
                    model="grok-4.5",
                    credentials=SecretStr("xai-test"),
                    config=TextProviderConfig(),
                    messages=(ProviderMessage(role="user", content="Hello"),),
                ),
            )
        ]

    assert [event.kind for event in events] == [TextProviderEventKind.RESPONSE_SUMMARY]
    assert events[0].response_summary is not None
    assert events[0].response_summary.saw_content_delta is False
    assert captured_requests[0].url.path == "/v1/responses"


def test_xai_request_payload_uses_responses_contract() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.XAI,
            model="grok-3-mini",
            credentials=SecretStr("xai-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Hello"),),
            max_output_tokens=512,
        ),
    )

    assert payload["max_output_tokens"] == 512
    assert payload["input"] == [{"role": "user", "content": "Hello"}]
    assert payload["store"] is False
    assert "include" not in payload


def test_nvidia_request_payload_uses_chat_completions_contract() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="nvidia/nemotron-3-nano-30b-a3b",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(temperature=0.6),
            messages=(
                ProviderMessage(role="system", content="System"),
                ProviderMessage(role="user", content="Hello"),
            ),
            tools=(
                AgentTool(
                    name="list_agent_memories",
                    description="List memories.",
                    parameters_schema={"type": "object", "properties": {}},
                    source=ToolGrantScope.INTERNAL,
                    risk=ToolRisk.LOW,
                ),
            ),
            max_output_tokens=512,
        ),
    )

    assert payload == {
        "model": "nvidia/nemotron-3-nano-30b-a3b",
        "messages": [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "Hello"},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.6,
        "max_tokens": 512,
        # Interactive chat stays in instruct mode even when tools are visible.
        "chat_template_kwargs": {"enable_thinking": False},
        "top_p": 0.95,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "list_agent_memories",
                    "description": "List memories.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
        "tool_choice": "auto",
    }


def test_nvidia_payload_maps_explicit_tool_choice_and_keeps_fast_mode_without_tools() -> None:
    web_search = AgentTool(
        name="mcp_hash_web_search_exa",
        source_tool_name="web_search_exa",
        description="Search the web.",
        parameters_schema={"type": "object", "properties": {}},
        source=ToolGrantScope.MCP_SERVER,
        risk=ToolRisk.LOW,
        mcp_server_id="exa-server",
        route_action=WEB_SEARCH_ACTION,
        automatic_route_priority=10,
    )

    def payload_for(*, tools: tuple[AgentTool, ...], choice: TextToolChoice) -> dict[str, object]:
        return request_payload(
            TextProviderRequest(
                provider=TextProvider.NVIDIA,
                model="nvidia/nemotron-3-nano-30b-a3b",
                credentials=SecretStr("nvidia-test"),
                config=TextProviderConfig(),
                messages=(ProviderMessage(role="user", content="Hello"),),
                tools=tools,
                tool_choice=choice,
            ),
        )

    # Required mode remains available for explicit callers even though ordinary chat
    # requests leave capability selection to the model.
    opening_payload = payload_for(tools=(web_search,), choice=TextToolChoice.REQUIRED)
    assert opening_payload["tool_choice"] == "required"
    # NVIDIA's documented tool-calling profile fills in for an unset user temperature.
    assert opening_payload["temperature"] == 0.6
    assert opening_payload["top_p"] == 0.95
    assert opening_payload["chat_template_kwargs"] == {"enable_thinking": False}

    automatic_payload = payload_for(tools=(web_search,), choice=TextToolChoice.AUTO)
    assert automatic_payload["tool_choice"] == "auto"
    assert automatic_payload["chat_template_kwargs"] == {"enable_thinking": False}

    # With the tool budget spent there is nothing to call, so the latency-oriented
    # fast profile is left alone.
    exhausted_payload = payload_for(tools=(), choice=TextToolChoice.REQUIRED)
    assert "tool_choice" not in exhausted_payload
    assert "temperature" not in exhausted_payload
    assert "top_p" not in exhausted_payload
    assert exhausted_payload["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.parametrize("with_tools", [False, True])
def test_nvidia_omni_uses_exact_instruct_profile(with_tools: bool) -> None:
    tools = (
        (
            AgentTool(
                name="search",
                description="Search the web.",
                source=ToolGrantScope.MCP_SERVER,
                risk=ToolRisk.LOW,
            ),
        )
        if with_tools
        else ()
    )

    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Hello"),),
            tools=tools,
        ),
    )

    assert payload["temperature"] == 0.2
    assert payload["top_k"] == 1
    assert "top_p" not in payload
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_nvidia_omni_uses_exact_thinking_profile_for_agentic_runs() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Investigate"),),
            tools=(
                AgentTool(
                    name="search",
                    description="Search the web.",
                    source=ToolGrantScope.MCP_SERVER,
                    risk=ToolRisk.LOW,
                ),
            ),
            reasoning_mode=TextReasoningMode.AGENTIC,
        ),
    )

    assert payload["temperature"] == 0.6
    assert payload["top_p"] == 0.95
    assert "top_k" not in payload
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}


def test_nvidia_does_not_apply_nemotron_sampling_to_other_catalog_models() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="moonshotai/kimi-k2.6",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Hello"),),
            tools=(
                AgentTool(
                    name="search",
                    description="Search the web.",
                    source=ToolGrantScope.MCP_SERVER,
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "chat_template_kwargs" not in payload


def test_nvidia_does_not_apply_profile_to_an_unknown_nemotron_prefix() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="nvidia/nemotron-3-nano-future-model",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Hello"),),
            tools=(
                AgentTool(
                    name="search",
                    description="Search the web.",
                    source=ToolGrantScope.MCP_SERVER,
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload
    assert "chat_template_kwargs" not in payload


def test_nvidia_catalog_nemotron_models_have_explicit_inference_profiles() -> None:
    nemotron_models = {
        option.model for option in NVIDIA_TEXT_MODEL_OPTIONS if option.model.startswith("nvidia/nemotron-")
    }

    assert nemotron_models
    assert all(text_model_inference_profile(TextProvider.NVIDIA, model) is not None for model in nemotron_models)


def test_nvidia_does_not_apply_nano_sampling_to_larger_nemotron_models() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.NVIDIA,
            model="nvidia/nemotron-3-super-120b-a12b",
            credentials=SecretStr("nvidia-test"),
            config=TextProviderConfig(),
            messages=(ProviderMessage(role="user", content="Hello"),),
            tools=(
                AgentTool(
                    name="search",
                    description="Search the web.",
                    source=ToolGrantScope.MCP_SERVER,
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    assert "temperature" not in payload
    assert "top_p" not in payload
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_nvidia_uses_its_fixed_catalog_base_url() -> None:
    request = TextProviderRequest(
        provider=TextProvider.NVIDIA,
        model="nvidia/nemotron-3-nano-30b-a3b",
        credentials=SecretStr("nvidia-test"),
        config=TextProviderConfig(base_url="https://example.test/v1"),
        messages=(ProviderMessage(role="user", content="Hello"),),
    )

    assert base_url(request) == "https://integrate.api.nvidia.com/v1"


@pytest.mark.asyncio
async def test_openai_compatible_provider_uses_custom_endpoint_without_auth() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        body = (
            'data: {"choices":[{"delta":{"content":"Local"},"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(status_code=200, text=body)

    request = TextProviderRequest(
        provider=TextProvider.OPENAI_COMPATIBLE,
        model="gpt-oss:20b",
        config=TextProviderConfig(
            base_url="http://model-host:11434/v1",
            available_models=("gpt-oss:20b",),
        ),
        messages=(ProviderMessage(role="user", content="Hello"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(
            http_client=http_client,
            outbound_endpoint_policy=local_text_endpoint_policy(
                "http://model-host:11434/v1",
            ),
        )
        events = [event async for event in provider.stream_chat(request)]

    assert captured_requests[0].url == "http://model-host:11434/v1/chat/completions"
    assert "Authorization" not in captured_requests[0].headers
    assert any(event.delta == "Local" for event in events)


@pytest.mark.asyncio
async def test_openai_compatible_provider_rejects_endpoint_redirects() -> None:
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        if request.url.host == "model-host":
            return httpx.Response(
                status_code=307,
                headers={
                    "Location": "http://redirected-model-host:8080/openai/chat/completions",
                },
            )
        return httpx.Response(
            status_code=200,
            text=('data: {"choices":[{"delta":{"content":"Redirected"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'),
        )

    request = TextProviderRequest(
        provider=TextProvider.OPENAI_COMPATIBLE,
        model="local-model",
        config=TextProviderConfig(base_url="http://model-host:11434/v1"),
        messages=(ProviderMessage(role="user", content="Hello"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(
            http_client=http_client,
            outbound_endpoint_policy=local_text_endpoint_policy(
                "http://model-host:11434/v1",
            ),
        )
        with pytest.raises(TextProviderRequestError, match="HTTP 307"):
            _ = [event async for event in provider.stream_chat(request)]

    assert [str(request.url) for request in captured_requests] == [
        "http://model-host:11434/v1/chat/completions",
    ]


@pytest.mark.asyncio
async def test_nvidia_provider_reports_retired_model_with_actionable_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=410)

    request = TextProviderRequest(
        provider=TextProvider.NVIDIA,
        model="deepseek-ai/deepseek-v4-flash",
        credentials=SecretStr("nvidia-test"),
        config=TextProviderConfig(),
        messages=(ProviderMessage(role="user", content="Hello"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        with pytest.raises(
            TextProviderRequestError,
            match=(
                "NVIDIA API Catalog rejected model 'deepseek-ai/deepseek-v4-flash' "
                r"\(HTTP 410 Gone\).*choose a current model"
            ),
        ):
            async for _event in provider.stream_chat(request):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_error_type", "expected_kind", "message_fragment"),
    [
        (
            httpx.ReadTimeout,
            TextProviderTransportErrorKind.TIMEOUT,
            "did not respond in time",
        ),
        (
            httpx.ConnectError,
            TextProviderTransportErrorKind.CONNECTION,
            "could not be reached",
        ),
    ],
)
async def test_provider_sanitizes_transport_failures(
    transport_error_type: type[httpx.TransportError],
    expected_kind: TextProviderTransportErrorKind,
    message_fragment: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "sensitive upstream transport detail"
        raise transport_error_type(
            message,
            request=request,
        )

    request = TextProviderRequest(
        provider=TextProvider.NVIDIA,
        model="openai/gpt-oss-20b",
        credentials=SecretStr("nvidia-test"),
        config=TextProviderConfig(),
        messages=(ProviderMessage(role="user", content="Hello"),),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        with pytest.raises(TextProviderTransportError) as exc_info:
            async for _event in provider.stream_chat(request):
                pass

    error = exc_info.value
    assert error.kind == expected_kind
    assert error.provider == TextProvider.NVIDIA
    assert error.model == "openai/gpt-oss-20b"
    assert message_fragment in str(error)
    assert "sensitive upstream transport detail" not in str(error)
    assert "nvidia-test" not in str(error)
    assert isinstance(error.__cause__, transport_error_type)


@pytest.mark.asyncio
async def test_nvidia_provider_streams_chat_completions_and_tool_context() -> None:
    chunks = (
        {
            "choices": [
                {
                    "delta": {"reasoning_content": "I should inspect memories."},
                    "finish_reason": None,
                },
            ],
        },
        {"choices": [{"delta": {"content": "Checking"}, "finish_reason": None}]},
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "list_agent_memories",
                                    "arguments": '{"request":',
                                },
                            },
                        ],
                    },
                    "finish_reason": None,
                },
            ],
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": "null}"},
                            },
                        ],
                    },
                    "finish_reason": "tool_calls",
                },
            ],
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
        },
    )
    captured_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        body = "".join(f"data: {orjson.dumps(chunk).decode()}\n\n" for chunk in chunks)
        return httpx.Response(status_code=200, text=f"{body}data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        events = [
            event
            async for event in provider.stream_chat(
                TextProviderRequest(
                    provider=TextProvider.NVIDIA,
                    model="nvidia/nemotron-3-nano-30b-a3b",
                    credentials=SecretStr("nvidia-test"),
                    config=TextProviderConfig(),
                    messages=(ProviderMessage(role="user", content="Remember this"),),
                    reasoning_mode=TextReasoningMode.AGENTIC,
                ),
            )
        ]

    assert captured_requests[0].url.path == "/v1/chat/completions"
    assert captured_requests[0].headers["authorization"] == "Bearer nvidia-test"
    request_body = orjson.loads(captured_requests[0].content)
    assert request_body["stream_options"] == {"include_usage": True}
    assert request_body["chat_template_kwargs"] == {"enable_thinking": True}
    assert [event.kind for event in events] == [
        TextProviderEventKind.MESSAGE_DELTA,
        TextProviderEventKind.USAGE,
        TextProviderEventKind.TOOL_CALLS,
        TextProviderEventKind.RESPONSE_SUMMARY,
    ]
    assert events[-2].tool_calls == (
        TextProviderToolCall(
            call_id="call-1",
            name="list_agent_memories",
            arguments_json={"request": None},
        ),
    )
    assert events[-2].continuation_items == (
        {
            "role": "assistant",
            "content": "Checking",
            "reasoning_content": "I should inspect memories.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "list_agent_memories",
                        "arguments": '{"request":null}',
                    },
                },
            ],
        },
    )
    assert events[-1].response_summary is not None
    assert events[-1].response_summary.saw_reasoning_content is True
    assert events[-1].response_summary.saw_content_delta is True
    assert events[-1].response_summary.saw_structured_tool_call is True


@pytest.mark.asyncio
async def test_nvidia_provider_canonicalizes_harmony_suffix_for_advertised_tool() -> None:
    chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-retry",
                            "type": "function",
                            "function": {
                                "name": "generate_image<|channel|>commentary",
                                "arguments": '{"action":"generate","prompt":"A fictional couple"}',
                            },
                        },
                    ],
                },
                "finish_reason": "tool_calls",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        body = f"data: {orjson.dumps(chunk).decode()}\n\ndata: [DONE]\n\n"
        return httpx.Response(status_code=200, text=body)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        provider = OpenAICompatibleTextProvider(http_client=http_client)
        events = [
            event
            async for event in provider.stream_chat(
                TextProviderRequest(
                    provider=TextProvider.NVIDIA,
                    model="openai/gpt-oss-20b",
                    credentials=SecretStr("nvidia-test"),
                    config=TextProviderConfig(),
                    messages=(ProviderMessage(role="user", content="Try again"),),
                    tools=(
                        AgentTool(
                            name="generate_image",
                            description="Generate an image",
                            source=ToolGrantScope.INTERNAL,
                            risk=ToolRisk.MEDIUM,
                        ),
                    ),
                ),
            )
        ]

    tool_event = next(event for event in events if event.kind == TextProviderEventKind.TOOL_CALLS)
    assert tool_event.tool_calls == (
        TextProviderToolCall(
            call_id="call-retry",
            name="generate_image",
            arguments_json={
                "action": "generate",
                "prompt": "A fictional couple",
            },
        ),
    )
    assert tool_event.continuation_items[0]["tool_calls"] == [
        {
            "id": "call-retry",
            "type": "function",
            "function": {
                "name": "generate_image",
                "arguments": '{"action":"generate","prompt":"A fictional couple"}',
            },
        },
    ]


@pytest.mark.parametrize(
    "raw_name",
    [
        "other_tool<|channel|>commentary",
        "generate_image<|channel|>commentary-extra",
    ],
)
def test_chat_completions_keeps_nonmatching_harmony_tool_name(
    raw_name: str,
) -> None:
    accumulator = ChatCompletionsStreamAccumulator(
        advertised_tool_names=frozenset({"generate_image"}),
    )
    accumulator.append_tool_call(
        0,
        {
            "id": "call-unknown",
            "function": {"name": raw_name, "arguments": "{}"},
        },
    )

    assert accumulator.tool_calls()[0].name == raw_name


@pytest.mark.asyncio
async def test_nvidia_provider_reports_length_response_as_incomplete() -> None:
    accumulator = ChatCompletionsStreamAccumulator()
    events = []

    with pytest.raises(TextProviderIncompleteResponseError, match="length"):
        async for event in chat_completions_events_from_chunk(
            orjson.dumps(
                {
                    "choices": [
                        {
                            "delta": {"content": "Partial"},
                            "finish_reason": "length",
                        },
                    ],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                    },
                },
            ).decode(),
            accumulator,
        ):
            events.append(event)

    assert [event.kind for event in events] == [
        TextProviderEventKind.MESSAGE_DELTA,
        TextProviderEventKind.USAGE,
    ]
    assert events[-1].usage["total_tokens"] == 12


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.5"])
def test_default_reasoning_models_omit_temperature(model: str) -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.OPENAI,
            model=model,
            credentials=SecretStr("sk-test"),
            config=TextProviderConfig(temperature=0.4),
            messages=(ProviderMessage(role="user", content="Hello"),),
        ),
    )

    assert "temperature" not in payload


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-5.4-nano"])
def test_gpt_5_4_small_models_enable_reasoning_for_tool_calls(model: str) -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.OPENAI,
            model=model,
            credentials=SecretStr("sk-test"),
            config=TextProviderConfig(temperature=0.4),
            messages=(ProviderMessage(role="user", content="Hello"),),
            tools=(
                AgentTool(
                    name="list_agent_memories",
                    description="List memories.",
                    parameters_schema={"type": "object", "properties": {}},
                    source=ToolGrantScope.INTERNAL,
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    assert payload["reasoning"] == {"effort": "low"}
    assert "temperature" not in payload


def test_gpt_5_4_mini_without_tools_preserves_temperature() -> None:
    payload = request_payload(
        TextProviderRequest(
            provider=TextProvider.OPENAI,
            model="gpt-5.4-mini",
            credentials=SecretStr("sk-test"),
            config=TextProviderConfig(temperature=0.4),
            messages=(ProviderMessage(role="user", content="Hello"),),
        ),
    )

    assert payload["temperature"] == 0.4
    assert "reasoning" not in payload

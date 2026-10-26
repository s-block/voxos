from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Annotated, Literal, Protocol

import httpx
import orjson
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from voxos.agent_core.models import AgentTool
from voxos.agent_runtime.models import JsonObject
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
)
from voxos.text.inference_profiles import text_model_inference_profile
from voxos.text.models import (
    TextProvider,
    TextProviderConfig,
    TextProviderProtocol,
    TextReasoningMode,
    text_provider_definition,
    validate_text_provider_temperature,
)

HTTP_CLIENT = httpx.AsyncClient()
TEXT_PROVIDER_TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=120.0,
    write=30.0,
    pool=10.0,
)
_KNOWN_INCOMPLETE_REASONS = frozenset(
    {
        "content_filter",
        "length",
        "max_output_tokens",
        "max_tokens",
    }
)


class TextProviderIncompleteResponseError(RuntimeError):
    """Raised when a provider explicitly terminates a response as incomplete."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Text provider response was incomplete: {reason}")


class TextProviderRequestError(RuntimeError):
    """A safe, actionable provider HTTP failure for runtime event surfaces."""

    def __init__(
        self,
        *,
        provider: TextProvider,
        model: str,
        status_code: int,
        reason_phrase: str,
    ) -> None:
        self.provider = provider
        self.model = model
        self.status_code = status_code
        message = (
            f"{text_provider_definition(provider).label} rejected model '{model}' (HTTP {status_code} {reason_phrase})."
        )
        if status_code == httpx.codes.GONE:
            message = f"{message} The model is no longer available through this endpoint; choose a current model."
        elif status_code == httpx.codes.TOO_MANY_REQUESTS:
            message = f"{message} The endpoint is rate limited; try again later."
        super().__init__(message)


class TextProviderTransportErrorKind(StrEnum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"


class TextProviderTransportError(RuntimeError):
    """A sanitized, retryable provider transport failure."""

    def __init__(
        self,
        *,
        provider: TextProvider,
        model: str,
        kind: TextProviderTransportErrorKind,
    ) -> None:
        self.provider = provider
        self.model = model
        self.kind = kind
        provider_label = text_provider_definition(provider).label
        if kind == TextProviderTransportErrorKind.TIMEOUT:
            message = (
                f"{provider_label} did not respond in time for model '{model}'. Try again or select another model."
            )
        else:
            message = f"{provider_label} could not be reached for model '{model}'. Try again or select another model."
        super().__init__(message)


class TextToolChoice(StrEnum):
    """How much discretion the model has over calling a tool this round.

    REQUIRED does not name a tool. Callers that need an exact capability narrow the
    attached tool set to that capability before using it.
    """

    AUTO = "auto"
    REQUIRED = "required"


class TextProviderEventKind(StrEnum):
    MESSAGE_DELTA = "message_delta"
    TOOL_CALLS = "tool_calls"
    USAGE = "usage"
    RESPONSE_SUMMARY = "response_summary"


class TextProviderResponseSummary(BaseModel):
    """Safe, content-free facts about one streamed provider response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    saw_content_delta: bool = False
    saw_reasoning_content: bool = False
    saw_structured_tool_call: bool = False


class TextProviderToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    arguments_json: JsonObject = Field(default_factory=dict)


class TextProviderStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: TextProviderEventKind
    delta: str | None = None
    tool_calls: tuple[TextProviderToolCall, ...] = Field(default_factory=tuple)
    continuation_items: tuple[JsonObject, ...] = Field(default_factory=tuple)
    usage: JsonObject = Field(default_factory=dict)
    response_summary: TextProviderResponseSummary | None = None


class ProviderInputText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["input_text"] = "input_text"
    text: str = Field(min_length=1)


class ProviderInputImage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["input_image"] = "input_image"
    image_url: str = Field(min_length=1)


ProviderContentPart = Annotated[
    ProviderInputText | ProviderInputImage,
    Field(discriminator="type"),
]


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str | tuple[ProviderContentPart, ...]
    tool_call_id: str | None = None
    tool_calls: tuple[JsonObject, ...] = Field(default_factory=tuple)
    discard_when_tools_unavailable: bool = Field(default=False, exclude=True)

    def content_payload(self) -> object:
        if isinstance(self.content, str):
            return self.content
        return [part.model_dump(mode="json") for part in self.content]


class TextProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: TextProvider
    model: str = Field(min_length=1, max_length=256)
    credentials: SecretStr | None = None
    config: TextProviderConfig
    messages: tuple[ProviderMessage, ...]
    continuation_items: tuple[JsonObject, ...] = Field(default_factory=tuple)
    tools: tuple[AgentTool, ...] = Field(default_factory=tuple)
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    reasoning_mode: TextReasoningMode = TextReasoningMode.FAST
    tool_choice: TextToolChoice = TextToolChoice.AUTO

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "TextProviderRequest":
        validate_text_provider_temperature(
            self.provider,
            self.config.temperature,
        )
        return self


class TextProviderClient(Protocol):
    def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        """Stream provider response events."""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleTextProvider(TextProviderClient):
    http_client: httpx.AsyncClient = HTTP_CLIENT
    outbound_endpoint_policy: OutboundEndpointPolicy = OutboundEndpointPolicy()

    async def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        try:
            protocol = text_provider_definition(request.provider).protocol
            if protocol == TextProviderProtocol.RESPONSES:
                async for event in self._stream_responses(request):
                    yield event
                return
            async for event in self._stream_chat_completions(request):
                yield event
        except httpx.TimeoutException as exc:
            raise TextProviderTransportError(
                provider=request.provider,
                model=request.model,
                kind=TextProviderTransportErrorKind.TIMEOUT,
            ) from exc
        except httpx.TransportError as exc:
            raise TextProviderTransportError(
                provider=request.provider,
                model=request.model,
                kind=TextProviderTransportErrorKind.CONNECTION,
            ) from exc

    async def _stream_responses(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        resolved_base_url = await self._resolved_base_url(request)
        url = f"{resolved_base_url}/responses"
        payload = request_payload(request)
        headers = provider_headers(request.credentials)
        async with self.http_client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=TEXT_PROVIDER_TIMEOUT,
            follow_redirects=False,
        ) as response:
            raise_for_provider_status(response, request)
            accumulator = ResponsesStreamAccumulator()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                async for event in responses_events_from_chunk(data, accumulator):
                    yield event
            tool_calls = accumulator.tool_calls()
            if tool_calls:
                yield TextProviderStreamEvent(
                    kind=TextProviderEventKind.TOOL_CALLS,
                    tool_calls=tool_calls,
                    continuation_items=accumulator.output_items(),
                )
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.RESPONSE_SUMMARY,
                response_summary=accumulator.response_summary(),
            )

    async def _stream_chat_completions(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        resolved_base_url = await self._resolved_base_url(request)
        url = f"{resolved_base_url}/chat/completions"
        payload = request_payload(request)
        headers = provider_headers(request.credentials)
        async with self.http_client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
            timeout=TEXT_PROVIDER_TIMEOUT,
            follow_redirects=False,
        ) as response:
            raise_for_provider_status(response, request)
            accumulator = ChatCompletionsStreamAccumulator(
                advertised_tool_names=frozenset(tool.name for tool in request.tools),
            )
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                async for event in chat_completions_events_from_chunk(data, accumulator):
                    yield event
            tool_calls = accumulator.tool_calls()
            if tool_calls:
                yield TextProviderStreamEvent(
                    kind=TextProviderEventKind.TOOL_CALLS,
                    tool_calls=tool_calls,
                    continuation_items=accumulator.continuation_items(),
                )
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.RESPONSE_SUMMARY,
                response_summary=accumulator.response_summary(),
            )

    async def _resolved_base_url(
        self,
        request: TextProviderRequest,
    ) -> str:
        configured_base_url = base_url(request).rstrip("/")
        if request.provider != TextProvider.OPENAI_COMPATIBLE:
            return configured_base_url
        endpoint = await self.outbound_endpoint_policy.resolve_url(
            configured_base_url,
            OutboundEndpointPurpose.TEXT_PROVIDER,
        )
        return endpoint.url.rstrip("/")


def provider_headers(credentials: SecretStr | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if credentials is not None:
        headers["Authorization"] = f"Bearer {credentials.get_secret_value()}"
    return headers


def raise_for_provider_status(
    response: httpx.Response,
    request: TextProviderRequest,
) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise TextProviderRequestError(
            provider=request.provider,
            model=request.model,
            status_code=response.status_code,
            reason_phrase=response.reason_phrase,
        ) from exc


class ResponsesStreamAccumulator:
    def __init__(self) -> None:
        self._output_items: dict[int, JsonObject] = {}
        self._saw_content_delta = False

    def append_content_delta(self) -> None:
        self._saw_content_delta = True

    def append_output_item(self, output_index: int, item: JsonObject) -> None:
        self._output_items[output_index] = item

    def replace_output_items(self, items: list[object]) -> None:
        self._output_items = {index: json_object(item) for index, item in enumerate(items) if isinstance(item, dict)}

    def output_items(self) -> tuple[JsonObject, ...]:
        return tuple(self._output_items[index] for index in sorted(self._output_items))

    def tool_calls(self) -> tuple[TextProviderToolCall, ...]:
        calls: list[TextProviderToolCall] = []
        for item in self.output_items():
            if item.get("type") != "function_call":
                continue
            call_id = item.get("call_id")
            name = item.get("name")
            arguments = item.get("arguments")
            if not isinstance(call_id, str) or not call_id:
                continue
            if not isinstance(name, str) or not name:
                continue
            calls.append(
                TextProviderToolCall(
                    call_id=call_id,
                    name=name,
                    arguments_json=parse_arguments(arguments if isinstance(arguments, str) else ""),
                ),
            )
        return tuple(calls)

    def response_summary(self) -> TextProviderResponseSummary:
        return TextProviderResponseSummary(
            saw_content_delta=self._saw_content_delta,
            saw_structured_tool_call=bool(self.tool_calls()),
        )


@dataclass(slots=True)
class ChatCompletionsToolCallAccumulator:
    advertised_tool_names: frozenset[str] = frozenset()
    call_id: str | None = None
    name: str | None = None
    arguments_parts: list[str] = field(default_factory=list)

    def append(self, tool_call: object) -> None:
        if not isinstance(tool_call, dict):
            return
        call_id = tool_call.get("id")
        if isinstance(call_id, str) and call_id:
            self.call_id = call_id
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return
        name = function.get("name")
        if isinstance(name, str) and name:
            self.name = name
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self.arguments_parts.append(arguments)

    def tool_call(self) -> TextProviderToolCall | None:
        if self.call_id is None or self.name is None:
            return None
        return TextProviderToolCall(
            call_id=self.call_id,
            name=canonical_chat_completions_tool_name(
                self.name,
                advertised_tool_names=self.advertised_tool_names,
            ),
            arguments_json=parse_arguments("".join(self.arguments_parts)),
        )


class ChatCompletionsStreamAccumulator:
    def __init__(
        self,
        *,
        advertised_tool_names: frozenset[str] = frozenset(),
    ) -> None:
        self._content_parts: list[str] = []
        self._reasoning_content_parts: list[str] = []
        self._tool_calls: dict[int, ChatCompletionsToolCallAccumulator] = {}
        self._advertised_tool_names = advertised_tool_names

    def append_content(self, content: str) -> None:
        self._content_parts.append(content)

    def append_reasoning_content(self, reasoning_content: str) -> None:
        self._reasoning_content_parts.append(reasoning_content)

    def append_tool_call(self, index: int, tool_call: object) -> None:
        accumulator = self._tool_calls.setdefault(
            index,
            ChatCompletionsToolCallAccumulator(
                advertised_tool_names=self._advertised_tool_names,
            ),
        )
        accumulator.append(tool_call)

    def tool_calls(self) -> tuple[TextProviderToolCall, ...]:
        return tuple(
            tool_call
            for index in sorted(self._tool_calls)
            if (tool_call := self._tool_calls[index].tool_call()) is not None
        )

    def response_summary(self) -> TextProviderResponseSummary:
        return TextProviderResponseSummary(
            saw_content_delta=bool(self._content_parts),
            saw_reasoning_content=bool(self._reasoning_content_parts),
            saw_structured_tool_call=bool(self.tool_calls()),
        )

    def continuation_items(self) -> tuple[JsonObject, ...]:
        tool_calls = self.tool_calls()
        if not tool_calls:
            return ()
        content = "".join(self._content_parts)
        reasoning_content = "".join(self._reasoning_content_parts)
        return (
            chat_completions_assistant_tool_call_item(
                tool_calls,
                content=content or None,
                reasoning_content=reasoning_content or None,
            ),
        )


_HARMONY_TOOL_NAME_CHANNEL_SUFFIXES = (
    "<|channel|>analysis",
    "<|channel|>commentary",
    "<|channel|>final",
)


def canonical_chat_completions_tool_name(
    name: str,
    *,
    advertised_tool_names: frozenset[str],
) -> str:
    if name in advertised_tool_names:
        return name
    for suffix in _HARMONY_TOOL_NAME_CHANNEL_SUFFIXES:
        if not name.endswith(suffix):
            continue
        candidate = name.removesuffix(suffix)
        if candidate in advertised_tool_names:
            return candidate
    return name


async def responses_events_from_chunk(
    data: str,
    accumulator: ResponsesStreamAccumulator,
) -> AsyncIterator[TextProviderStreamEvent]:
    chunk = orjson.loads(data)
    if not isinstance(chunk, dict):
        return
    event_type = chunk.get("type")
    if event_type in {"error", "response.failed"}:
        raise RuntimeError(response_error_message(chunk))
    if event_type == "response.incomplete":
        response = chunk.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict):
                yield TextProviderStreamEvent(
                    kind=TextProviderEventKind.USAGE,
                    usage=json_object(usage),
                )
        raise TextProviderIncompleteResponseError(
            response_incomplete_reason(response),
        )
    if event_type == "response.output_text.delta":
        delta = chunk.get("delta")
        if isinstance(delta, str) and delta:
            accumulator.append_content_delta()
            yield TextProviderStreamEvent(
                kind=TextProviderEventKind.MESSAGE_DELTA,
                delta=delta,
            )
        return
    if event_type == "response.output_item.done":
        output_index = chunk.get("output_index")
        item = chunk.get("item")
        if isinstance(output_index, int) and isinstance(item, dict):
            accumulator.append_output_item(output_index, json_object(item))
        return
    if event_type != "response.completed":
        return
    response = chunk.get("response")
    if not isinstance(response, dict):
        return
    output = response.get("output")
    if isinstance(output, list):
        accumulator.replace_output_items(output)
    usage = response.get("usage")
    if isinstance(usage, dict):
        yield TextProviderStreamEvent(
            kind=TextProviderEventKind.USAGE,
            usage=json_object(usage),
        )


async def chat_completions_events_from_chunk(
    data: str,
    accumulator: ChatCompletionsStreamAccumulator,
) -> AsyncIterator[TextProviderStreamEvent]:
    chunk = orjson.loads(data)
    if not isinstance(chunk, dict):
        return
    if isinstance(chunk.get("error"), dict):
        raise RuntimeError(chat_completions_error_message(chunk))

    incomplete_reason: str | None = None
    choices = chunk.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    accumulator.append_content(content)
                    yield TextProviderStreamEvent(
                        kind=TextProviderEventKind.MESSAGE_DELTA,
                        delta=content,
                    )
                reasoning_content = delta.get("reasoning_content")
                if isinstance(reasoning_content, str) and reasoning_content:
                    accumulator.append_reasoning_content(reasoning_content)
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    for default_index, tool_call in enumerate(tool_calls):
                        index = tool_call.get("index") if isinstance(tool_call, dict) else None
                        accumulator.append_tool_call(
                            index if isinstance(index, int) and index >= 0 else default_index,
                            tool_call,
                        )
            finish_reason = choice.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason in _KNOWN_INCOMPLETE_REASONS:
                incomplete_reason = finish_reason

    usage = chunk.get("usage")
    if isinstance(usage, dict):
        yield TextProviderStreamEvent(
            kind=TextProviderEventKind.USAGE,
            usage=json_object(usage),
        )
    if incomplete_reason is not None:
        raise TextProviderIncompleteResponseError(incomplete_reason)


def response_incomplete_reason(response: object) -> str:
    if not isinstance(response, dict):
        return "unknown"
    details = response.get("incomplete_details")
    if not isinstance(details, dict):
        return "unknown"
    reason = details.get("reason")
    if not isinstance(reason, str) or reason not in _KNOWN_INCOMPLETE_REASONS:
        return "unknown"
    return reason


def request_payload(request: TextProviderRequest) -> JsonObject:
    protocol = text_provider_definition(request.provider).protocol
    if protocol == TextProviderProtocol.RESPONSES:
        return responses_request_payload(request)
    return chat_completions_request_payload(request)


def responses_request_payload(request: TextProviderRequest) -> JsonObject:
    input_items: list[JsonObject] = [
        {
            "role": message.role,
            "content": message.content_payload(),
        }
        for message in request.messages
    ]
    input_items.extend(request.continuation_items)
    payload: JsonObject = {
        "model": request.model,
        "input": input_items,
        "stream": True,
        "store": False,
    }
    if request.provider == TextProvider.OPENAI:
        payload["include"] = ["reasoning.encrypted_content"]
    if request.config.temperature is not None and supports_temperature(request):
        payload["temperature"] = request.config.temperature
    if request.max_output_tokens is not None:
        payload["max_output_tokens"] = request.max_output_tokens
    if request.tools:
        if requires_explicit_tool_reasoning(request):
            payload["reasoning"] = {"effort": "low"}
        payload["tools"] = [responses_tool_schema(tool) for tool in request.tools]
        payload["tool_choice"] = request.tool_choice.value
    return payload


def chat_completions_request_payload(request: TextProviderRequest) -> JsonObject:
    messages: list[JsonObject] = [chat_completions_message(message) for message in request.messages]
    messages.extend(request.continuation_items)
    payload: JsonObject = {
        "model": request.model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request.config.temperature is not None:
        payload["temperature"] = request.config.temperature
    if request.max_output_tokens is not None:
        payload["max_tokens"] = request.max_output_tokens
    if request.provider == TextProvider.NVIDIA:
        apply_nvidia_request_options(payload, request)
    if request.tools:
        payload["tools"] = [chat_completions_tool_schema(tool) for tool in request.tools]
        payload["tool_choice"] = request.tool_choice.value
    return payload


def apply_nvidia_request_options(payload: JsonObject, request: TextProviderRequest) -> None:
    """Apply an exact NVIDIA model profile without family-prefix leakage."""

    profile = text_model_inference_profile(request.provider, request.model)
    if profile is None:
        return
    if profile.supports_thinking_mode:
        payload["chat_template_kwargs"] = {
            "enable_thinking": request.reasoning_mode == TextReasoningMode.AGENTIC,
        }
    sampling = profile.sampling_for(
        reasoning_mode=request.reasoning_mode,
        has_tools=bool(request.tools),
    )
    if sampling is None:
        return
    if request.config.temperature is None and sampling.temperature is not None:
        payload["temperature"] = sampling.temperature
    if sampling.top_p is not None:
        payload["top_p"] = sampling.top_p
    if sampling.top_k is not None:
        payload["top_k"] = sampling.top_k


def chat_completions_message(message: ProviderMessage) -> JsonObject:
    payload: JsonObject = {
        "role": message.role,
        "content": chat_completions_content(message.content),
    }
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = list(message.tool_calls)
    return payload


def chat_completions_content(
    content: str | tuple[ProviderContentPart, ...],
) -> str | list[JsonObject]:
    if isinstance(content, str):
        return content
    parts: list[JsonObject] = []
    for part in content:
        if isinstance(part, ProviderInputText):
            parts.append({"type": "text", "text": part.text})
            continue
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": part.image_url},
            },
        )
    return parts


def supports_temperature(request: TextProviderRequest) -> bool:
    if request.provider != TextProvider.OPENAI:
        return True
    if request.model.startswith(("gpt-5.6", "gpt-5.5")):
        return False
    return not requires_explicit_tool_reasoning(request)


def requires_explicit_tool_reasoning(request: TextProviderRequest) -> bool:
    return bool(
        request.tools
        and request.provider == TextProvider.OPENAI
        and request.model.startswith(("gpt-5.4-mini", "gpt-5.4-nano"))
    )


def responses_tool_schema(tool: AgentTool) -> JsonObject:
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters_schema,
    }


def chat_completions_tool_schema(tool: AgentTool) -> JsonObject:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters_schema,
        },
    }


def responses_function_call_item(tool_call: TextProviderToolCall) -> JsonObject:
    return {
        "type": "function_call",
        "call_id": tool_call.call_id,
        "name": tool_call.name,
        "arguments": orjson.dumps(tool_call.arguments_json).decode(),
    }


def responses_function_call_output_item(
    *,
    call_id: str,
    output: str,
) -> JsonObject:
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": output,
    }


def provider_function_call_items(
    provider: TextProvider,
    tool_calls: tuple[TextProviderToolCall, ...],
) -> tuple[JsonObject, ...]:
    protocol = text_provider_definition(provider).protocol
    if protocol == TextProviderProtocol.RESPONSES:
        return tuple(responses_function_call_item(call) for call in tool_calls)
    return (
        chat_completions_assistant_tool_call_item(
            tool_calls,
            content=None,
            reasoning_content=None,
        ),
    )


def chat_completions_assistant_tool_call_item(
    tool_calls: tuple[TextProviderToolCall, ...],
    *,
    content: str | None,
    reasoning_content: str | None = None,
) -> JsonObject:
    item: JsonObject = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": tool_call.call_id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": orjson.dumps(tool_call.arguments_json).decode(),
                },
            }
            for tool_call in tool_calls
        ],
    }
    if reasoning_content is not None:
        item["reasoning_content"] = reasoning_content
    return item


def provider_function_call_output_item(
    provider: TextProvider,
    *,
    call_id: str,
    output: str,
) -> JsonObject:
    protocol = text_provider_definition(provider).protocol
    if protocol == TextProviderProtocol.RESPONSES:
        return responses_function_call_output_item(
            call_id=call_id,
            output=output,
        )
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": output,
    }


def base_url(request: TextProviderRequest) -> str:
    definition = text_provider_definition(request.provider)
    if definition.allow_custom_base_url and request.config.base_url is not None:
        return request.config.base_url
    return definition.base_url


def parse_arguments(arguments_text: str) -> JsonObject:
    stripped = arguments_text.strip()
    if not stripped:
        return {}
    parsed = orjson.loads(stripped)
    if not isinstance(parsed, dict):
        return {}
    return json_object(parsed)


def response_error_message(chunk: dict[object, object]) -> str:
    error = chunk.get("error")
    if not isinstance(error, dict):
        response = chunk.get("response")
        if isinstance(response, dict):
            error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return f"Responses stream failed: {message}"
    message = chunk.get("message")
    if isinstance(message, str) and message:
        return f"Responses stream failed: {message}"
    return "Responses stream failed"


def chat_completions_error_message(chunk: dict[object, object]) -> str:
    error = chunk.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return f"Chat Completions stream failed: {message}"
    message = chunk.get("message")
    if isinstance(message, str) and message:
        return f"Chat Completions stream failed: {message}"
    return "Chat Completions stream failed"


def json_object(value: dict[object, object]) -> JsonObject:
    return {str(key): json_value(item) for key, item in value.items()}


def json_value(value: object) -> object:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return json_object(value)
    return str(value)

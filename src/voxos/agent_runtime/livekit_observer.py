import asyncio
import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import orjson

from voxos.agent_runtime.models import (
    AgentRuntimeEventKind,
    AgentStateEvent,
    AgentToolEvent,
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
    AgentUsageSnapshot,
    JsonObject,
)
from voxos.agent_runtime.storage import AgentRuntimeStore
from voxos.db.models import utc_now
from voxos.db.tenant import tenant_user_context

logger = logging.getLogger(__name__)


class LiveKitEventSource(Protocol):
    def on(
        self,
        event: str,
        callback: Callable[[object], None] | None = None,
    ) -> object:
        """Register a LiveKit event handler."""


class _UserInputTranscribedEvent(Protocol):
    transcript: str
    is_final: bool
    speaker_id: str | None
    language: object | None
    created_at: float


class _ConversationItemAddedEvent(Protocol):
    item: object
    created_at: float


class _ChatMessageLike(Protocol):
    id: str
    role: object
    text_content: str
    interrupted: bool
    transcript_confidence: float | None
    created_at: float


class _FunctionToolsExecutedEvent(Protocol):
    function_calls: Sequence[object]
    function_call_outputs: Sequence[object | None]
    created_at: float


class _FunctionCallLike(Protocol):
    call_id: str
    arguments: str
    name: str
    created_at: float
    group_id: str | None


class _FunctionCallOutputLike(Protocol):
    output: str
    is_error: bool


class _SessionUsageUpdatedEvent(Protocol):
    usage: object
    created_at: float


class _AgentSessionUsageLike(Protocol):
    model_usage: Sequence[object]


class _CloseEvent(Protocol):
    reason: object
    error: object | None
    created_at: float


class _ErrorEvent(Protocol):
    error: object
    source: object
    created_at: float


class _StateChangedEvent(Protocol):
    old_state: object
    new_state: object
    created_at: float


class _SpeechCreatedEvent(Protocol):
    user_initiated: bool
    source: object
    created_at: float


@dataclass(frozen=True, slots=True)
class LiveKitObserverContext:
    session_id: str
    user_id: str
    agent_id: str


class LiveKitSessionObserver:
    def __init__(
        self,
        *,
        context: LiveKitObserverContext,
        runtime_store: AgentRuntimeStore,
    ) -> None:
        self._context = context
        self._runtime_store = runtime_store
        self._tasks: set[asyncio.Task[object]] = set()

    def register(self, session: object) -> None:
        event_source = cast(LiveKitEventSource, session)
        event_source.on("user_input_transcribed", self._handle_user_input_transcribed)
        event_source.on("conversation_item_added", self._handle_conversation_item_added)
        event_source.on("function_tools_executed", self._handle_function_tools_executed)
        event_source.on("session_usage_updated", self._handle_session_usage_updated)
        event_source.on("close", self._handle_close)
        event_source.on("error", self._handle_error)
        event_source.on("agent_state_changed", self._handle_agent_state_changed)
        event_source.on("user_state_changed", self._handle_user_state_changed)
        event_source.on("speech_created", self._handle_speech_created)

    async def flush(self) -> None:
        while self._tasks:
            tasks = tuple(self._tasks)
            await asyncio.gather(*tasks, return_exceptions=True)

    def _handle_user_input_transcribed(self, event: object) -> None:
        transcript_event = cast(_UserInputTranscribedEvent, event)
        if not transcript_event.is_final:
            self._schedule(
                self._runtime_store.append_runtime_event(
                    AgentStateEvent(
                        session_id=self._context.session_id,
                        user_id=self._context.user_id,
                        agent_id=self._context.agent_id,
                        kind=AgentRuntimeEventKind.USER_TRANSCRIPT,
                        payload_json={
                            "is_final": False,
                            "speaker_id": transcript_event.speaker_id,
                            "language": _optional_text(transcript_event.language),
                        },
                        created_at=_event_datetime(transcript_event.created_at),
                    ),
                ),
            )
            return

        transcript = transcript_event.transcript.strip()
        if not transcript:
            return

        self._schedule(
            self._runtime_store.append_transcript_item(
                AgentTranscriptItem(
                    session_id=self._context.session_id,
                    user_id=self._context.user_id,
                    agent_id=self._context.agent_id,
                    role=AgentTranscriptRole.USER,
                    text=transcript,
                    source=AgentTranscriptSource.USER_INPUT_TRANSCRIBED,
                    language=_optional_text(transcript_event.language),
                    speaker_id=transcript_event.speaker_id,
                    is_final=True,
                    created_at=_event_datetime(transcript_event.created_at),
                ),
            ),
        )

    def _handle_conversation_item_added(self, event: object) -> None:
        item_event = cast(_ConversationItemAddedEvent, event)
        if not hasattr(item_event.item, "text_content"):
            return

        chat_item = cast(_ChatMessageLike, item_event.item)
        text = chat_item.text_content.strip()
        if not text:
            return

        self._schedule(
            self._runtime_store.append_transcript_item(
                AgentTranscriptItem(
                    session_id=self._context.session_id,
                    user_id=self._context.user_id,
                    agent_id=self._context.agent_id,
                    role=_transcript_role(chat_item.role),
                    text=text,
                    source=AgentTranscriptSource.CONVERSATION_ITEM,
                    interrupted=chat_item.interrupted,
                    transcript_confidence=chat_item.transcript_confidence,
                    is_final=True,
                    livekit_item_id=chat_item.id,
                    created_at=_event_datetime(chat_item.created_at),
                ),
            ),
        )

    def _handle_function_tools_executed(self, event: object) -> None:
        tools_event = cast(_FunctionToolsExecutedEvent, event)
        for index, raw_call in enumerate(tools_event.function_calls):
            call = cast(_FunctionCallLike, raw_call)
            raw_output = (
                tools_event.function_call_outputs[index] if index < len(tools_event.function_call_outputs) else None
            )
            output = cast(_FunctionCallOutputLike | None, raw_output)
            self._schedule(
                self._runtime_store.append_tool_event(
                    AgentToolEvent(
                        session_id=self._context.session_id,
                        user_id=self._context.user_id,
                        agent_id=self._context.agent_id,
                        call_id=call.call_id,
                        name=call.name,
                        arguments_json=_arguments_json(call.arguments),
                        output_text=output.output if output is not None else None,
                        is_error=output.is_error if output is not None else False,
                        group_id=call.group_id,
                        created_at=_event_datetime(call.created_at),
                    ),
                ),
            )

    def _handle_session_usage_updated(self, event: object) -> None:
        usage_event = cast(_SessionUsageUpdatedEvent, event)
        usage = cast(_AgentSessionUsageLike, usage_event.usage)
        created_at = _event_datetime(usage_event.created_at)
        for usage_item in usage.model_usage:
            payload = _payload_from_object(usage_item)
            input_tokens = _int_field(payload, "input_tokens")
            output_tokens = _int_field(payload, "output_tokens")
            self._schedule(
                self._runtime_store.append_usage_snapshot(
                    AgentUsageSnapshot(
                        session_id=self._context.session_id,
                        user_id=self._context.user_id,
                        agent_id=self._context.agent_id,
                        provider=_optional_text(payload.get("provider")),
                        model=_optional_text(payload.get("model")),
                        usage_type=_text_field(payload, "type", "unknown_usage"),
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=_total_tokens(input_tokens, output_tokens),
                        duration_seconds=_float_field(payload, "session_duration"),
                        audio_duration_seconds=_float_field(payload, "audio_duration"),
                        characters_count=_int_field(payload, "characters_count"),
                        total_requests=_int_field(payload, "total_requests"),
                        raw_usage_json=payload,
                        created_at=created_at,
                    ),
                ),
            )

    def _handle_close(self, event: object) -> None:
        close_event = cast(_CloseEvent, event)
        self._schedule(self._persist_close(close_event))

    def _handle_error(self, event: object) -> None:
        error_event = cast(_ErrorEvent, event)
        self._schedule(
            self._runtime_store.append_runtime_event(
                AgentStateEvent(
                    session_id=self._context.session_id,
                    user_id=self._context.user_id,
                    agent_id=self._context.agent_id,
                    kind=AgentRuntimeEventKind.ERROR,
                    payload_json={
                        "error_type": type(error_event.error).__name__,
                        "error_message": str(error_event.error),
                        "source": str(error_event.source),
                    },
                    created_at=_event_datetime(error_event.created_at),
                ),
            ),
        )

    def _handle_agent_state_changed(self, event: object) -> None:
        self._handle_state_changed(event, AgentRuntimeEventKind.AGENT_STATE)

    def _handle_user_state_changed(self, event: object) -> None:
        self._handle_state_changed(event, AgentRuntimeEventKind.USER_STATE)

    def _handle_speech_created(self, event: object) -> None:
        speech_event = cast(_SpeechCreatedEvent, event)
        self._schedule(
            self._runtime_store.append_runtime_event(
                AgentStateEvent(
                    session_id=self._context.session_id,
                    user_id=self._context.user_id,
                    agent_id=self._context.agent_id,
                    kind=AgentRuntimeEventKind.SPEECH_CREATED,
                    payload_json={
                        "user_initiated": speech_event.user_initiated,
                        "source": str(speech_event.source),
                    },
                    created_at=_event_datetime(speech_event.created_at),
                ),
            ),
        )

    def _handle_state_changed(
        self,
        event: object,
        kind: AgentRuntimeEventKind,
    ) -> None:
        state_event = cast(_StateChangedEvent, event)
        self._schedule(
            self._runtime_store.append_runtime_event(
                AgentStateEvent(
                    session_id=self._context.session_id,
                    user_id=self._context.user_id,
                    agent_id=self._context.agent_id,
                    kind=kind,
                    payload_json={
                        "old_state": str(state_event.old_state),
                        "new_state": str(state_event.new_state),
                    },
                    created_at=_event_datetime(state_event.created_at),
                ),
            ),
        )

    async def _persist_close(self, event: _CloseEvent) -> None:
        error = event.error
        await self._runtime_store.end_session(
            session_id=self._context.session_id,
            user_id=self._context.user_id,
            agent_id=self._context.agent_id,
            close_reason=_optional_text(event.reason),
            close_error_type=type(error).__name__ if error is not None else None,
            close_error_message=str(error) if error is not None else None,
        )
        await self._runtime_store.append_runtime_event(
            AgentStateEvent(
                session_id=self._context.session_id,
                user_id=self._context.user_id,
                agent_id=self._context.agent_id,
                kind=AgentRuntimeEventKind.CLOSE,
                payload_json={
                    "reason": _optional_text(event.reason),
                    "error_type": type(error).__name__ if error is not None else None,
                },
                created_at=_event_datetime(event.created_at),
            ),
        )

    def _schedule(self, awaitable: Coroutine[object, object, object]) -> None:
        task = asyncio.create_task(self._run_in_tenant_context(awaitable))
        self._tasks.add(task)
        task.add_done_callback(self._handle_task_done)

    async def _run_in_tenant_context(
        self,
        awaitable: Coroutine[object, object, object],
    ) -> object:
        with tenant_user_context(self._context.user_id):
            return await awaitable

    def _handle_task_done(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is not None:
            logger.error(
                "LiveKit observer persistence failed",
                exc_info=(type(exception), exception, exception.__traceback__),
            )


def _event_datetime(timestamp: float | None) -> datetime:
    if timestamp is None:
        return utc_now()
    return datetime.fromtimestamp(timestamp, UTC)


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _transcript_role(role: object) -> AgentTranscriptRole:
    role_text = str(role).strip()
    try:
        return AgentTranscriptRole(role_text)
    except ValueError:
        return AgentTranscriptRole.UNKNOWN


def _arguments_json(arguments: str) -> JsonObject:
    stripped_arguments = arguments.strip()
    if not stripped_arguments:
        return {}
    try:
        loaded: object = orjson.loads(stripped_arguments)
    except orjson.JSONDecodeError:
        return {"raw": stripped_arguments}
    if isinstance(loaded, Mapping):
        return {str(key): value for key, value in loaded.items()}
    return {"value": loaded}


def _payload_from_object(value: object) -> JsonObject:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_object(cast(Mapping[object, object], asdict(value)))
    if isinstance(value, Mapping):
        return _json_object(cast(Mapping[object, object], value))
    return {"value": str(value)}


def _json_object(payload: Mapping[object, object]) -> JsonObject:
    return {str(key): value for key, value in payload.items()}


def _text_field(payload: Mapping[str, object], key: str, default: str) -> str:
    value = _optional_text(payload.get(key))
    return value or default


def _int_field(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    return None


def _float_field(payload: Mapping[str, object], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, int | float):
        return float(value)
    return None


def _total_tokens(input_tokens: int | None, output_tokens: int | None) -> int | None:
    if input_tokens is None and output_tokens is None:
        return None
    return (input_tokens or 0) + (output_tokens or 0)

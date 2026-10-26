from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from sqlmodel import col, select

from voxos.agent_runtime.livekit_observer import (
    LiveKitObserverContext,
    LiveKitSessionObserver,
)
from voxos.agent_runtime.models import AgentStateEvent
from voxos.agent_runtime.records import (
    AgentReflectionJobRecord,
    AgentRuntimeEventRecord,
    AgentToolEventRecord,
    AgentTranscriptRecord,
    AgentUsageRecord,
)
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import current_tenant_user_id

EventCallback = Callable[[object], None]


@dataclass(slots=True)
class FakeEventSource:
    callbacks: dict[str, EventCallback]

    def on(self, event: str, callback: EventCallback | None = None) -> object:
        assert callback is not None
        self.callbacks[event] = callback
        return callback


@dataclass(frozen=True, slots=True)
class FakeChatMessage:
    id: str
    role: str
    text_content: str
    interrupted: bool
    transcript_confidence: float | None
    created_at: float


@dataclass(frozen=True, slots=True)
class FakeConversationItemEvent:
    item: FakeChatMessage
    created_at: float


@dataclass(frozen=True, slots=True)
class FakeTranscribedEvent:
    transcript: str
    is_final: bool
    speaker_id: str | None
    language: str | None
    created_at: float


@dataclass(frozen=True, slots=True)
class FakeFunctionCall:
    call_id: str
    arguments: str
    name: str
    created_at: float
    group_id: str | None


@dataclass(frozen=True, slots=True)
class FakeFunctionOutput:
    output: str
    is_error: bool


@dataclass(frozen=True, slots=True)
class FakeToolsExecutedEvent:
    function_calls: tuple[FakeFunctionCall, ...]
    function_call_outputs: tuple[FakeFunctionOutput | None, ...]
    created_at: float


@dataclass(frozen=True, slots=True)
class FakeUsageItem:
    type: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    session_duration: float


@dataclass(frozen=True, slots=True)
class FakeUsage:
    model_usage: tuple[FakeUsageItem, ...]


@dataclass(frozen=True, slots=True)
class FakeUsageEvent:
    usage: FakeUsage
    created_at: float


@dataclass(frozen=True, slots=True)
class FakeCloseEvent:
    reason: str
    error: object | None
    created_at: float


@dataclass(slots=True)
class TenantCheckingRuntimeStore:
    end_session_tenant_user_id: str | None = None
    runtime_event_tenant_user_ids: list[str] = field(default_factory=list)

    async def end_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        close_reason: str | None,
        close_error_type: str | None,
        close_error_message: str | None,
    ) -> object | None:
        self.end_session_tenant_user_id = current_tenant_user_id()
        return None

    async def append_runtime_event(self, event: AgentStateEvent) -> object:
        self.runtime_event_tenant_user_ids.append(current_tenant_user_id())
        return object()


@pytest.mark.asyncio
async def test_livekit_observer_persists_session_events_without_inline_reflection(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    session = await store.create_session(
        user_id="user-123",
        agent_id="agent-123",
        provider="openai_realtime",
        model="gpt-realtime",
        voice="marin",
        room_name="room-1",
        room_metadata={
            "user_id": "user-123",
            "agent_id": "agent-123",
            "runtime_session_id": "session-123",
        },
        prompt_version="test",
        prompt_snapshot="Base instructions.",
        prompt_hash="hash",
    )
    event_source = FakeEventSource(callbacks={})
    observer = LiveKitSessionObserver(
        context=LiveKitObserverContext(
            session_id=session.id,
            user_id="user-123",
            agent_id="agent-123",
        ),
        runtime_store=store,
    )
    observer.register(event_source)

    event_source.callbacks["conversation_item_added"](
        FakeConversationItemEvent(
            item=FakeChatMessage(
                id="item-1",
                role="assistant",
                text_content="Hello.",
                interrupted=False,
                transcript_confidence=None,
                created_at=1.0,
            ),
            created_at=1.0,
        ),
    )
    event_source.callbacks["user_input_transcribed"](
        FakeTranscribedEvent(
            transcript="Book a planning session",
            is_final=True,
            speaker_id="speaker-1",
            language="en",
            created_at=2.0,
        ),
    )
    event_source.callbacks["function_tools_executed"](
        FakeToolsExecutedEvent(
            function_calls=(
                FakeFunctionCall(
                    call_id="call-1",
                    arguments='{"memory_id":"mem-1"}',
                    name="confirm_agent_memory",
                    created_at=3.0,
                    group_id=None,
                ),
            ),
            function_call_outputs=(FakeFunctionOutput(output="ok", is_error=False),),
            created_at=3.0,
        ),
    )
    event_source.callbacks["session_usage_updated"](
        FakeUsageEvent(
            usage=FakeUsage(
                model_usage=(
                    FakeUsageItem(
                        type="llm_usage",
                        provider="openai",
                        model="gpt-realtime",
                        input_tokens=10,
                        output_tokens=5,
                        session_duration=1.5,
                    ),
                ),
            ),
            created_at=4.0,
        ),
    )
    event_source.callbacks["close"](
        FakeCloseEvent(reason="task_completed", error=None, created_at=5.0),
    )

    await observer.flush()

    async with sqlite_session_factory() as db_session:
        transcript_result = await db_session.exec(select(AgentTranscriptRecord))
        transcripts = transcript_result.all()
        tool_result = await db_session.exec(select(AgentToolEventRecord))
        tool_events = tool_result.all()
        usage_result = await db_session.exec(select(AgentUsageRecord))
        usage_events = usage_result.all()
        runtime_result = await db_session.exec(
            select(AgentRuntimeEventRecord).where(
                col(AgentRuntimeEventRecord.kind) == "close",
            ),
        )
        close_events = runtime_result.all()
        reflection_result = await db_session.exec(select(AgentReflectionJobRecord))
        reflection_jobs = reflection_result.all()

    assert not reflection_jobs
    assert tuple(record.text for record in transcripts) == (
        "Hello.",
        "Book a planning session",
    )
    assert tool_events[0].name == "confirm_agent_memory"
    assert tool_events[0].agent_id == "agent-123"
    assert tool_events[0].arguments_json == {"memory_id": "mem-1"}
    assert usage_events[0].total_tokens == 15
    assert close_events[0].payload_json["reason"] == "task_completed"


@pytest.mark.asyncio
async def test_livekit_observer_persistence_tasks_restore_tenant_context() -> None:
    store = TenantCheckingRuntimeStore()
    observer = LiveKitSessionObserver(
        context=LiveKitObserverContext(
            session_id="session-123",
            user_id="user-123",
            agent_id="agent-123",
        ),
        runtime_store=store,
    )
    event_source = FakeEventSource(callbacks={})
    observer.register(event_source)

    event_source.callbacks["close"](
        FakeCloseEvent(reason="participant_disconnected", error=None, created_at=5.0),
    )

    await observer.flush()

    assert store.end_session_tenant_user_id == "user-123"
    assert store.runtime_event_tenant_user_ids == ["user-123"]

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import cast

import pytest
from pydantic import ValidationError

from voxos.agent_core.models import AgentEvent, AgentEventKind
from voxos.agent_runtime.models import AgentTranscriptSource, JsonObject
from voxos.agent_runtime.records import AgentRunEventRecord, AgentRunRecord, AgentSessionRecord
from voxos.agent_runtime.run_execution import (
    AgentRunExecutionResult,
    AgentTurnRunPayload,
    TextAgentRunExecutor,
)
from voxos.agent_runtime.run_models import AgentRunEventKind, AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.subagents import SubAgentSpawnRequest
from voxos.db.models import utc_now
from voxos.location.models import ClientLocation
from voxos.text.models import TextProvider
from voxos.text.run_chat_service import RunChatDeliveryResult, RunChatService
from voxos.text.service import TextChatService


@dataclass(frozen=True, slots=True)
class StreamCall:
    chat_id: str
    text: str
    user_message_source: AgentTranscriptSource
    assistant_message_source: AgentTranscriptSource
    current_location: ClientLocation | None = None


@dataclass(slots=True)
class RecordingTextService:
    created_chats: list[AgentSessionRecord] = field(default_factory=list)
    stream_calls: list[StreamCall] = field(default_factory=list)

    async def create_chat(
        self,
        *,
        user_id: str,
        agent_id: str | None,
        title: str | None,
        provider: TextProvider | None,
        model: str | None,
    ) -> AgentSessionRecord:
        chat = AgentSessionRecord(
            id="child-chat",
            user_id=user_id,
            agent_id=agent_id,
            provider=(provider or TextProvider.OPENAI).value,
            model=model or "gpt-5.4-nano",
            channel="text",
            title=title,
        )
        self.created_chats.append(chat)
        return chat

    async def stream_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
        current_location: ClientLocation | None,
        run_id: str,
        manage_run: bool,
        user_message_source: AgentTranscriptSource,
        assistant_message_source: AgentTranscriptSource,
    ) -> AsyncIterator[AgentEvent]:
        del manage_run
        self.stream_calls.append(
            StreamCall(
                chat_id=chat_id,
                text=text,
                user_message_source=user_message_source,
                assistant_message_source=assistant_message_source,
                current_location=current_location,
            ),
        )
        yield AgentEvent(
            id="event-1",
            kind=AgentEventKind.MESSAGE_COMPLETED,
            session_id=chat_id,
            run_id=run_id,
            user_id=user_id,
            agent_id="agent-1",
            payload={"text": "Delegated result"},
            created_at=utc_now(),
        )


@dataclass(slots=True)
class RecordingRunStore:
    bound_chats: list[tuple[str, str]] = field(default_factory=list)
    events: list[tuple[str, AgentRunEventKind, JsonObject]] = field(default_factory=list)

    async def bind_run_to_chat(
        self,
        *,
        run_id: str,
        user_id: str,
        chat_id: str,
    ) -> AgentRunRecord | None:
        self.bound_chats.append((run_id, chat_id))
        return AgentRunRecord(
            id=run_id,
            user_id=user_id,
            agent_id="agent-1",
            kind=AgentRunKind.SUBAGENT.value,
            payload_json={"task_prompt": "Investigate"},
            created_by_user_id=user_id,
            session_id=chat_id,
            chat_id=chat_id,
        )

    async def append_event(
        self,
        *,
        run_id: str,
        user_id: str,
        kind: AgentRunEventKind,
        payload_json: JsonObject,
    ) -> AgentRunEventRecord | None:
        del user_id
        self.events.append((run_id, kind, payload_json))
        return None


@dataclass(slots=True)
class RecordingRunChatService:
    text_service: RecordingTextService
    run_store: RecordingRunStore
    parent_deliveries: list[tuple[str, str]] = field(default_factory=list)

    async def ensure_run_chat(
        self,
        *,
        run: AgentRunRecord,
        title: str,
        provider: TextProvider | None,
        model: str | None,
    ) -> AgentSessionRecord:
        if run.chat_id is not None:
            return AgentSessionRecord(
                id=run.chat_id,
                user_id=run.user_id,
                agent_id=run.agent_id,
                provider=(provider or TextProvider.OPENAI).value,
                model=model or "gpt-5.4-nano",
                channel="text",
                title=title,
            )
        chat = await self.text_service.create_chat(
            user_id=run.user_id,
            agent_id=run.agent_id,
            title=title,
            provider=provider,
            model=model,
        )
        await self.run_store.bind_run_to_chat(
            run_id=run.id,
            user_id=run.user_id,
            chat_id=chat.id,
        )
        return chat

    async def deliver_to_parent(
        self,
        *,
        run: AgentRunRecord,
        text: str,
        stage: str = "complete",
    ) -> RunChatDeliveryResult:
        del stage
        self.parent_deliveries.append((run.id, text))
        return RunChatDeliveryResult(delivered=True, transcript_id="parent-message", created=True)


@dataclass(frozen=True, slots=True)
class UnexpectedFallbackExecutor:
    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        del run
        message = "Subagent runs must not use the fallback executor"
        raise AssertionError(message)


def test_subagent_summary_is_included_in_the_child_prompt() -> None:
    payload = AgentTurnRunPayload.model_validate(
        {
            "task_prompt": "Compare the two options.",
            "context_mode": "summary",
            "parent_summary": "The user prioritizes reliability.",
        },
    )

    assert payload.prompt_text == (
        "Parent context:\nThe user prioritizes reliability.\n\nDelegated task:\nCompare the two options."
    )


def test_subagent_spawn_request_requires_consistent_context_fields() -> None:
    with pytest.raises(ValidationError, match="requires a parent summary"):
        SubAgentSpawnRequest(
            parent_run_id="run-123",
            target_agent_id="agent-123",
            task_prompt="Compare the options.",
            context_mode="summary",
        )


@pytest.mark.asyncio
async def test_subagent_execution_uses_a_dedicated_attributed_chat() -> None:
    text_service = RecordingTextService()
    run_store = RecordingRunStore()
    run_chat_service = RecordingRunChatService(text_service, run_store)
    executor = TextAgentRunExecutor(
        text_service=cast(TextChatService, text_service),
        run_store=cast(SqlModelAgentRunStore, run_store),
        run_chat_service=cast(RunChatService, run_chat_service),
        fallback_executor=UnexpectedFallbackExecutor(),
    )
    run = AgentRunRecord(
        id="child-run",
        user_id="user-1",
        agent_id="agent-1",
        kind=AgentRunKind.SUBAGENT.value,
        payload_json={
            "task_prompt": "Investigate the inbox.",
            "context_mode": "isolated",
            "provider": "openai",
            "model": "gpt-5.4-nano",
        },
        created_by_user_id="user-1",
        parent_run_id="parent-run",
        parent_session_id="parent-chat",
        root_run_id="parent-run",
    )

    result = await executor.execute(run)

    assert result.finalized is True
    assert [chat.id for chat in text_service.created_chats] == ["child-chat"]
    assert run_store.bound_chats == [("child-run", "child-chat")]
    assert run_chat_service.parent_deliveries == [("child-run", "Delegated result")]
    assert text_service.stream_calls == [
        StreamCall(
            chat_id="child-chat",
            text="Investigate the inbox.",
            user_message_source=AgentTranscriptSource.SUBAGENT_TASK,
            assistant_message_source=AgentTranscriptSource.SUBAGENT_OUTPUT,
        ),
    ]
    assert run_store.events == [
        (
            "parent-run",
            AgentRunEventKind.SUBAGENT_COMPLETED,
            {
                "child_run_id": "child-run",
                "child_chat_id": "child-chat",
                "characters_count": 16,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_subagent_execution_reuses_its_dedicated_chat_on_retry() -> None:
    text_service = RecordingTextService()
    run_store = RecordingRunStore()
    run_chat_service = RecordingRunChatService(text_service, run_store)
    executor = TextAgentRunExecutor(
        text_service=cast(TextChatService, text_service),
        run_store=cast(SqlModelAgentRunStore, run_store),
        run_chat_service=cast(RunChatService, run_chat_service),
        fallback_executor=UnexpectedFallbackExecutor(),
    )
    run = AgentRunRecord(
        id="child-run",
        user_id="user-1",
        agent_id="agent-1",
        kind=AgentRunKind.SUBAGENT.value,
        payload_json={
            "task_prompt": "Investigate the inbox.",
            "context_mode": "isolated",
        },
        created_by_user_id="user-1",
        session_id="child-chat",
        chat_id="child-chat",
        parent_run_id="parent-run",
        parent_session_id="parent-chat",
        root_run_id="parent-run",
    )

    result = await executor.execute(run)

    assert result.finalized is True
    assert text_service.created_chats == []
    assert run_store.bound_chats == []
    assert run_chat_service.parent_deliveries == [("child-run", "Delegated result")]
    assert [call.chat_id for call in text_service.stream_calls] == ["child-chat"]


@pytest.mark.asyncio
async def test_webhook_execution_creates_an_isolated_task_chat() -> None:
    text_service = RecordingTextService()
    run_store = RecordingRunStore()
    run_chat_service = RecordingRunChatService(text_service, run_store)
    executor = TextAgentRunExecutor(
        text_service=cast(TextChatService, text_service),
        run_store=cast(SqlModelAgentRunStore, run_store),
        run_chat_service=cast(RunChatService, run_chat_service),
        fallback_executor=UnexpectedFallbackExecutor(),
    )
    run = AgentRunRecord(
        id="webhook-run",
        user_id="user-1",
        agent_id="agent-1",
        kind=AgentRunKind.WEBHOOK_AGENT_TASK.value,
        payload_json={
            "text": "Handle this event.\n\n<untrusted_webhook_json>{}</untrusted_webhook_json>",
            "title": "Webhook: CRM lead",
        },
        created_by_user_id="user-1",
        webhook_id="webhook-1",
    )

    result = await executor.execute(run)

    assert result.finalized is True
    assert [(chat.id, chat.title) for chat in text_service.created_chats] == [
        ("child-chat", "Webhook: CRM lead"),
    ]
    assert run_store.bound_chats == [("webhook-run", "child-chat")]
    assert run_chat_service.parent_deliveries == []
    assert text_service.stream_calls == [
        StreamCall(
            chat_id="child-chat",
            text="Handle this event.\n\n<untrusted_webhook_json>{}</untrusted_webhook_json>",
            user_message_source=AgentTranscriptSource.TEXT_INPUT,
            assistant_message_source=AgentTranscriptSource.TEXT_OUTPUT,
        ),
    ]

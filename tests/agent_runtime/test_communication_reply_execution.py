from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import cast

import pytest

from voxos.agent_core.models import AgentEvent, AgentEventKind
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_execution import (
    AgentRunExecutionResult,
    CommunicationReplyRunPayload,
    TextAgentRunExecutor,
)
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.db.models import utc_now
from voxos.integrations.models import IntegrationReplyStatus
from voxos.integrations.reply_dispatch import (
    CommunicationReplyDispatcher,
    CommunicationReplyDispatchResult,
)
from voxos.text.run_chat_service import RunChatService
from voxos.text.service import TextChatService
from voxos.whatsapp.models import WhatsAppOutboxStatus


@dataclass(slots=True)
class FakeCommunicationTextService:
    media: list[CommunicationMedium] = field(default_factory=list)

    async def stream_message(
        self,
        *,
        user_id: str,
        chat_id: str,
        text: str,
        run_id: str,
        manage_run: bool,
        communication_medium: CommunicationMedium,
        append_user_message: bool,
        persist_assistant_message: bool,
    ) -> AsyncIterator[AgentEvent]:
        del text, manage_run, append_user_message, persist_assistant_message
        self.media.append(communication_medium)
        yield AgentEvent(
            id="event-1",
            kind=AgentEventKind.MESSAGE_COMPLETED,
            session_id=chat_id,
            run_id=run_id,
            user_id=user_id,
            agent_id="agent-1",
            payload={"text": "WhatsApp response"},
            created_at=utc_now(),
        )


@dataclass(slots=True)
class FakeCommunicationReplyDispatcher:
    accepted_media: list[CommunicationMedium] = field(default_factory=list)
    dispatched_bodies: list[str] = field(default_factory=list)
    inbound_transcript_calls: int = 0
    outbound_transcript_calls: int = 0

    async def accepted_result(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        communication_medium: CommunicationMedium,
    ) -> CommunicationReplyDispatchResult | None:
        del user_id, run_id, inbound_message_id, conversation_id
        self.accepted_media.append(communication_medium)
        return None

    async def ensure_inbound_transcript(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
    ) -> None:
        del user_id, run_id, inbound_message_id, conversation_id
        self.inbound_transcript_calls += 1

    async def dispatch_whatsapp(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        generated_body: str,
    ) -> CommunicationReplyDispatchResult:
        del user_id, run_id, inbound_message_id, conversation_id
        self.dispatched_bodies.append(generated_body)
        return CommunicationReplyDispatchResult(
            communication_medium=CommunicationMedium.WHATSAPP,
            reply_id="reply-1",
            whatsapp_outbox_id="outbox-1",
            whatsapp_outbox_status=WhatsAppOutboxStatus.QUEUED,
            status=IntegrationReplyStatus.GENERATED,
            body=generated_body,
        )

    async def dispatch_sms(self, **_: object) -> CommunicationReplyDispatchResult:
        message = "WhatsApp execution must not dispatch SMS"
        raise AssertionError(message)

    async def ensure_outbound_transcript(
        self,
        *,
        user_id: str,
        run_id: str,
        inbound_message_id: str,
        conversation_id: str,
        body: str,
    ) -> None:
        del user_id, run_id, inbound_message_id, conversation_id, body
        self.outbound_transcript_calls += 1


@dataclass(slots=True)
class FakeCommunicationRunStore:
    results: list[JsonObject] = field(default_factory=list)

    async def complete_run(
        self,
        *,
        run_id: str,
        user_id: str,
        result_json: JsonObject,
    ) -> AgentRunRecord | None:
        del run_id, user_id
        self.results.append(result_json)
        return None


@dataclass(slots=True)
class UnexpectedFallbackExecutor:
    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        del run
        message = "Communication replies must not use the fallback executor"
        raise AssertionError(message)


def test_legacy_communication_reply_payload_defaults_to_sms() -> None:
    payload = CommunicationReplyRunPayload.model_validate(
        {
            "task_prompt": "Reply",
            "chat_id": "session-1",
            "conversation_id": "conversation-1",
            "inbound_message_id": "inbound-1",
        },
    )

    assert payload.communication_medium == CommunicationMedium.SMS


@pytest.mark.asyncio
async def test_whatsapp_communication_reply_run_uses_whatsapp_dispatch() -> None:
    text_service = FakeCommunicationTextService()
    dispatcher = FakeCommunicationReplyDispatcher()
    run_store = FakeCommunicationRunStore()
    executor = TextAgentRunExecutor(
        text_service=cast(TextChatService, text_service),
        run_store=cast(SqlModelAgentRunStore, run_store),
        run_chat_service=cast(RunChatService, object()),
        fallback_executor=UnexpectedFallbackExecutor(),
        reply_dispatcher=cast(CommunicationReplyDispatcher, dispatcher),
    )
    run = AgentRunRecord(
        id="run-1",
        user_id="user-1",
        agent_id="agent-1",
        kind=AgentRunKind.COMMUNICATION_REPLY.value,
        payload_json={
            "task_prompt": "Reply on WhatsApp",
            "chat_id": "session-1",
            "conversation_id": "conversation-1",
            "inbound_message_id": "inbound-1",
            "communication_medium": "whatsapp",
        },
        created_by_user_id="user-1",
    )

    result = await executor.execute(run)

    assert result.finalized is True
    assert text_service.media == [CommunicationMedium.WHATSAPP]
    assert dispatcher.accepted_media == [CommunicationMedium.WHATSAPP]
    assert dispatcher.dispatched_bodies == ["WhatsApp response"]
    assert dispatcher.inbound_transcript_calls == 1
    assert dispatcher.outbound_transcript_calls == 1
    assert run_store.results == [
        {
            "characters_count": 17,
            "response_profile": "whatsapp:v1",
            "reply_status": "generated",
            "whatsapp_outbox_id": "outbox-1",
            "whatsapp_outbox_status": "queued",
        },
    ]

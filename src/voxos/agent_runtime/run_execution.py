import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from voxos.agent_core.models import AgentEventKind
from voxos.agent_core.response_profiles import CommunicationMedium, response_profile
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.models import AgentTranscriptSource, JsonObject
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import (
    AgentRunEventKind,
    AgentRunFailureCategory,
    AgentRunKind,
    AgentRunStatus,
)
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.subagents import SubAgentContextMode
from voxos.integrations.reply_dispatch import (
    CommunicationReplyDispatcher,
    CommunicationReplyDispatchResult,
)
from voxos.location.models import ClientLocation
from voxos.text.models import TextProvider
from voxos.text.run_chat_service import RunChatService
from voxos.text.service import TextChatService

logger = logging.getLogger(__name__)

TEXT_AGENT_WORKER_RUN_KINDS = (
    AgentRunKind.REFLECTION,
    AgentRunKind.COMMUNICATION_REPLY,
    AgentRunKind.SCHEDULED_AGENT_TURN,
    AgentRunKind.SUBAGENT,
    AgentRunKind.MANUAL_AGENT_TASK,
    AgentRunKind.WEBHOOK_AGENT_TASK,
)


class AgentRunExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    finalized: bool = False
    result_json: JsonObject = Field(default_factory=dict)


class AgentRunExecutor(Protocol):
    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        """Execute a claimed run."""


class AgentTurnRunPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    text: str | None = Field(default=None, min_length=1, max_length=24000)
    task_prompt: str | None = Field(default=None, min_length=1, max_length=24000)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    provider: TextProvider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=256)
    context_mode: SubAgentContextMode = SubAgentContextMode.ISOLATED
    parent_summary: str | None = Field(default=None, min_length=1, max_length=12000)
    scheduled_context: JsonObject = Field(default_factory=dict)
    current_location: ClientLocation | None = None

    @model_validator(mode="after")
    def validate_text(self) -> "AgentTurnRunPayload":
        if self.text is None and self.task_prompt is None:
            message = "Run payload requires text or task_prompt"
            raise ValueError(message)
        return self

    @property
    def prompt_text(self) -> str:
        prompt = self.text or self.task_prompt or ""
        if (
            self.task_prompt is not None
            and self.context_mode == SubAgentContextMode.SUMMARY
            and self.parent_summary is not None
        ):
            return f"Parent context:\n{self.parent_summary}\n\nDelegated task:\n{prompt}"
        return prompt


class CommunicationReplyRunPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    task_prompt: str = Field(min_length=1, max_length=24000)
    chat_id: str = Field(min_length=1, max_length=36)
    conversation_id: str = Field(min_length=1, max_length=36)
    inbound_message_id: str = Field(min_length=1, max_length=36)
    communication_medium: CommunicationMedium = CommunicationMedium.SMS
    contact_id: str | None = Field(default=None, min_length=1, max_length=36)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    sender_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    is_group: bool = False


@dataclass(frozen=True, slots=True)
class RecordedAgentRunExecutor:
    run_store: SqlModelAgentRunStore
    identity_service: AgentIdentityService

    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        await self.identity_service.require_agent(
            owner_user_id=run.user_id,
            agent_id=run.agent_id,
        )
        if run.kind not in {
            AgentRunKind.MANUAL_AGENT_TASK.value,
            AgentRunKind.SUBAGENT.value,
            AgentRunKind.SCHEDULED_AGENT_TURN.value,
            AgentRunKind.WEBHOOK_AGENT_TASK.value,
        }:
            message = f"Unsupported run kind: {run.kind}"
            raise ValueError(message)
        try:
            AgentTurnRunPayload.model_validate(run.payload_json)
        except ValidationError:
            if run.kind == AgentRunKind.SCHEDULED_AGENT_TURN.value:
                raise
        await self.run_store.append_event(
            run_id=run.id,
            user_id=run.user_id,
            kind=AgentRunEventKind.PROGRESS,
            payload_json={
                "message": "Run claimed and ownership verified.",
                "kind": run.kind,
            },
        )
        return AgentRunExecutionResult(
            finalized=False,
            result_json={
                "status": "recorded",
                "kind": run.kind,
            },
        )


@dataclass(frozen=True, slots=True)
class TextAgentRunExecutor:
    text_service: TextChatService
    run_store: SqlModelAgentRunStore
    run_chat_service: RunChatService
    fallback_executor: AgentRunExecutor
    reply_dispatcher: CommunicationReplyDispatcher | None = None

    reflection_executor: AgentRunExecutor | None = None

    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        if run.kind == AgentRunKind.REFLECTION.value:
            if self.reflection_executor is None:
                message = "Reflection executor is unavailable"
                raise RuntimeError(message)
            return await self.reflection_executor.execute(run)
        if run.kind == AgentRunKind.COMMUNICATION_REPLY.value:
            return await self._execute_communication_reply(run)
        if run.kind not in {
            AgentRunKind.SCHEDULED_AGENT_TURN.value,
            AgentRunKind.SUBAGENT.value,
            AgentRunKind.MANUAL_AGENT_TASK.value,
            AgentRunKind.WEBHOOK_AGENT_TASK.value,
        }:
            return await self.fallback_executor.execute(run)
        payload = AgentTurnRunPayload.model_validate(run.payload_json)
        is_subagent = run.kind == AgentRunKind.SUBAGENT.value
        chat = await self.run_chat_service.ensure_run_chat(
            run=run,
            title=payload.title or scheduled_chat_title(run.kind),
            provider=payload.provider,
            model=payload.model,
        )
        chat_id = chat.id
        assistant_text = ""
        try:
            async for event in self.text_service.stream_message(
                user_id=run.user_id,
                chat_id=chat_id,
                text=payload.prompt_text,
                current_location=payload.current_location,
                run_id=run.id,
                manage_run=True,
                user_message_source=(
                    AgentTranscriptSource.SUBAGENT_TASK if is_subagent else AgentTranscriptSource.TEXT_INPUT
                ),
                assistant_message_source=(
                    AgentTranscriptSource.SUBAGENT_OUTPUT if is_subagent else AgentTranscriptSource.TEXT_OUTPUT
                ),
            ):
                if event.kind != AgentEventKind.MESSAGE_COMPLETED:
                    continue
                event_text = event.payload.get("text")
                if isinstance(event_text, str):
                    assistant_text = event_text
        except Exception:
            updated = await self.run_store.get_run(user_id=run.user_id, run_id=run.id)
            if updated is not None and AgentRunStatus(updated.status) in {
                AgentRunStatus.SUCCEEDED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
                AgentRunStatus.LOST,
            }:
                return AgentRunExecutionResult(finalized=True)
            raise
        if is_subagent and run.parent_run_id is not None:
            try:
                delivery = await self.run_chat_service.deliver_to_parent(
                    run=run,
                    text=assistant_text,
                )
                if not delivery.delivered:
                    logger.warning(
                        "Subagent result could not be delivered to its parent chat",
                        extra={
                            "run_id": run.id,
                            "parent_run_id": run.parent_run_id,
                            "reason": delivery.unavailable_reason,
                        },
                    )
            except Exception:
                logger.exception(
                    "Subagent result delivery failed",
                    extra={
                        "run_id": run.id,
                        "parent_run_id": run.parent_run_id,
                    },
                )
            await self.run_store.append_event(
                run_id=run.parent_run_id,
                user_id=run.user_id,
                kind=AgentRunEventKind.SUBAGENT_COMPLETED,
                payload_json={
                    "child_run_id": run.id,
                    "child_chat_id": chat_id,
                    "characters_count": len(assistant_text),
                },
            )
        return AgentRunExecutionResult(finalized=True)

    async def _execute_communication_reply(
        self,
        run: AgentRunRecord,
    ) -> AgentRunExecutionResult:
        if self.reply_dispatcher is None:
            message = "Communication reply dispatcher is not configured"
            raise RuntimeError(message)
        payload = CommunicationReplyRunPayload.model_validate(run.payload_json)
        existing = await self.reply_dispatcher.accepted_result(
            user_id=run.user_id,
            run_id=run.id,
            inbound_message_id=payload.inbound_message_id,
            conversation_id=payload.conversation_id,
            communication_medium=payload.communication_medium,
        )
        await self.reply_dispatcher.ensure_inbound_transcript(
            user_id=run.user_id,
            run_id=run.id,
            inbound_message_id=payload.inbound_message_id,
            conversation_id=payload.conversation_id,
        )
        result = existing
        if result is None:
            generated_parts: list[str] = []
            async for event in self.text_service.stream_message(
                user_id=run.user_id,
                chat_id=payload.chat_id,
                text=payload.task_prompt,
                run_id=run.id,
                manage_run=False,
                communication_medium=payload.communication_medium,
                append_user_message=False,
                persist_assistant_message=False,
            ):
                if event.kind != AgentEventKind.MESSAGE_COMPLETED:
                    continue
                event_text = event.payload.get("text")
                if isinstance(event_text, str):
                    generated_parts.append(event_text)
            generated_body = "".join(generated_parts).strip()
            if payload.communication_medium == CommunicationMedium.SMS:
                result = await self.reply_dispatcher.dispatch_sms(
                    user_id=run.user_id,
                    run_id=run.id,
                    inbound_message_id=payload.inbound_message_id,
                    conversation_id=payload.conversation_id,
                    generated_body=generated_body,
                    repairer=lambda body: self.text_service.condense_sms_response(
                        user_id=run.user_id,
                        chat_id=payload.chat_id,
                        text=body,
                    ),
                )
            elif payload.communication_medium == CommunicationMedium.WHATSAPP:
                result = await self.reply_dispatcher.dispatch_whatsapp(
                    user_id=run.user_id,
                    run_id=run.id,
                    inbound_message_id=payload.inbound_message_id,
                    conversation_id=payload.conversation_id,
                    generated_body=generated_body,
                )
            else:
                message = f"Unsupported communication reply medium: {payload.communication_medium.value}"
                raise ValueError(message)
        await self._finalize_communication_reply(
            run=run,
            payload=payload,
            result=result,
        )
        return AgentRunExecutionResult(finalized=True)

    async def _finalize_communication_reply(
        self,
        *,
        run: AgentRunRecord,
        payload: CommunicationReplyRunPayload,
        result: CommunicationReplyDispatchResult,
    ) -> None:
        if self.reply_dispatcher is None:
            message = "Communication reply dispatcher is not configured"
            raise RuntimeError(message)
        await self.reply_dispatcher.ensure_outbound_transcript(
            user_id=run.user_id,
            run_id=run.id,
            inbound_message_id=payload.inbound_message_id,
            conversation_id=payload.conversation_id,
            body=result.body,
        )
        if result.communication_medium != payload.communication_medium:
            message = "Communication reply result medium does not match its durable run."
            raise ValueError(message)
        result_json: JsonObject = {
            "characters_count": len(result.body),
            "response_profile": response_profile(
                payload.communication_medium,
            ).identifier,
            "reply_status": result.status.value,
        }
        if payload.communication_medium == CommunicationMedium.SMS:
            if result.encoding is None or result.estimated_segments is None or result.outbound_message_id is None:
                message = "SMS communication reply result is incomplete."
                raise ValueError(message)
            result_json.update(
                {
                    "sms_encoding": result.encoding.value,
                    "sms_segments": result.estimated_segments,
                    "outbound_message_id": result.outbound_message_id,
                },
            )
        elif payload.communication_medium == CommunicationMedium.WHATSAPP:
            if result.whatsapp_outbox_id is None or result.whatsapp_outbox_status is None:
                message = "WhatsApp communication reply result is incomplete."
                raise ValueError(message)
            result_json.update(
                {
                    "whatsapp_outbox_id": result.whatsapp_outbox_id,
                    "whatsapp_outbox_status": result.whatsapp_outbox_status.value,
                },
            )
        else:
            message = f"Unsupported communication reply medium: {payload.communication_medium.value}"
            raise ValueError(message)
        await self.run_store.complete_run(
            run_id=run.id,
            user_id=run.user_id,
            result_json=result_json,
        )


def failure_category_for_exception(exc: Exception) -> AgentRunFailureCategory:
    if isinstance(exc, (ValidationError, ValueError)):
        return AgentRunFailureCategory.VALIDATION
    return AgentRunFailureCategory.UNKNOWN


def scheduled_chat_title(run_kind: str) -> str:
    if run_kind == AgentRunKind.SUBAGENT.value:
        return "Subagent run"
    if run_kind == AgentRunKind.SCHEDULED_AGENT_TURN.value:
        return "Scheduled task"
    if run_kind == AgentRunKind.WEBHOOK_AGENT_TASK.value:
        return "Webhook task"
    return "Agent task"

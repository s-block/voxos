from datetime import datetime

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.models import (
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
)
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.notifications.models import PushNotificationOutboxRecord

NOTIFIABLE_RUN_KINDS = frozenset(
    {
        AgentRunKind.SCHEDULED_AGENT_TURN,
        AgentRunKind.CODEX_CLOUD_TASK,
    },
)


async def enqueue_terminal_run_notification(
    *,
    session: AsyncSession,
    run: AgentRunRecord,
    status: AgentRunStatus,
    created_at: datetime,
    max_attempts: int,
) -> None:
    run_kind = AgentRunKind(run.kind)
    if run_kind not in NOTIFIABLE_RUN_KINDS:
        return
    if status not in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}:
        return

    event_key = f"run:{run.id}:{status.value}"
    existing = await session.exec(
        select(PushNotificationOutboxRecord.id).where(
            col(PushNotificationOutboxRecord.event_key) == event_key,
        ),
    )
    if existing.first() is not None:
        return

    successful = status == AgentRunStatus.SUCCEEDED
    if run_kind == AgentRunKind.SCHEDULED_AGENT_TURN:
        title = "Scheduled task complete" if successful else "Scheduled task needs attention"
    else:
        title = "Cloud task complete" if successful else "Cloud task needs attention"
    body = "Open VoxOS to view the result." if successful else "Open VoxOS to review what happened."
    data = {
        "schema_version": "1",
        "type": "run_terminal",
        "notification_id": event_key,
        "run_id": run.id,
        "chat_id": run.chat_id or "",
        "status": status.value,
    }
    session.add(
        PushNotificationOutboxRecord(
            user_id=run.user_id,
            event_key=event_key,
            kind="run_terminal",
            title=title,
            body=body,
            data_json=data,
            max_attempts=max_attempts,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        ),
    )


async def enqueue_chat_message_notification(
    *,
    session: AsyncSession,
    item: AgentTranscriptItem,
    transcript_id: str,
    max_attempts: int,
) -> None:
    if not should_notify_for_chat_message(item):
        return

    event_key = (
        f"run:{item.notification_run_id}:{AgentRunStatus.SUCCEEDED.value}"
        if item.notification_run_id is not None
        else f"message:{transcript_id}"
    )
    existing = await session.exec(
        select(PushNotificationOutboxRecord.id).where(
            col(PushNotificationOutboxRecord.event_key) == event_key,
        ),
    )
    if existing.first() is not None:
        return

    data = {
        "schema_version": "1",
        "type": "chat_message",
        "notification_id": event_key,
        "message_id": transcript_id,
        "chat_id": item.session_id,
    }
    if item.notification_run_id is not None:
        data["run_id"] = item.notification_run_id
    session.add(
        PushNotificationOutboxRecord(
            user_id=item.user_id,
            event_key=event_key,
            kind="chat_message",
            title="New message from VoxOS",
            body="Open VoxOS to view the message.",
            data_json=data,
            max_attempts=max_attempts,
            next_attempt_at=item.created_at,
            created_at=item.created_at,
            updated_at=item.created_at,
        ),
    )


def should_notify_for_chat_message(item: AgentTranscriptItem) -> bool:
    if item.role != AgentTranscriptRole.ASSISTANT or item.interrupted or not item.is_final:
        return False
    if item.source == AgentTranscriptSource.TEXT_OUTPUT:
        return True
    if item.source == AgentTranscriptSource.SUBAGENT_OUTPUT:
        return item.client_turn_id is not None
    if item.source != AgentTranscriptSource.RUN_UPDATE or item.client_turn_id is None:
        return False
    return item.client_turn_id.endswith(":complete")

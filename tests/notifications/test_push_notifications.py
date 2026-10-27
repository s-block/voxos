from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import col, select

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.models import (
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
)
from voxos.agent_runtime.records import AgentSessionRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.models import (
    PushInstallationRecord,
    PushNotificationOutboxRecord,
    PushOutboxStatus,
)
from voxos.notifications.sender import (
    PushDeliveryTarget,
    PushMessage,
    PushSendResult,
)
from voxos.notifications.settings import PushNotificationSettings
from voxos.notifications.worker import PushNotificationDeliveryWorker


@dataclass(slots=True)
class RecordingPushSender:
    results: list[PushSendResult]
    messages: list[PushMessage] = field(default_factory=list)
    targets: list[tuple[PushDeliveryTarget, ...]] = field(default_factory=list)

    async def send(
        self,
        *,
        message: PushMessage,
        targets: tuple[PushDeliveryTarget, ...],
    ) -> PushSendResult:
        self.messages.append(message)
        self.targets.append(targets)
        return self.results.pop(0)


@dataclass(slots=True)
class RaisingPushSender:
    calls: int = 0

    async def send(
        self,
        *,
        message: PushMessage,
        targets: tuple[PushDeliveryTarget, ...],
    ) -> PushSendResult:
        del message, targets
        self.calls += 1
        error_message = "firebase unavailable"
        raise RuntimeError(error_message)


def _registration_token() -> str:
    return "test-fcm-registration"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_terminal_scheduled_run_enqueues_safe_notification(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
        terminal_notifications_enabled=True,
    )
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SCHEDULED_AGENT_TURN,
        payload_json={"prompt": "private prompt"},
        created_by_user_id="user-123",
        chat_id=None,
    )
    await store.claim_runs(
        worker_id="worker-1",
        queues=("agent-default",),
        batch_size=1,
        lease_seconds=60,
    )

    await store.complete_run(
        run_id=run.id,
        user_id="user-123",
        result_json={"result": "private result"},
    )

    async with sqlite_session_factory() as session:
        records = (await session.exec(select(PushNotificationOutboxRecord))).all()
    assert len(records) == 1
    assert records[0].title == "Scheduled task complete"
    assert records[0].data_json == {
        "schema_version": "1",
        "type": "run_terminal",
        "notification_id": f"run:{run.id}:succeeded",
        "run_id": run.id,
        "chat_id": "",
        "status": "succeeded",
    }
    assert "private" not in str(records[0].data_json)
    assert "private" not in records[0].body


@pytest.mark.asyncio
async def test_text_chat_terminal_run_does_not_enqueue_notification(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
        terminal_notifications_enabled=True,
    )
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={},
        created_by_user_id="user-123",
    )
    await store.claim_runs(
        worker_id="worker-1",
        queues=("agent-default",),
        batch_size=1,
        lease_seconds=60,
    )
    await store.complete_run(run_id=run.id, user_id="user-123")

    async with sqlite_session_factory() as session:
        records = (await session.exec(select(PushNotificationOutboxRecord))).all()
    assert records == []


@pytest.mark.asyncio
async def test_assistant_message_replaces_duplicate_scheduled_completion(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        chat = AgentSessionRecord(
            user_id="user-123",
            agent_id=agent.id,
            provider="openai",
            model="gpt-5.6-sol",
            channel="text",
        )
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

    run_store = SqlModelAgentRunStore(
        session_factory=sqlite_session_factory,
        terminal_notifications_enabled=True,
    )
    run = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SCHEDULED_AGENT_TURN,
        payload_json={},
        created_by_user_id="user-123",
        chat_id=chat.id,
    )
    await run_store.claim_runs(
        worker_id="worker-1",
        queues=("agent-default",),
        batch_size=1,
        lease_seconds=60,
    )
    runtime_store = SqlModelAgentRuntimeStore(
        sqlite_session_factory,
        chat_notifications_enabled=True,
    )
    message = await runtime_store.append_transcript_item(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Private task result",
            source=AgentTranscriptSource.TEXT_OUTPUT,
            notification_run_id=run.id,
            created_at=datetime.now(UTC),
        ),
    )
    retried_message = await runtime_store.append_transcript_item(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Private task result",
            source=AgentTranscriptSource.TEXT_OUTPUT,
            notification_run_id=run.id,
            created_at=datetime.now(UTC),
        ),
    )
    await run_store.complete_run(run_id=run.id, user_id="user-123")

    async with sqlite_session_factory() as session:
        records = (await session.exec(select(PushNotificationOutboxRecord))).all()
    assert len(records) == 1
    assert records[0].kind == "chat_message"
    assert records[0].event_key == f"run:{run.id}:succeeded"
    assert records[0].title == "New message from VoxOS"
    assert records[0].body == "Open VoxOS to view the message."
    assert records[0].data_json == {
        "schema_version": "1",
        "type": "chat_message",
        "notification_id": f"run:{run.id}:succeeded",
        "message_id": message.id,
        "chat_id": chat.id,
        "run_id": run.id,
    }
    assert retried_message.id != message.id
    assert "Private task result" not in str(records[0].data_json)


@pytest.mark.asyncio
async def test_only_user_facing_assistant_messages_enqueue_notifications(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        chat = AgentSessionRecord(
            user_id="user-123",
            agent_id=agent.id,
            provider="openai",
            model="gpt-5.6-sol",
            channel="text",
        )
        session.add(chat)
        await session.commit()
        await session.refresh(chat)

    store = SqlModelAgentRuntimeStore(
        sqlite_session_factory,
        chat_notifications_enabled=True,
    )
    now = datetime.now(UTC)
    await store.append_transcript_item(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.USER,
            text="User message",
            source=AgentTranscriptSource.TEXT_INPUT,
            created_at=now,
        ),
    )
    await store.append_transcript_item(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Internal child result",
            source=AgentTranscriptSource.SUBAGENT_OUTPUT,
            notification_run_id="child-run",
            created_at=now,
        ),
    )
    await store.append_chat_message_once(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Task submitted",
            source=AgentTranscriptSource.RUN_UPDATE,
            client_turn_id="run:cloud-run:submitted",
            notification_run_id="cloud-run",
            created_at=now,
        ),
    )
    await store.append_transcript_item(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Partial reply from a failed run",
            source=AgentTranscriptSource.TEXT_OUTPUT,
            interrupted=True,
            is_final=False,
            notification_run_id="failed-run",
            created_at=now,
        ),
    )
    parent_result = await store.append_chat_message_once(
        AgentTranscriptItem(
            session_id=chat.id,
            user_id="user-123",
            agent_id=agent.id,
            role=AgentTranscriptRole.ASSISTANT,
            text="Parent-visible result",
            source=AgentTranscriptSource.SUBAGENT_OUTPUT,
            client_turn_id="run:child-run:complete",
            notification_run_id="child-run",
            created_at=now,
        ),
    )

    async with sqlite_session_factory() as session:
        records = (await session.exec(select(PushNotificationOutboxRecord))).all()
    assert parent_result is not None
    assert [record.data_json["message_id"] for record in records] == [
        parent_result.record.id,
    ]


@pytest.mark.asyncio
async def test_delivery_worker_retries_transient_and_deactivates_invalid_targets(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        installation = PushInstallationRecord(
            user_id="user-123",
            installation_id="fid-123",
            registration_token=_registration_token(),
            platform="ios",
        )
        session.add(installation)
        await session.commit()
        await session.refresh(installation)
        outbox = PushNotificationOutboxRecord(
            user_id="user-123",
            event_key="run:run-123:failed",
            kind="run_terminal",
            title="Task needs attention",
            body="Open VoxOS to review what happened.",
            data_json={"schema_version": "1", "type": "run_terminal"},
            max_attempts=3,
        )
        session.add(outbox)
        await session.commit()
        await session.refresh(outbox)

    sender = RecordingPushSender(
        results=[
            PushSendResult(
                completed_installation_ids=(),
                invalid_installation_ids=(),
                transient_installation_ids=(installation.id,),
                error_code="UNAVAILABLE",
            ),
        ],
    )
    worker = PushNotificationDeliveryWorker(
        session_factory=sqlite_session_factory,
        sender=sender,
        settings=PushNotificationSettings(
            PUSH_NOTIFICATION_CLAIM_BATCH_SIZE=1,
            PUSH_NOTIFICATION_RETRY_BASE_SECONDS=1,
        ),
        worker_id="push-worker",
    )

    assert await worker.run_once() == 1
    async with sqlite_session_factory() as session:
        retried = await session.get(PushNotificationOutboxRecord, outbox.id)
    assert retried is not None
    assert retried.status == PushOutboxStatus.RETRY.value
    assert retried.attempt_count == 1
    assert retried.last_error_code == "UNAVAILABLE"

    async with sqlite_session_factory() as session:
        retried.next_attempt_at = retried.created_at
        session.add(retried)
        await session.commit()
    sender.results.append(
        PushSendResult(
            completed_installation_ids=(),
            invalid_installation_ids=(installation.id,),
            transient_installation_ids=(),
            error_code="UnregisteredError",
        ),
    )

    assert await worker.run_once() == 1
    async with sqlite_session_factory() as session:
        delivered = await session.get(PushNotificationOutboxRecord, outbox.id)
        active = (
            await session.exec(
                select(PushInstallationRecord).where(
                    col(PushInstallationRecord.id) == installation.id,
                ),
            )
        ).one()
    assert delivered is not None
    assert delivered.status == PushOutboxStatus.DELIVERED.value
    assert active.active is False
    assert len(sender.messages) == 2


@pytest.mark.asyncio
async def test_delivery_worker_retries_sender_exceptions_until_failed(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        installation = PushInstallationRecord(
            user_id="user-123",
            installation_id="fid-123",
            registration_token=_registration_token(),
            platform="ios",
        )
        outbox = PushNotificationOutboxRecord(
            user_id="user-123",
            event_key="message:message-123",
            kind="chat_message",
            title="New message from VoxOS",
            body="Open VoxOS to view the message.",
            data_json={"schema_version": "1", "type": "chat_message"},
            max_attempts=2,
        )
        session.add(installation)
        session.add(outbox)
        await session.commit()
        await session.refresh(outbox)

    sender = RaisingPushSender()
    worker = PushNotificationDeliveryWorker(
        session_factory=sqlite_session_factory,
        sender=sender,
        settings=PushNotificationSettings(
            PUSH_NOTIFICATION_CLAIM_BATCH_SIZE=1,
            PUSH_NOTIFICATION_RETRY_BASE_SECONDS=1,
        ),
        worker_id="push-worker",
    )

    assert await worker.run_once() == 1
    async with sqlite_session_factory() as session:
        retried = await session.get(PushNotificationOutboxRecord, outbox.id)
        assert retried is not None
        assert retried.status == PushOutboxStatus.RETRY.value
        assert retried.attempt_count == 1
        assert retried.lease_owner is None
        assert retried.lease_expires_at is None
        assert retried.last_error_code == "RuntimeError"
        retried.next_attempt_at = retried.created_at
        session.add(retried)
        await session.commit()

    assert await worker.run_once() == 1
    async with sqlite_session_factory() as session:
        failed = await session.get(PushNotificationOutboxRecord, outbox.id)
    assert failed is not None
    assert failed.status == PushOutboxStatus.FAILED.value
    assert failed.attempt_count == 2
    assert failed.lease_owner is None
    assert failed.lease_expires_at is None
    assert failed.last_error_code == "RuntimeError"
    assert sender.calls == 2


@pytest.mark.asyncio
async def test_delivery_claims_use_unique_renewable_leases(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        outbox = PushNotificationOutboxRecord(
            user_id="user-123",
            event_key="message:message-123",
            kind="chat_message",
            title="New message from VoxOS",
            body="Open VoxOS to view the message.",
            data_json={"schema_version": "1", "type": "chat_message"},
            max_attempts=3,
        )
        session.add(outbox)
        await session.commit()
        await session.refresh(outbox)

    worker = PushNotificationDeliveryWorker(
        session_factory=sqlite_session_factory,
        sender=RecordingPushSender(results=[]),
        settings=PushNotificationSettings(
            PUSH_NOTIFICATION_CLAIM_BATCH_SIZE=1,
            PUSH_NOTIFICATION_LEASE_SECONDS=10,
        ),
        worker_id="push-worker",
    )

    first_claim = await worker._claim_next()
    assert first_claim is not None
    first_owner = first_claim.lease_owner
    assert first_owner is not None
    assert first_owner.startswith("push-worker:")
    assert first_owner != "push-worker"

    async with sqlite_session_factory() as session:
        expired = await session.get(PushNotificationOutboxRecord, outbox.id)
        assert expired is not None
        expired.lease_expires_at = expired.created_at - timedelta(seconds=1)
        session.add(expired)
        await session.commit()

    second_claim = await worker._claim_next()
    assert second_claim is not None
    second_owner = second_claim.lease_owner
    assert second_owner is not None
    assert second_owner != first_owner
    expiry_before_renewal = second_claim.lease_expires_at
    assert expiry_before_renewal is not None
    assert (
        await worker._renew_lease(
            outbox_id=outbox.id,
            lease_owner=first_owner,
        )
        is False
    )
    assert (
        await worker._renew_lease(
            outbox_id=outbox.id,
            lease_owner=second_owner,
        )
        is True
    )

    async with sqlite_session_factory() as session:
        renewed = await session.get(PushNotificationOutboxRecord, outbox.id)
    assert renewed is not None
    assert renewed.lease_owner == second_owner
    assert renewed.lease_expires_at is not None
    assert renewed.lease_expires_at >= expiry_before_renewal


@pytest.mark.asyncio
async def test_delivery_worker_fails_expired_exhausted_claims(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = datetime.now(UTC)
    async with sqlite_session_factory() as session:
        outbox = PushNotificationOutboxRecord(
            user_id="user-123",
            event_key="message:message-123",
            kind="chat_message",
            title="New message from VoxOS",
            body="Open VoxOS to view the message.",
            data_json={"schema_version": "1", "type": "chat_message"},
            status=PushOutboxStatus.PROCESSING.value,
            attempt_count=2,
            max_attempts=2,
            lease_owner="dead-worker:claim",
            lease_expires_at=now - timedelta(seconds=1),
        )
        session.add(outbox)
        await session.commit()
        await session.refresh(outbox)

    worker = PushNotificationDeliveryWorker(
        session_factory=sqlite_session_factory,
        sender=RecordingPushSender(results=[]),
        settings=PushNotificationSettings(PUSH_NOTIFICATION_CLAIM_BATCH_SIZE=1),
        worker_id="push-worker",
    )

    assert await worker.run_once() == 0
    async with sqlite_session_factory() as session:
        failed = await session.get(PushNotificationOutboxRecord, outbox.id)
    assert failed is not None
    assert failed.status == PushOutboxStatus.FAILED.value
    assert failed.lease_owner is None
    assert failed.lease_expires_at is None
    assert failed.last_error_code == "lease_expired"

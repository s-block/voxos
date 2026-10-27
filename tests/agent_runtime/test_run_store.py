from datetime import timedelta

import pytest

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.records import AgentScheduleRecord, AgentSessionRecord
from voxos.agent_runtime.run_models import (
    AgentRunEventKind,
    AgentRunKind,
    AgentRunStatus,
)
from voxos.agent_runtime.run_store import (
    AgentRunRelationshipError,
    AgentRunTransitionError,
    SqlModelAgentRunStore,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_run_store_enqueues_claims_completes_and_redacts_events(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)

    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"credential_token": "secret-value", "message_characters": 12},
        created_by_user_id="user-123",
        idempotency_key="turn-1",
    )
    duplicate = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"message_characters": 99},
        created_by_user_id="user-123",
        idempotency_key="turn-1",
    )

    assert duplicate.id == run.id
    detail = await store.get_run_detail(user_id="user-123", run_id=run.id)
    assert detail is not None
    assert detail.payload_json["credential_token"] == "[redacted]"

    claimed = await store.claim_runs(
        worker_id="worker-1",
        queues=("agent-default",),
        batch_size=1,
        lease_seconds=60,
    )

    assert [item.id for item in claimed] == [run.id]
    assert claimed[0].status == AgentRunStatus.RUNNING.value
    assert claimed[0].attempt == 1

    event = await store.append_event(
        run_id=run.id,
        user_id="user-123",
        kind=AgentRunEventKind.PROGRESS,
        payload_json={
            "api_key": "redaction-fixture-value",  # pragma: allowlist secret
            "message": "Working",
        },
    )
    assert event is not None
    assert event.payload_json["api_key"] == "[redacted]"

    completed = await store.complete_run(
        run_id=run.id,
        user_id="user-123",
        result_json={"ok": True},
    )

    assert completed is not None
    assert completed.status == AgentRunStatus.SUCCEEDED.value
    events = await store.list_events(user_id="user-123", run_id=run.id)
    assert [event.kind for event in events] == [
        AgentRunEventKind.RUN_ENQUEUED,
        AgentRunEventKind.RUN_STARTED,
        AgentRunEventKind.PROGRESS,
        AgentRunEventKind.RUN_COMPLETED,
    ]


@pytest.mark.asyncio
async def test_run_store_cancels_running_and_child_runs(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    parent = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Parent"},
        created_by_user_id="user-123",
    )
    child = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SUBAGENT,
        payload_json={"task_prompt": "Child"},
        created_by_user_id="user-123",
        parent_run_id=parent.id,
        root_run_id=parent.id,
    )
    await store.start_run(
        run_id=parent.id,
        user_id="user-123",
        lease_owner="worker-1",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )

    cancelled_parent = await store.request_cancel(
        run_id=parent.id,
        user_id="user-123",
        reason="No longer needed",
    )
    cancelled_child = await store.get_run(user_id="user-123", run_id=child.id)

    assert cancelled_parent is not None
    assert cancelled_parent.status == AgentRunStatus.CANCELLING.value
    assert cancelled_child is not None
    assert cancelled_child.status == AgentRunStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_run_store_retries_failed_runs_only(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Retry me"},
        created_by_user_id="user-123",
    )

    with pytest.raises(AgentRunTransitionError):
        await store.retry_run(run_id=run.id, user_id="user-123")

    await store.start_run(
        run_id=run.id,
        user_id="user-123",
        lease_owner="worker-1",
        lease_expires_at=utc_now() + timedelta(seconds=60),
    )
    await store.fail_run(
        run_id=run.id,
        user_id="user-123",
        error_type="RuntimeError",
        error_message="Failed",
    )

    retried = await store.retry_run(run_id=run.id, user_id="user-123")

    assert retried is not None
    assert retried.status == AgentRunStatus.QUEUED.value
    assert retried.error_type is None


@pytest.mark.asyncio
async def test_run_store_binds_an_unscoped_run_to_its_created_chat(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SUBAGENT,
        payload_json={"task_prompt": "Child"},
        created_by_user_id="user-123",
    )
    async with sqlite_session_factory() as session:
        child_chat = AgentSessionRecord(
            user_id="user-123",
            agent_id=agent.id,
            provider="openai",
            model="gpt-5.4-nano",
            channel="text",
        )
        session.add(child_chat)
        await session.commit()
        await session.refresh(child_chat)

    bound = await store.bind_run_to_chat(
        run_id=run.id,
        user_id="user-123",
        chat_id=child_chat.id,
    )

    assert bound is not None
    assert bound.session_id == child_chat.id
    assert bound.chat_id == child_chat.id


@pytest.mark.asyncio
async def test_run_store_rejects_wrong_user_relationship_links(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    owner_agent = await identity_service.get_or_create_default_agent("owner-user")
    other_agent = await identity_service.get_or_create_default_agent("other-user")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    other_parent = await store.enqueue_run(
        user_id="other-user",
        agent_id=other_agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Other parent"},
        created_by_user_id="other-user",
    )
    async with sqlite_session_factory() as session:
        other_session = AgentSessionRecord(
            user_id="other-user",
            agent_id=other_agent.id,
            provider="openai",
            model="gpt-4.1-mini",
            channel="text",
        )
        other_schedule = AgentScheduleRecord(
            user_id="other-user",
            agent_id=other_agent.id,
            name="Other schedule",
            payload_json={},
        )
        session.add(other_session)
        session.add(other_schedule)
        await session.commit()
        await session.refresh(other_session)
        await session.refresh(other_schedule)

    with pytest.raises(AgentRunRelationshipError):
        await store.enqueue_run(
            user_id="owner-user",
            agent_id=owner_agent.id,
            kind=AgentRunKind.SUBAGENT,
            payload_json={"task_prompt": "Child"},
            created_by_user_id="owner-user",
            parent_run_id=other_parent.id,
        )
    with pytest.raises(AgentRunRelationshipError):
        await store.enqueue_run(
            user_id="owner-user",
            agent_id=owner_agent.id,
            kind=AgentRunKind.TEXT_CHAT_TURN,
            payload_json={"text": "Wrong session"},
            created_by_user_id="owner-user",
            session_id=other_session.id,
        )
    with pytest.raises(AgentRunRelationshipError):
        await store.enqueue_run(
            user_id="owner-user",
            agent_id=owner_agent.id,
            kind=AgentRunKind.SCHEDULED_AGENT_TURN,
            payload_json={"text": "Wrong schedule"},
            created_by_user_id="owner-user",
            schedule_id=other_schedule.id,
        )
    owner_run = await store.enqueue_run(
        user_id="owner-user",
        agent_id=owner_agent.id,
        kind=AgentRunKind.SUBAGENT,
        payload_json={"task_prompt": "Owner child"},
        created_by_user_id="owner-user",
    )
    with pytest.raises(AgentRunRelationshipError):
        await store.bind_run_to_chat(
            run_id=owner_run.id,
            user_id="owner-user",
            chat_id=other_session.id,
        )

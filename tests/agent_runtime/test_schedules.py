from datetime import UTC, timedelta

import pytest

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.records import AgentScheduleRecord
from voxos.agent_runtime.run_models import AgentRunStatus
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.schedule_models import (
    AgentScheduleCreateRequest,
    ScheduleKind,
    ScheduleStatus,
)
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService, RunChatValidationError


@pytest.mark.asyncio
async def test_schedule_store_enqueues_due_one_shot_and_completes_schedule(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    schedule = await schedule_store.create_schedule(
        user_id="user-123",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            name="Daily check",
            kind=ScheduleKind.ONE_SHOT,
            run_at=utc_now() - timedelta(seconds=1),
            payload_json={"text": "Summarize my priorities"},
        ),
    )

    enqueued = await schedule_store.enqueue_due_schedules()
    updated = await schedule_store.get_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    runs = await run_store.list_runs(user_id="user-123")

    assert [item.id for item in enqueued] == [schedule.id]
    assert updated is not None
    assert updated.status == ScheduleStatus.COMPLETED
    assert updated.enabled is False
    assert len(runs) == 1
    assert runs[0].schedule_id == schedule.id
    assert schedule.chat_id is not None
    assert runs[0].chat_id == schedule.chat_id


@pytest.mark.asyncio
async def test_schedule_store_pause_resume_and_run_now_are_user_scoped(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    schedule = await schedule_store.create_schedule(
        user_id="user-123",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            name="Heartbeat",
            kind=ScheduleKind.INTERVAL,
            interval_seconds=120,
            payload_json={"text": "Check in"},
        ),
    )

    assert (
        await schedule_store.pause_schedule(
            user_id="other-user",
            schedule_id=schedule.id,
        )
        is None
    )

    paused = await schedule_store.pause_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    resumed = await schedule_store.resume_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    run_now = await schedule_store.run_now(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    runs = await run_store.list_runs(user_id="user-123")

    assert paused is not None
    assert paused.status == ScheduleStatus.PAUSED
    assert resumed is not None
    assert resumed.status == ScheduleStatus.ACTIVE
    assert run_now is not None
    assert run_now.last_run_status == AgentRunStatus.QUEUED
    assert len(runs) == 1
    assert run_now.chat_id is not None
    assert runs[0].chat_id == run_now.chat_id


@pytest.mark.asyncio
async def test_deleting_a_schedule_chat_pauses_the_schedule(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    schedule = await schedule_store.create_schedule(
        user_id="user-123",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            name="Heartbeat",
            kind=ScheduleKind.INTERVAL,
            interval_seconds=120,
            payload_json={"text": "Check in"},
        ),
    )
    assert schedule.chat_id is not None

    deleted = await SqlModelAgentRuntimeStore(
        sqlite_session_factory,
    ).soft_delete_session(
        session_id=schedule.chat_id,
        user_id="user-123",
        agent_id=agent.id,
    )
    updated = await schedule_store.get_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )

    assert deleted is not None
    assert updated is not None
    assert updated.enabled is False
    assert updated.status == ScheduleStatus.PAUSED

    with pytest.raises(RunChatValidationError):
        await schedule_store.resume_schedule(
            user_id="user-123",
            schedule_id=schedule.id,
        )
    with pytest.raises(RunChatValidationError):
        await schedule_store.run_now(
            user_id="user-123",
            schedule_id=schedule.id,
        )

    async with sqlite_session_factory() as session:
        record = await session.get(AgentScheduleRecord, schedule.id)
        assert record is not None
        record.enabled = True
        record.status = ScheduleStatus.ACTIVE.value
        record.next_run_at = utc_now() - timedelta(seconds=1)
        session.add(record)
        await session.commit()

    assert await schedule_store.enqueue_due_schedules() == ()
    safely_paused = await schedule_store.get_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    assert safely_paused is not None
    assert safely_paused.enabled is False
    assert safely_paused.status == ScheduleStatus.PAUSED


@pytest.mark.asyncio
async def test_schedule_reuses_a_selected_chat_for_each_run(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    chat = await schedule_store.run_chat_service.create_chat(
        user_id="user-123",
        agent_id=agent.id,
        title="Existing chat",
    )
    schedule = await schedule_store.create_schedule(
        user_id="user-123",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            chat_id=chat.id,
            name="Heartbeat",
            kind=ScheduleKind.INTERVAL,
            interval_seconds=120,
            payload_json={"text": "Check in"},
        ),
    )

    await schedule_store.run_now(user_id="user-123", schedule_id=schedule.id)
    await schedule_store.run_now(user_id="user-123", schedule_id=schedule.id)
    runs = await run_store.list_runs(user_id="user-123")

    assert schedule.chat_id == chat.id
    assert len(runs) == 2
    assert {run.chat_id for run in runs} == {chat.id}


@pytest.mark.asyncio
async def test_legacy_schedule_claims_one_chat_on_first_run(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    async with sqlite_session_factory() as session:
        record = AgentScheduleRecord(
            user_id="user-123",
            agent_id=agent.id,
            name="Legacy heartbeat",
            kind=ScheduleKind.INTERVAL.value,
            interval_seconds=120,
            payload_json={"text": "Check in"},
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        schedule_id = record.id

    first = await schedule_store.run_now(user_id="user-123", schedule_id=schedule_id)
    second = await schedule_store.run_now(user_id="user-123", schedule_id=schedule_id)
    runs = await run_store.list_runs(user_id="user-123")

    assert first is not None
    assert first.chat_id is not None
    assert second is not None
    assert second.chat_id == first.chat_id
    assert {run.chat_id for run in runs} == {first.chat_id}


@pytest.mark.asyncio
async def test_schedule_store_skips_missed_runs_beyond_grace(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    schedule_store = create_schedule_store(
        session_factory=sqlite_session_factory,
        run_store=run_store,
        identity_service=identity_service,
    )
    schedule = await schedule_store.create_schedule(
        user_id="user-123",
        request=AgentScheduleCreateRequest(
            agent_id=agent.id,
            name="Missed interval",
            kind=ScheduleKind.INTERVAL,
            run_at=utc_now() - timedelta(hours=2),
            interval_seconds=300,
            misfire_grace_seconds=60,
            payload_json={"text": "Check in"},
        ),
    )

    enqueued = await schedule_store.enqueue_due_schedules()
    updated = await schedule_store.get_schedule(
        user_id="user-123",
        schedule_id=schedule.id,
    )
    runs = await run_store.list_runs(user_id="user-123")

    assert enqueued == ()
    assert runs == ()
    assert updated is not None
    assert updated.next_run_at is not None
    assert updated.next_run_at.replace(tzinfo=UTC) > utc_now()


def create_schedule_store(
    *,
    session_factory: AsyncSessionFactory,
    run_store: SqlModelAgentRunStore,
    identity_service: AgentIdentityService,
) -> AgentScheduleStore:
    runtime_store = SqlModelAgentRuntimeStore(session_factory)
    return AgentScheduleStore(
        session_factory=session_factory,
        run_store=run_store,
        identity_service=identity_service,
        run_chat_service=RunChatService(
            session_factory=session_factory,
            runtime_store=runtime_store,
            run_store=run_store,
        ),
    )

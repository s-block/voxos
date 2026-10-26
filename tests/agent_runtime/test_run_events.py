import pytest

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.run_models import AgentRunEventKind, AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_run_event_cursor_returns_events_after_seen_event(
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
        payload_json={"text": "Track events"},
        created_by_user_id="user-123",
    )
    first_batch = await store.list_events(user_id="user-123", run_id=run.id)

    progress = await store.append_event(
        run_id=run.id,
        user_id="user-123",
        kind=AgentRunEventKind.PROGRESS,
        payload_json={"message": "after cursor"},
    )
    second_batch = await store.list_events(
        user_id="user-123",
        run_id=run.id,
        since_event_id=first_batch[-1].id,
    )

    assert progress is not None
    assert [event.id for event in second_batch] == [progress.id]

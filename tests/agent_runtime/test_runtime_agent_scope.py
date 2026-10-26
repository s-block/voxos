import pytest

from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_runtime_session_status_update_requires_agent_scope(
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
        room_metadata={},
        prompt_version=None,
        prompt_snapshot=None,
        prompt_hash=None,
    )

    wrong_agent_result = await store.end_session(
        session_id=session.id,
        user_id="user-123",
        agent_id="agent-456",
        close_reason=None,
        close_error_type=None,
        close_error_message=None,
    )
    correct_agent_result = await store.end_session(
        session_id=session.id,
        user_id="user-123",
        agent_id="agent-123",
        close_reason="done",
        close_error_type=None,
        close_error_message=None,
    )

    assert wrong_agent_result is None
    assert correct_agent_result is not None
    assert correct_agent_result.agent_id == "agent-123"
    assert correct_agent_result.status == "ended"

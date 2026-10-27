import pytest

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.models import (
    AgentTranscriptItem,
    AgentTranscriptRole,
    AgentTranscriptSource,
)
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService, RunChatValidationError


@pytest.mark.asyncio
async def test_run_chat_delivery_is_idempotent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "user-123"
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    chat = await service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Background task",
    )
    await service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Newer inactive chat",
    )
    run = await run_store.enqueue_run(
        user_id=user_id,
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Check the account"},
        created_by_user_id=user_id,
        session_id=chat.id,
        chat_id=chat.id,
    )

    first = await service.deliver_to_run(
        run=run,
        text="The account check completed.",
        stage="complete",
    )
    repeated = await service.deliver_to_run(
        run=run,
        text="The account check completed.",
        stage="complete",
    )
    transcript = await runtime_store.list_transcript_items(
        session_id=chat.id,
        user_id=user_id,
        agent_id=agent.id,
    )
    chats = await runtime_store.list_sessions(
        user_id=user_id,
        agent_id=agent.id,
        channel="text",
    )

    assert first.delivered is True
    assert first.created is True
    assert repeated.delivered is True
    assert repeated.created is False
    assert repeated.transcript_id == first.transcript_id
    assert len(transcript) == 1
    assert transcript[0].client_turn_id == f"run:{run.id}:complete"
    assert transcript[0].source == AgentTranscriptSource.RUN_UPDATE.value
    assert chats[0].id == chat.id


@pytest.mark.asyncio
async def test_regular_text_messages_refresh_chat_activity_order(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "user-123"
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    older_chat = await service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Older chat",
    )
    newer_chat = await service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Newer chat",
    )

    await runtime_store.append_transcript_item(
        AgentTranscriptItem(
            session_id=older_chat.id,
            user_id=user_id,
            agent_id=agent.id,
            role=AgentTranscriptRole.USER,
            text="Continue the older conversation",
            source=AgentTranscriptSource.TEXT_INPUT,
            created_at=utc_now(),
        ),
    )
    chats = await runtime_store.list_sessions(
        user_id=user_id,
        agent_id=agent.id,
        channel="text",
    )

    assert [chat.id for chat in chats[:2]] == [older_chat.id, newer_chat.id]


@pytest.mark.asyncio
async def test_subagent_delivery_uses_the_parent_chat_agent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    user_id = "user-123"
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    parent_chat = await service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Parent chat",
    )
    parent = await run_store.enqueue_run(
        user_id=user_id,
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Delegate this"},
        created_by_user_id=user_id,
        session_id=parent_chat.id,
        chat_id=parent_chat.id,
    )
    child = await run_store.enqueue_run(
        user_id=user_id,
        agent_id=agent.id,
        kind=AgentRunKind.SUBAGENT,
        payload_json={"task_prompt": "Research this"},
        created_by_user_id=user_id,
        parent_run_id=parent.id,
        parent_session_id=parent_chat.id,
        root_run_id=parent.id,
    )

    result = await service.deliver_to_parent(
        run=child,
        text="Research completed.",
    )
    transcript = await runtime_store.list_transcript_items(
        session_id=parent_chat.id,
        user_id=user_id,
        agent_id=agent.id,
    )

    assert result.delivered is True
    assert len(transcript) == 1
    assert transcript[0].source == AgentTranscriptSource.SUBAGENT_OUTPUT.value


@pytest.mark.asyncio
async def test_selected_chat_must_match_the_user_and_agent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    owner_agent = await identity_service.get_or_create_default_agent("owner-user")
    other_agent = await identity_service.get_or_create_default_agent("other-user")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=SqlModelAgentRuntimeStore(sqlite_session_factory),
        run_store=run_store,
    )
    other_chat = await service.create_chat(
        user_id="other-user",
        agent_id=other_agent.id,
        title="Other user's chat",
    )
    voice_session = await SqlModelAgentRuntimeStore(
        sqlite_session_factory,
    ).create_session(
        user_id="owner-user",
        agent_id=owner_agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        channel="livekit",
        voice="alloy",
        room_name="room-1",
        room_metadata={},
        prompt_version=None,
        prompt_snapshot=None,
        prompt_hash=None,
    )

    async with sqlite_session_factory() as session:
        with pytest.raises(RunChatValidationError):
            await service.require_chat_in_session(
                session=session,
                user_id="owner-user",
                agent_id=owner_agent.id,
                chat_id=other_chat.id,
            )
        with pytest.raises(RunChatValidationError):
            await service.require_chat_in_session(
                session=session,
                user_id="owner-user",
                agent_id=owner_agent.id,
                chat_id=voice_session.id,
            )

    run = await run_store.enqueue_run(
        user_id="other-user",
        agent_id=other_agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Already linked"},
        created_by_user_id="other-user",
        session_id=other_chat.id,
        chat_id=other_chat.id,
    )
    await SqlModelAgentRuntimeStore(sqlite_session_factory).soft_delete_session(
        session_id=other_chat.id,
        user_id="other-user",
        agent_id=other_agent.id,
    )
    async with sqlite_session_factory() as session:
        with pytest.raises(RunChatValidationError):
            await service.require_chat_in_session(
                session=session,
                user_id="other-user",
                agent_id=other_agent.id,
                chat_id=other_chat.id,
            )
    unavailable = await service.deliver_to_run(
        run=run,
        text="Should not be delivered",
        stage="complete",
    )
    assert unavailable.delivered is False
    assert unavailable.unavailable_reason == "chat_unavailable"

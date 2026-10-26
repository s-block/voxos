import pytest

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyService,
    effective_tool_policy_from_json,
)
from voxos.agent_runtime.run_models import AgentRunEventKind, AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.subagents import (
    SubAgentContextMode,
    SubAgentService,
    SubAgentSpawnRequest,
)
from voxos.agent_runtime.tool_authorization import (
    READ_ONLY_INTERNAL_TOOL_NAMES,
    SqlModelToolAuthorizationService,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_subagent_service_spawns_child_run_with_parent_visible_event(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
            ToolGrantUpdate(
                tool_name="delegate_agent_task",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    parent_policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )
    parent = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Parent task"},
        created_by_user_id="user-123",
        effective_tool_policy_json=parent_policy.to_json(),
    )
    service = SubAgentService(run_store=store, identity_service=identity_service)

    child = await service.spawn_subagent(
        user_id="user-123",
        request=SubAgentSpawnRequest(
            parent_run_id=parent.id,
            target_agent_id=agent.id,
            task_prompt="Research one option",
            context_mode=SubAgentContextMode.SUMMARY,
            parent_summary="Parent summary",
            inputs={"topic": "runtime"},
        ),
    )

    assert child is not None
    assert child.parent_run_id == parent.id
    assert child.root_run_id == parent.id
    assert child.payload_json["context_mode"] == "summary"
    child_policy = effective_tool_policy_from_json(child.effective_tool_policy_json)
    assert child_policy is not None
    assert {entry.tool_name for entry in child_policy.entries} == (
        set(READ_ONLY_INTERNAL_TOOL_NAMES) - {"search_agent_history", "read_agent_history"}
    )
    events = await store.list_events(user_id="user-123", run_id=parent.id)
    assert AgentRunEventKind.SUBAGENT_STARTED in [event.kind for event in events]


@pytest.mark.asyncio
async def test_subagent_does_not_reuse_the_parent_chat(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    parent_chat = await runtime_store.create_session(
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-5.4-nano",
        channel="text",
        title="Parent chat",
        voice=None,
        room_name=None,
        room_metadata={},
        prompt_version=None,
        prompt_snapshot=None,
        prompt_hash=None,
    )
    run_store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    parent_policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id=parent_chat.id,
    )
    parent = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Parent task"},
        created_by_user_id="user-123",
        session_id=parent_chat.id,
        chat_id=parent_chat.id,
        effective_tool_policy_json=parent_policy.to_json(),
    )

    child = await SubAgentService(
        run_store=run_store,
        identity_service=identity_service,
    ).spawn_subagent(
        user_id="user-123",
        request=SubAgentSpawnRequest(
            parent_run_id=parent.id,
            target_agent_id=agent.id,
            task_prompt="Research one option",
        ),
    )

    assert child is not None
    assert child.session_id is None
    assert child.chat_id is None
    assert child.parent_session_id == parent_chat.id


@pytest.mark.asyncio
async def test_subagent_service_denies_wrong_user_parent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    parent = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Parent task"},
        created_by_user_id="user-123",
    )
    service = SubAgentService(run_store=store, identity_service=identity_service)

    child = await service.spawn_subagent(
        user_id="other-user",
        request=SubAgentSpawnRequest(
            parent_run_id=parent.id,
            target_agent_id=agent.id,
            task_prompt="Research one option",
        ),
    )

    assert child is None


@pytest.mark.asyncio
async def test_subagent_child_policy_cannot_expand_parent_permissions(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    parent_agent = await identity_service.get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        target_agent_record = UserAgentRecord(
            owner_user_id="user-123",
            slug="researcher",
            display_name="Researcher",
        )
        session.add(target_agent_record)
        await session.commit()
        await session.refresh(target_agent_record)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=parent_agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
        ),
    )
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=target_agent_record.id,
        grants=(
            ToolGrantUpdate(
                tool_name="list_agent_memories",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.LOW,
            ),
            ToolGrantUpdate(
                tool_name="create_agent_memory",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    parent_policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=parent_agent.id,
    )
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    parent = await store.enqueue_run(
        user_id="user-123",
        agent_id=parent_agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Parent task"},
        created_by_user_id="user-123",
        effective_tool_policy_json=parent_policy.to_json(),
    )
    service = SubAgentService(run_store=store, identity_service=identity_service)

    child = await service.spawn_subagent(
        user_id="user-123",
        request=SubAgentSpawnRequest(
            parent_run_id=parent.id,
            target_agent_id=target_agent_record.id,
            task_prompt="Research one option",
        ),
    )

    assert child is not None
    child_policy = effective_tool_policy_from_json(child.effective_tool_policy_json)
    assert child_policy is not None
    assert {entry.tool_name for entry in child_policy.entries} == (
        set(READ_ONLY_INTERNAL_TOOL_NAMES) - {"search_agent_history", "read_agent_history"}
    )

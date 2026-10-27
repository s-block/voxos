import pytest

from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyService,
    effective_tool_policy_from_json,
)
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.tool_authorization import (
    READ_ONLY_INTERNAL_TOOL_NAMES,
    SqlModelToolAuthorizationService,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolReference,
    ToolRisk,
)
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_effective_tool_policy_intersection_allows_only_shared_tools(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    parent_agent = await identity_service.get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        target_agent = UserAgentRecord(
            owner_user_id="user-123",
            slug="target",
            display_name="Target",
        )
        session.add(target_agent)
        await session.commit()
        await session.refresh(target_agent)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    policy_service = EffectiveToolPolicyService(authorization_service)
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
        agent_id=target_agent.id,
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
    parent_policy = await policy_service.snapshot_for_agent(
        user_id="user-123",
        agent_id=parent_agent.id,
    )

    child_policy = await policy_service.snapshot_for_subagent(
        parent_policy=parent_policy,
        user_id="user-123",
        target_agent_id=target_agent.id,
    )

    assert {entry.tool_name for entry in child_policy.entries} == (
        set(READ_ONLY_INTERNAL_TOOL_NAMES) - {"search_agent_history", "read_agent_history"}
    )
    assert child_policy.parent_policy_id == parent_policy.id


@pytest.mark.asyncio
async def test_image_generation_is_a_medium_risk_root_default(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")

    policy = await EffectiveToolPolicyService(
        SqlModelToolAuthorizationService(sqlite_session_factory),
    ).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    image_entry = next(entry for entry in policy.entries if entry.tool_name == "generate_image")
    assert image_entry.tool_source == ToolGrantScope.INTERNAL
    assert image_entry.risk == ToolRisk.MEDIUM


@pytest.mark.asyncio
async def test_run_scoped_authorization_uses_policy_snapshot_not_new_grants(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
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
        ),
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Use policy"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="create_agent_memory",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        run_id=run.id,
        channel="test",
        source="test",
    )

    allowed = await authorization_service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="list_agent_memories",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
        ),
    )
    denied = await authorization_service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="create_agent_memory",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        ),
    )

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "run effective policy denies execution"


@pytest.mark.asyncio
async def test_run_scoped_authorization_denies_missing_policy(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "No policy"},
        created_by_user_id="user-123",
    )
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        run_id=run.id,
        channel="test",
        source="test",
    )

    decision = await authorization_service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="list_agent_memories",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
        ),
    )

    assert decision.allowed is False
    assert decision.reason == "run has no valid effective tool policy"
    assert effective_tool_policy_from_json(run.effective_tool_policy_json) is None


@pytest.mark.asyncio
async def test_livekit_session_authorization_uses_persisted_policy_snapshot(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
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
        ),
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="livekit",
    )
    await SqlModelAgentRuntimeStore(sqlite_session_factory).create_session(
        session_id="session-123",
        user_id="user-123",
        agent_id=agent.id,
        provider="openai_realtime",
        model="gpt-realtime",
        voice="marin",
        room_name="voice-room",
        room_metadata={},
        prompt_version=None,
        prompt_snapshot=None,
        prompt_hash=None,
        effective_tool_policy_json=policy.to_json(),
    )
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="create_agent_memory",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="livekit",
        source="voice.worker",
    )

    allowed_names = await authorization_service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("list_agent_memories", "create_agent_memory"),
    )
    denied = await authorization_service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="create_agent_memory",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        ),
    )

    assert allowed_names == ("list_agent_memories",)
    assert denied.allowed is False
    assert denied.reason == "session effective policy denies execution"


@pytest.mark.asyncio
async def test_livekit_session_authorization_fails_closed_without_valid_policy(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    await SqlModelAgentRuntimeStore(sqlite_session_factory).create_session(
        session_id="session-123",
        user_id="user-123",
        agent_id=agent.id,
        provider="openai_realtime",
        model="gpt-realtime",
        voice="marin",
        room_name="voice-room",
        room_metadata={},
        prompt_version=None,
        prompt_snapshot=None,
        prompt_hash=None,
    )
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="livekit",
        source="voice.worker",
    )

    allowed_names = await authorization_service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("get_current_datetime",),
    )
    decision = await authorization_service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="get_current_datetime",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
        ),
    )

    assert allowed_names == ()
    assert decision.allowed is False
    assert decision.reason == "session has no valid effective tool policy"

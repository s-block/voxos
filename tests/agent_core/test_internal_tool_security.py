import pytest
from sqlmodel import select

from voxos.agent_core.internal_tools import InternalAgentToolProvider
from voxos.agent_core.models import AgentRunContext
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.records import AgentToolAuditEventRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_execution import ToolAuthorityArgumentError
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.agent_skills.services import AgentSkillService
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_internal_tool_rejects_llm_supplied_user_id(
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
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Use tools"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name="list_agent_memories",
            call_id="call-123",
            arguments_json={"request": {"user_id": "other-user"}},
        )
    async with sqlite_session_factory() as session:
        result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = result.all()
    assert audit_events[-1].user_id == "user-123"
    assert audit_events[-1].run_id == run.id
    assert audit_events[-1].decision == "denied"
    assert audit_events[-1].metadata_json == {"category": "invalid_arguments"}


@pytest.mark.asyncio
async def test_internal_docs_tool_rejects_llm_supplied_user_id(
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
                tool_name="search_voxos_docs",
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
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Search docs"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name="search_voxos_docs",
            call_id="call-123",
            arguments_json={
                "query": "MCP setup",
                "user_id": "other-user",
            },
        )
    async with sqlite_session_factory() as session:
        result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = result.all()
    assert audit_events[-1].user_id == "user-123"
    assert audit_events[-1].run_id == run.id
    assert audit_events[-1].tool_name == "search_voxos_docs"
    assert audit_events[-1].decision == "denied"
    assert audit_events[-1].metadata_json == {"category": "invalid_arguments"}


@pytest.mark.asyncio
async def test_internal_tool_rejects_llm_supplied_session_id_field(
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
                tool_name="create_task_plan",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.ALLOWED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )
    policy = await EffectiveToolPolicyService(authorization_service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Use tools"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    provider = InternalAgentToolProvider(
        memory_service=AgentMemoryService(sqlite_session_factory),
        skill_service=AgentSkillService(sqlite_session_factory),
        planning_service=AgentPlanningService(sqlite_session_factory),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id="session-123",
        run_id=run.id,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-4.1-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name="create_task_plan",
            call_id="call-123",
            arguments_json={
                "request": {
                    "objective": "Plan",
                    "steps": ["Step 1"],
                    "session_id": "other-session",
                },
            },
        )

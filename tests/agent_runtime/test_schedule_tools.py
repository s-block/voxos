from datetime import timedelta

import pytest
from sqlmodel import select

from voxos.agent_core.models import AgentRunContext
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.records import AgentScheduleRecord, AgentToolAuditEventRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.schedule_tools import (
    CREATE_AGENT_SCHEDULE_TOOL_NAME,
    AgentScheduleToolProvider,
)
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_execution import (
    ToolAuthorityArgumentError,
    ToolExecutionSecurityError,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService


@pytest.mark.asyncio
async def test_schedule_tool_creates_a_tenant_owned_task_in_the_current_chat(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    run_chat_service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    chat = await run_chat_service.create_chat(
        user_id="user-123",
        agent_id=agent.id,
        title="Race updates",
    )
    authorization_service = SqlModelToolAuthorizationService(
        sqlite_session_factory,
    )
    policy = await EffectiveToolPolicyService(
        authorization_service,
    ).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
        session_id=chat.id,
    )
    run = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"text": "Schedule a race update"},
        created_by_user_id="user-123",
        session_id=chat.id,
        chat_id=chat.id,
        effective_tool_policy_json=policy.to_json(),
    )
    provider = AgentScheduleToolProvider(
        schedule_store=AgentScheduleStore(
            session_factory=sqlite_session_factory,
            run_store=run_store,
            identity_service=identity_service,
            run_chat_service=run_chat_service,
        ),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id=chat.id,
        run_id=run.id,
        run_kind=AgentRunKind.TEXT_CHAT_TURN,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
        effective_tool_policy_id=policy.id,
    )
    run_at = utc_now() + timedelta(hours=1)

    tools = await provider.list_tools(context)
    result = await provider.execute_tool(
        context=context,
        tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
        call_id="call-schedule",
        arguments_json={
            "name": "Race start update",
            "instruction": "Check the race status and summarize it.",
            "kind": "one_shot",
            "run_at": run_at.isoformat(),
            "timezone": "UTC",
        },
    )

    assert [tool.name for tool in tools] == [CREATE_AGENT_SCHEDULE_TOOL_NAME]
    assert result.is_error is False
    assert result.raw_output["schedule"]["chat_id"] == chat.id
    async with sqlite_session_factory() as session:
        schedules = (await session.exec(select(AgentScheduleRecord))).all()
    assert len(schedules) == 1
    assert schedules[0].user_id == "user-123"
    assert schedules[0].agent_id == agent.id
    assert schedules[0].chat_id == chat.id
    assert schedules[0].payload_json == {
        "text": "Check the race status and summarize it.",
    }


@pytest.mark.asyncio
async def test_schedule_tool_rejects_noninteractive_worker_runs(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    run_chat_service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    chat = await run_chat_service.create_chat(
        user_id="user-123",
        agent_id=agent.id,
        title="Scheduled worker chat",
    )
    run = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SCHEDULED_AGENT_TURN,
        payload_json={"text": "Run unattended"},
        created_by_user_id="user-123",
        session_id=chat.id,
        chat_id=chat.id,
    )
    authorization_service = SqlModelToolAuthorizationService(
        sqlite_session_factory,
    )
    provider = AgentScheduleToolProvider(
        schedule_store=AgentScheduleStore(
            session_factory=sqlite_session_factory,
            run_store=run_store,
            identity_service=identity_service,
            run_chat_service=run_chat_service,
        ),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id=chat.id,
        run_id=run.id,
        run_kind=AgentRunKind.SCHEDULED_AGENT_TURN,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
    )

    assert await provider.list_tools(context) == ()
    with pytest.raises(
        ToolExecutionSecurityError,
        match="only during an interactive text chat turn",
    ):
        await provider.execute_tool(
            context=context.model_copy(
                update={"run_kind": AgentRunKind.TEXT_CHAT_TURN},
            ),
            tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
            call_id="call-schedule",
            arguments_json={
                "name": "Recursive schedule",
                "instruction": "Create another schedule.",
                "run_at": (utc_now() + timedelta(hours=1)).isoformat(),
            },
        )

    async with sqlite_session_factory() as session:
        schedules = (await session.exec(select(AgentScheduleRecord))).all()
        audit_events = (await session.exec(select(AgentToolAuditEventRecord))).all()
    assert schedules == []
    assert audit_events[-1].metadata_json == {
        "category": "invalid_execution_scope",
    }


@pytest.mark.asyncio
async def test_schedule_tool_rejects_model_selected_chat_authority(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    run_chat_service = RunChatService(
        session_factory=sqlite_session_factory,
        runtime_store=runtime_store,
        run_store=run_store,
    )
    chat = await run_chat_service.create_chat(
        user_id="user-123",
        agent_id=agent.id,
        title="Owner chat",
    )
    run = await run_store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"text": "Schedule elsewhere"},
        created_by_user_id="user-123",
        session_id=chat.id,
        chat_id=chat.id,
    )
    authorization_service = SqlModelToolAuthorizationService(
        sqlite_session_factory,
    )
    provider = AgentScheduleToolProvider(
        schedule_store=AgentScheduleStore(
            session_factory=sqlite_session_factory,
            run_store=run_store,
            identity_service=identity_service,
            run_chat_service=run_chat_service,
        ),
        authorization_service=authorization_service,
    )
    context = AgentRunContext(
        session_id=chat.id,
        run_id=run.id,
        run_kind=AgentRunKind.TEXT_CHAT_TURN,
        root_run_id=run.root_run_id,
        user_id="user-123",
        agent_id=agent.id,
        provider="openai",
        model="gpt-5.4-mini",
        instructions="System instructions.",
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=context,
            tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
            call_id="call-schedule",
            arguments_json={
                "name": "Wrong chat",
                "instruction": "Post elsewhere.",
                "run_at": (utc_now() + timedelta(hours=1)).isoformat(),
                "chat_id": "other-user-chat",
            },
        )

    async with sqlite_session_factory() as session:
        schedules = (await session.exec(select(AgentScheduleRecord))).all()
        audit_events = (await session.exec(select(AgentToolAuditEventRecord))).all()
    assert schedules == []
    assert audit_events[-1].user_id == "user-123"
    assert audit_events[-1].agent_id == agent.id
    assert audit_events[-1].decision == "denied"
    assert audit_events[-1].metadata_json == {"category": "invalid_arguments"}

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.records import AgentToolAuditEventRecord, AgentToolGrantRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import (
    SqlModelToolAuthorizationService,
    ToolGrantValidationError,
)
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolReference,
    ToolRisk,
)
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.defaults import InitialMcpServerProvisioner
from voxos.mcp.records import UserMcpServerRecord


@pytest.mark.asyncio
async def test_tool_authorization_denies_mutating_internal_tool_by_default_then_allows_grant(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    reference = ToolReference(
        tool_name="create_agent_memory",
        tool_source=ToolGrantScope.INTERNAL,
        risk=ToolRisk.MEDIUM,
    )

    denied = await service.authorize_tool(scope=scope, reference=reference)

    assert denied.allowed is False

    await service.set_grants(
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

    allowed_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("create_agent_memory", "list_agent_memories"),
    )
    allowed = await service.authorize_tool(scope=scope, reference=reference)
    policy = await EffectiveToolPolicyService(service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )
    run = await SqlModelAgentRunStore(sqlite_session_factory).enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Run with policy"},
        created_by_user_id="user-123",
        effective_tool_policy_json=policy.to_json(),
    )
    run_scope = scope.model_copy(update={"run_id": run.id})
    run_allowed = await service.authorize_tool(scope=run_scope, reference=reference)

    assert allowed_names == ("create_agent_memory", "list_agent_memories")
    assert allowed.allowed is True
    assert run_allowed.allowed is True

    async with sqlite_session_factory() as session:
        result = await session.exec(select(AgentToolAuditEventRecord))
        audit_events = result.all()

    assert tuple(event.tool_name for event in audit_events) == (
        "create_agent_memory",
        "create_agent_memory",
        "create_agent_memory",
        "create_agent_memory",
    )
    assert audit_events[-1].run_id == run.id


@pytest.mark.asyncio
async def test_tool_authorization_allows_all_read_only_internal_tools_by_default(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )

    allowed_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=(
            "get_current_datetime",
            "list_agent_memories",
            "list_agent_skills",
            "get_agent_skill",
            "list_task_plans",
            "search_contacts",
            "search_voxos_docs",
            "read_voxos_doc_page",
        ),
    )
    decision = await service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="search_voxos_docs",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
        ),
    )
    policy = await EffectiveToolPolicyService(service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    assert allowed_names == (
        "get_current_datetime",
        "list_agent_memories",
        "list_agent_skills",
        "get_agent_skill",
        "list_task_plans",
        "search_contacts",
        "search_voxos_docs",
        "read_voxos_doc_page",
    )
    assert decision.allowed is True
    assert {entry.tool_name for entry in policy.entries} >= {
        "get_current_datetime",
        "list_agent_memories",
        "list_agent_skills",
        "get_agent_skill",
        "list_task_plans",
        "search_contacts",
        "search_voxos_docs",
        "read_voxos_doc_page",
    }


@pytest.mark.asyncio
async def test_contact_search_is_allowed_by_default_and_can_be_denied(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    service = SqlModelToolAuthorizationService(sqlite_session_factory)

    allowed_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("search_contacts",),
    )
    await service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.DENIED,
                risk=ToolRisk.LOW,
            ),
        ),
    )
    denied_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("search_contacts",),
    )

    assert allowed_names == ("search_contacts",)
    assert denied_names == ()


@pytest.mark.asyncio
async def test_tool_authorization_can_deny_default_docs_tool(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="test",
        source="test",
    )
    service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    await service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search_voxos_docs",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.DENIED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    allowed_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.INTERNAL,
        tool_names=("search_voxos_docs", "read_voxos_doc_page"),
    )
    decision = await service.authorize_tool(
        scope=scope,
        reference=ToolReference(
            tool_name="search_voxos_docs",
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.LOW,
        ),
    )

    assert allowed_names == ("read_voxos_doc_page",)
    assert decision.allowed is False


@pytest.mark.asyncio
async def test_tool_authorization_allows_platform_default_exa_until_denied(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    await InitialMcpServerProvisioner(sqlite_session_factory).initialize_user(
        "user-123",
    )
    async with sqlite_session_factory() as session:
        result = await session.exec(select(UserMcpServerRecord))
        server = result.one()
    scope = AgentScope(
        owner_user_id="user-123",
        agent_id=agent.id,
        session_id="session-123",
        channel="text",
        source="test",
    )
    service = SqlModelToolAuthorizationService(sqlite_session_factory)
    reference = ToolReference(
        tool_name="web_search_exa",
        tool_source=ToolGrantScope.MCP_SERVER,
        mcp_server_id=server.id,
        risk=ToolRisk.LOW,
    )

    allowed_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.MCP_SERVER,
        tool_names=("web_search_exa", "web_fetch_exa"),
        mcp_server_id=server.id,
    )
    decision = await service.authorize_tool(scope=scope, reference=reference)
    policy = await EffectiveToolPolicyService(service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    assert allowed_names == ("web_search_exa", "web_fetch_exa")
    assert decision.allowed is True
    assert {
        (entry.mcp_server_id, entry.tool_name)
        for entry in policy.entries
        if entry.tool_source == ToolGrantScope.MCP_SERVER
    } == {
        (server.id, "web_search_exa"),
        (server.id, "web_fetch_exa"),
    }

    await service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="web_search_exa",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=server.id,
                status=ToolGrantStatus.DENIED,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    denied_names = await service.allowed_tool_names(
        scope=scope,
        tool_source=ToolGrantScope.MCP_SERVER,
        tool_names=("web_search_exa", "web_fetch_exa"),
        mcp_server_id=server.id,
    )
    denied = await service.authorize_tool(scope=scope, reference=reference)

    assert denied_names == ("web_fetch_exa",)
    assert denied.allowed is False

    async with sqlite_session_factory() as session:
        stored_server = await session.get(UserMcpServerRecord, server.id)
        assert stored_server is not None
        stored_server.enabled = False
        stored_server.status = "disabled"
        await session.commit()

    disabled_policy = await EffectiveToolPolicyService(service).snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    assert all(
        entry.mcp_server_id != server.id
        for entry in disabled_policy.entries
        if entry.tool_source == ToolGrantScope.MCP_SERVER
    )


@pytest.mark.asyncio
async def test_tool_authorization_validates_mcp_server_owner(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        server = UserMcpServerRecord(
            user_id="other-user",
            label="GitHub",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            auth_type="none",
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)

    service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )

    with pytest.raises(ToolGrantValidationError):
        await service.set_grants(
            user_id="user-123",
            agent_id=agent.id,
            grants=(
                ToolGrantUpdate(
                    tool_name="issues.list",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id=server.id,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.MEDIUM,
                ),
            ),
        )


@pytest.mark.asyncio
async def test_tool_authorization_validates_mcp_tool_is_server_allowed(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    async with sqlite_session_factory() as session:
        server = UserMcpServerRecord(
            user_id="user-123",
            label="GitHub",
            transport="streamable_http",
            url="https://mcp.example.com/mcp",
            auth_type="none",
            allowed_tools=[],
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)

    service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )

    with pytest.raises(ToolGrantValidationError, match="server allowlist"):
        await service.set_grants(
            user_id="user-123",
            agent_id=agent.id,
            grants=(
                ToolGrantUpdate(
                    tool_name="issues.list",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id=server.id,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.MEDIUM,
                ),
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_source", "mcp_server_id", "integration_id", "tool_name"),
    (
        (ToolGrantScope.INTERNAL.value, None, None, "list_agent_memories"),
        (ToolGrantScope.MCP_SERVER.value, "server-1", None, "issues.list"),
        (ToolGrantScope.INTEGRATION.value, None, "integration-1", "twilio_send_sms"),
    ),
)
async def test_tool_grant_scope_indexes_reject_duplicate_logical_grants(
    sqlite_session_factory: AsyncSessionFactory,
    tool_source: str,
    mcp_server_id: str | None,
    integration_id: str | None,
    tool_name: str,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(
            AgentToolGrantRecord(
                user_id="user-123",
                agent_id="agent-123",
                tool_name=tool_name,
                tool_source=tool_source,
                mcp_server_id=mcp_server_id,
                integration_id=integration_id,
            ),
        )
        session.add(
            AgentToolGrantRecord(
                user_id="user-123",
                agent_id="agent-123",
                tool_name=tool_name,
                tool_source=tool_source,
                mcp_server_id=mcp_server_id,
                integration_id=integration_id,
            ),
        )

        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

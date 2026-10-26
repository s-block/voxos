import pytest

from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.agent_runtime.tool_routing import (
    WEB_SEARCH_ACTION,
    SqlModelToolRoutingService,
    ToolRouteMode,
    ToolRoutePreferenceUpdate,
    ToolRouteSelection,
)
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.records import UserMcpServerRecord


@pytest.mark.asyncio
async def test_web_search_route_prefers_exa_and_snapshots_pinned_choice(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    exa_server = UserMcpServerRecord(
        user_id="user-123",
        label="Exa",
        transport="streamable_http",
        url="https://mcp.exa.ai/mcp",
        auth_type="none",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "exa"},
        allowed_tools=["web_search_exa"],
    )
    browserless_server = UserMcpServerRecord(
        user_id="user-123",
        label="Browserless",
        transport="streamable_http",
        url="https://mcp.browserless.io/mcp",
        auth_type="static_bearer",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "browserless"},
        allowed_tools=["browserless_search"],
    )
    async with sqlite_session_factory() as session:
        session.add(exa_server)
        session.add(browserless_server)
        await session.commit()
        await session.refresh(exa_server)
        await session.refresh(browserless_server)

    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            mcp_grant("web_search_exa", exa_server.id),
            mcp_grant("browserless_search", browserless_server.id),
        ),
    )
    routing_service = SqlModelToolRoutingService(sqlite_session_factory)
    policy_service = EffectiveToolPolicyService(
        authorization_service,
        routing_service,
    )

    automatic_policy = await policy_service.snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    automatic_route = automatic_policy.resolved_routes[0]
    assert automatic_route.action == WEB_SEARCH_ACTION
    assert automatic_route.mode == ToolRouteMode.AUTOMATIC
    assert automatic_route.selected_tool is not None
    assert automatic_route.selected_tool.tool_name == "web_search_exa"

    async with sqlite_session_factory() as session:
        persisted_exa = await session.get(UserMcpServerRecord, exa_server.id)
        assert persisted_exa is not None
        persisted_exa.enabled = False
        session.add(persisted_exa)
        await session.commit()
    fallback_policy = await policy_service.snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )
    assert fallback_policy.resolved_routes[0].selected_tool is not None
    assert fallback_policy.resolved_routes[0].selected_tool.tool_name == "browserless_search"
    async with sqlite_session_factory() as session:
        persisted_exa = await session.get(UserMcpServerRecord, exa_server.id)
        assert persisted_exa is not None
        persisted_exa.enabled = True
        session.add(persisted_exa)
        await session.commit()

    grants = await authorization_service.list_effective_grants(
        scope_for(agent.id),
    )
    await routing_service.set_preferences(
        user_id="user-123",
        agent_id=agent.id,
        grants=grants,
        preferences=(
            ToolRoutePreferenceUpdate(
                action=WEB_SEARCH_ACTION,
                mode=ToolRouteMode.PINNED,
                pinned_tool=ToolRouteSelection(
                    tool_name="browserless_search",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id=browserless_server.id,
                ),
            ),
        ),
    )
    pinned_policy = await policy_service.snapshot_for_agent(
        user_id="user-123",
        agent_id=agent.id,
    )

    pinned_route = pinned_policy.resolved_routes[0]
    assert pinned_route.mode == ToolRouteMode.PINNED
    assert pinned_route.selected_tool is not None
    assert pinned_route.selected_tool.tool_name == "browserless_search"
    assert automatic_policy.resolved_routes[0].selected_tool is not None
    assert automatic_policy.resolved_routes[0].selected_tool.tool_name == "web_search_exa"


@pytest.mark.asyncio
async def test_pinned_web_search_does_not_silently_fallback_when_unavailable(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    browserless_server = UserMcpServerRecord(
        user_id="user-123",
        label="Browserless",
        transport="streamable_http",
        url="https://mcp.browserless.io/mcp",
        auth_type="none",
        enabled=True,
        trust_state="trusted",
        status="connected",
        safe_metadata={"preset_id": "browserless"},
        allowed_tools=["browserless_search"],
    )
    async with sqlite_session_factory() as session:
        session.add(browserless_server)
        await session.commit()
        await session.refresh(browserless_server)
    authorization_service = SqlModelToolAuthorizationService(sqlite_session_factory)
    routing_service = SqlModelToolRoutingService(sqlite_session_factory)
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(mcp_grant("browserless_search", browserless_server.id),),
    )
    grants = await authorization_service.list_effective_grants(scope_for(agent.id))
    await routing_service.set_preferences(
        user_id="user-123",
        agent_id=agent.id,
        grants=grants,
        preferences=(
            ToolRoutePreferenceUpdate(
                action=WEB_SEARCH_ACTION,
                mode=ToolRouteMode.PINNED,
                pinned_tool=ToolRouteSelection(
                    tool_name="browserless_search",
                    tool_source=ToolGrantScope.MCP_SERVER,
                    mcp_server_id=browserless_server.id,
                ),
            ),
        ),
    )
    await authorization_service.set_grants(
        user_id="user-123",
        agent_id=agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="browserless_search",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id=browserless_server.id,
                status=ToolGrantStatus.DENIED,
                risk=ToolRisk.MEDIUM,
            ),
        ),
    )

    policy = await EffectiveToolPolicyService(
        authorization_service,
        routing_service,
    ).snapshot_for_agent(user_id="user-123", agent_id=agent.id)

    assert policy.resolved_routes[0].mode == ToolRouteMode.PINNED
    assert policy.resolved_routes[0].selected_tool is None


def mcp_grant(tool_name: str, server_id: str) -> ToolGrantUpdate:
    return ToolGrantUpdate(
        tool_name=tool_name,
        tool_source=ToolGrantScope.MCP_SERVER,
        mcp_server_id=server_id,
        status=ToolGrantStatus.ALLOWED,
        risk=ToolRisk.MEDIUM,
    )


def scope_for(agent_id: str) -> AgentScope:
    return AgentScope(
        owner_user_id="user-123",
        agent_id=agent_id,
        channel="test",
        source="test",
    )

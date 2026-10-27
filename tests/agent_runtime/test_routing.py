import pytest

from voxos.agent_runtime.routing import AgentRouteResolver
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_livekit_route_resolver_creates_default_agent_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    resolver = AgentRouteResolver(session_factory=sqlite_session_factory)

    route = await resolver.resolve_livekit_route(owner_user_id="user-123")

    assert route.owner_user_id == "user-123"
    assert route.agent.owner_user_id == "user-123"
    assert route.agent.is_default is True
    assert route.runtime_session_id
    assert route.scope().agent_id == route.agent.id
    assert route.scope().session_id == route.runtime_session_id

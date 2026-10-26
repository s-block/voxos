from dataclasses import dataclass

from voxos.agent_identity.models import AgentIdentity, AgentScope
from voxos.agent_identity.services import AgentIdentityService
from voxos.db.models import new_record_id
from voxos.db.session import AsyncSessionFactory


@dataclass(frozen=True, slots=True)
class AgentRouteContext:
    owner_user_id: str
    agent: AgentIdentity
    runtime_session_id: str
    channel: str
    source: str

    def scope(self) -> AgentScope:
        return AgentScope(
            owner_user_id=self.owner_user_id,
            agent_id=self.agent.id,
            session_id=self.runtime_session_id,
            channel=self.channel,
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class AgentRouteResolver:
    session_factory: AsyncSessionFactory

    async def resolve_livekit_route(
        self,
        *,
        owner_user_id: str,
        agent_id: str | None = None,
    ) -> AgentRouteContext:
        service = AgentIdentityService(session_factory=self.session_factory)
        agent = (
            await service.get_or_create_default_agent(owner_user_id)
            if agent_id is None
            else await service.require_agent(
                owner_user_id=owner_user_id,
                agent_id=agent_id,
            )
        )
        return AgentRouteContext(
            owner_user_id=owner_user_id,
            agent=agent,
            runtime_session_id=new_record_id(),
            channel="livekit",
            source="api.livekit.token",
        )

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_identity.models import (
    DEFAULT_AGENT_DISPLAY_NAME,
    DEFAULT_AGENT_SLUG,
    AgentIdentity,
    AgentStatus,
    AgentUpdateRequest,
)
from voxos.agent_identity.records import UserAgentRecord
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


class AgentIdentityNotFoundError(ValueError):
    def __init__(self, agent_id: str) -> None:
        super().__init__("Agent was not found.")
        self.agent_id = agent_id


@dataclass(frozen=True, slots=True)
class AgentIdentityService:
    session_factory: AsyncSessionFactory

    async def get_or_create_default_agent(self, owner_user_id: str) -> AgentIdentity:
        async with self.session_factory() as session:
            existing = await self._default_agent_record(
                session=session,
                owner_user_id=owner_user_id,
            )
            if existing is not None:
                return agent_from_record(existing)

            record = UserAgentRecord(
                owner_user_id=owner_user_id,
                slug=DEFAULT_AGENT_SLUG,
                display_name=DEFAULT_AGENT_DISPLAY_NAME,
                is_default=True,
            )
            session.add(record)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await self._default_agent_record(
                    session=session,
                    owner_user_id=owner_user_id,
                )
                if existing is None:
                    raise
                return agent_from_record(existing)
            await session.refresh(record)
            return agent_from_record(record)

    async def list_agents(self, owner_user_id: str) -> tuple[AgentIdentity, ...]:
        statement = (
            select(UserAgentRecord)
            .where(col(UserAgentRecord.owner_user_id) == owner_user_id)
            .order_by(
                col(UserAgentRecord.is_default).desc(),
                col(UserAgentRecord.created_at).asc(),
            )
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(agent_from_record(record) for record in result.all())

    async def get_agent(
        self,
        *,
        owner_user_id: str,
        agent_id: str,
    ) -> AgentIdentity | None:
        async with self.session_factory() as session:
            record = await self._agent_record(
                session=session,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
            )
            if record is None:
                return None
            return agent_from_record(record)

    async def require_agent(
        self,
        *,
        owner_user_id: str,
        agent_id: str,
    ) -> AgentIdentity:
        agent = await self.get_agent(owner_user_id=owner_user_id, agent_id=agent_id)
        if agent is None:
            raise AgentIdentityNotFoundError(agent_id)
        return agent

    async def update_agent(
        self,
        *,
        owner_user_id: str,
        agent_id: str,
        request: AgentUpdateRequest,
    ) -> AgentIdentity | None:
        async with self.session_factory() as session:
            record = await self._agent_record(
                session=session,
                owner_user_id=owner_user_id,
                agent_id=agent_id,
            )
            if record is None:
                return None
            if "display_name" in request.model_fields_set and request.display_name is not None:
                record.display_name = request.display_name
            if "description" in request.model_fields_set:
                record.description = request.description
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
            return agent_from_record(record)

    async def _default_agent_record(
        self,
        *,
        session: AsyncSession,
        owner_user_id: str,
    ) -> UserAgentRecord | None:
        statement = select(UserAgentRecord).where(
            col(UserAgentRecord.owner_user_id) == owner_user_id,
            col(UserAgentRecord.is_default).is_(True),
        )
        result = await session.exec(statement)
        record = result.first()
        if record is not None:
            return record

        fallback_statement = select(UserAgentRecord).where(
            col(UserAgentRecord.owner_user_id) == owner_user_id,
            col(UserAgentRecord.slug) == DEFAULT_AGENT_SLUG,
        )
        fallback_result = await session.exec(fallback_statement)
        fallback = fallback_result.one_or_none()
        if fallback is None:
            return None
        fallback.is_default = True
        fallback.updated_at = utc_now()
        await session.commit()
        await session.refresh(fallback)
        return fallback

    async def _agent_record(
        self,
        *,
        session: AsyncSession,
        owner_user_id: str,
        agent_id: str,
    ) -> UserAgentRecord | None:
        statement = select(UserAgentRecord).where(
            col(UserAgentRecord.id) == agent_id,
            col(UserAgentRecord.owner_user_id) == owner_user_id,
        )
        result = await session.exec(statement)
        return result.one_or_none()


def agent_from_record(record: UserAgentRecord) -> AgentIdentity:
    return AgentIdentity(
        id=record.id,
        owner_user_id=record.owner_user_id,
        slug=record.slug,
        display_name=record.display_name,
        description=record.description,
        status=AgentStatus(record.status),
        is_default=record.is_default,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

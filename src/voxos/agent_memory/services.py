import re
from dataclasses import dataclass, field

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_memory.models import (
    AgentMemory,
    MemoryCreateRequest,
    MemoryKind,
    MemorySearchRequest,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
    MemoryUpdateRequest,
)
from voxos.agent_memory.records import AgentMemoryRecord
from voxos.agent_memory.update_policy import MemoryUpdatePolicy
from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory

NORMALIZED_WORD_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class AgentMemoryService:
    session_factory: AsyncSessionFactory
    update_policy: MemoryUpdatePolicy = field(default_factory=MemoryUpdatePolicy)

    def create_learning_in_session(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        request: MemoryCreateRequest,
    ) -> AgentMemoryRecord:
        """Compose a policy-approved learning mutation in the caller's transaction."""
        record = AgentMemoryRecord(
            user_id=user_id,
            kind=request.kind.value,
            status=MemoryStatus.ACTIVE.value,
            sensitivity=request.sensitivity.value,
            source=MemorySource.REFLECTION.value,
            text=request.text,
            normalized_key=normalized_memory_key(kind=request.kind.value, text=request.text),
            confidence=request.confidence,
            created_by_reflection_id=request.created_by_reflection_id,
        )
        session.add(record)
        return record

    async def create_memory(
        self,
        *,
        user_id: str,
        request: MemoryCreateRequest,
    ) -> AgentMemory:
        normalized_key = normalized_memory_key(kind=request.kind.value, text=request.text)
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            existing = await self._active_memory_for_key_in_session(
                session=session,
                user_id=user_id,
                normalized_key=normalized_key,
            )
            if existing is not None and existing.text == request.text:
                return memory_from_record(existing)

            decision = self.update_policy.decide_create(
                request,
                has_active_conflict=existing is not None,
            )
            record = AgentMemoryRecord(
                user_id=user_id,
                kind=request.kind.value,
                status=decision.status.value,
                sensitivity=request.sensitivity.value,
                source=request.source.value,
                text=request.text,
                normalized_key=normalized_key,
                confidence=request.confidence,
                evidence_session_id=request.evidence_session_id,
                evidence_transcript_id=request.evidence_transcript_id,
                created_by_reflection_id=request.created_by_reflection_id,
            )
            if existing is not None and decision.status == MemoryStatus.ACTIVE:
                existing.status = MemoryStatus.SUPERSEDED.value
                existing.updated_at = utc_now()
                record.supersedes_memory_id = existing.id
                session.add(existing)
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return memory_from_record(record)

    async def propose_memory(
        self,
        *,
        user_id: str,
        request: MemoryCreateRequest,
    ) -> AgentMemory:
        proposal_request = request.model_copy(
            update={
                "source": MemorySource.ASSISTANT_INFERRED,
                "confirmed": False,
                "evidence_session_id": None,
                "evidence_transcript_id": None,
                "created_by_reflection_id": None,
            },
        )
        normalized_key = normalized_memory_key(
            kind=proposal_request.kind.value,
            text=proposal_request.text,
        )
        record = AgentMemoryRecord(
            user_id=user_id,
            kind=proposal_request.kind.value,
            status=MemoryStatus.PROPOSED.value,
            sensitivity=proposal_request.sensitivity.value,
            source=proposal_request.source.value,
            text=proposal_request.text,
            normalized_key=normalized_key,
            confidence=proposal_request.confidence,
            evidence_session_id=proposal_request.evidence_session_id,
            evidence_transcript_id=proposal_request.evidence_transcript_id,
            created_by_reflection_id=proposal_request.created_by_reflection_id,
        )
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return memory_from_record(record)

    async def confirm_memory(self, *, user_id: str, memory_id: str) -> AgentMemory | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await session.get(AgentMemoryRecord, memory_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status != MemoryStatus.PROPOSED.value:
                message = "Only proposed memories can be confirmed"
                raise ValueError(message)
            active_conflict = await self._active_memory_for_key_in_session(
                session=session,
                user_id=user_id,
                normalized_key=record.normalized_key,
                exclude_memory_id=record.id,
            )
            if active_conflict is not None:
                active_conflict.status = MemoryStatus.SUPERSEDED.value
                active_conflict.updated_at = utc_now()
                record.supersedes_memory_id = active_conflict.id
                session.add(active_conflict)
            record.status = MemoryStatus.ACTIVE.value
            record.source = MemorySource.USER_EXPLICIT.value
            record.created_by_reflection_id = None
            record.updated_at = utc_now()
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return memory_from_record(record)

    async def update_memory(
        self,
        *,
        user_id: str,
        request: MemoryUpdateRequest,
    ) -> AgentMemory | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await session.get(AgentMemoryRecord, request.memory_id)
            if record is None or record.user_id != user_id:
                return None

            if request.text is not None or request.confirmed or request.status == MemoryStatus.ACTIVE:
                record.created_by_reflection_id = None
                record.source = MemorySource.USER_EXPLICIT.value
            if request.text is not None:
                record.text = request.text
                record.normalized_key = normalized_memory_key(
                    kind=record.kind,
                    text=request.text,
                )
            if request.status is not None:
                record.status = request.status.value
            if request.confirmed:
                record.status = MemoryStatus.ACTIVE.value
            if record.status == MemoryStatus.ACTIVE.value:
                active_conflict = await self._active_memory_for_key_in_session(
                    session=session,
                    user_id=user_id,
                    normalized_key=record.normalized_key,
                    exclude_memory_id=record.id,
                )
                if active_conflict is not None:
                    active_conflict.status = MemoryStatus.SUPERSEDED.value
                    active_conflict.updated_at = utc_now()
                    record.supersedes_memory_id = active_conflict.id
                    session.add(active_conflict)

            record.updated_at = utc_now()
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return memory_from_record(record)

    async def archive_memory(self, *, user_id: str, memory_id: str) -> AgentMemory | None:
        return await self._set_status(
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.ARCHIVED,
        )

    async def delete_memory(self, *, user_id: str, memory_id: str) -> AgentMemory | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await session.get(AgentMemoryRecord, memory_id)
            if record is None or record.user_id != user_id:
                return None

            deleted = memory_from_record(record)
            referring_statement = select(AgentMemoryRecord).where(
                col(AgentMemoryRecord.user_id) == user_id,
                col(AgentMemoryRecord.supersedes_memory_id) == memory_id,
            )
            referring_result = await session.exec(referring_statement)
            for referring_record in referring_result.all():
                referring_record.supersedes_memory_id = None
                referring_record.updated_at = utc_now()

            await session.delete(record)
            await session.commit()
        return deleted

    async def reject_memory(self, *, user_id: str, memory_id: str) -> AgentMemory | None:
        return await self._set_status(
            user_id=user_id,
            memory_id=memory_id,
            status=MemoryStatus.REJECTED,
        )

    async def list_memories(
        self,
        *,
        user_id: str,
        request: MemorySearchRequest | None = None,
    ) -> tuple[AgentMemory, ...]:
        search = request or MemorySearchRequest()
        statement = select(AgentMemoryRecord).where(
            col(AgentMemoryRecord.user_id) == user_id,
        )
        if search.statuses:
            statement = statement.where(
                col(AgentMemoryRecord.status).in_(
                    tuple(status.value for status in search.statuses),
                ),
            )
        if search.kinds:
            statement = statement.where(
                col(AgentMemoryRecord.kind).in_(
                    tuple(kind.value for kind in search.kinds),
                ),
            )
        statement = statement.order_by(col(AgentMemoryRecord.updated_at).desc()).limit(
            search.limit,
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(memory_from_record(record) for record in result.all())

    async def active_memories(
        self,
        *,
        user_id: str,
        limit: int = 12,
    ) -> tuple[AgentMemory, ...]:
        return await self.list_memories(
            user_id=user_id,
            request=MemorySearchRequest(statuses=(MemoryStatus.ACTIVE,), limit=limit),
        )

    async def _set_status(
        self,
        *,
        user_id: str,
        memory_id: str,
        status: MemoryStatus,
    ) -> AgentMemory | None:
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await session.get(AgentMemoryRecord, memory_id)
            if record is None or record.user_id != user_id:
                return None
            record.status = status.value
            record.updated_at = utc_now()
            await session.commit()
            await session.refresh(record)
        return memory_from_record(record)

    async def _active_memory_for_key_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        normalized_key: str,
        exclude_memory_id: str | None = None,
    ) -> AgentMemoryRecord | None:
        statement = select(AgentMemoryRecord).where(
            col(AgentMemoryRecord.user_id) == user_id,
            col(AgentMemoryRecord.normalized_key) == normalized_key,
            col(AgentMemoryRecord.status) == MemoryStatus.ACTIVE.value,
        )
        if exclude_memory_id is not None:
            statement = statement.where(col(AgentMemoryRecord.id) != exclude_memory_id)
        result = await session.exec(statement)
        return result.first()


def normalized_memory_key(*, kind: str, text: str) -> str:
    words = NORMALIZED_WORD_PATTERN.findall(text.lower())
    if not words:
        return f"{kind}:memory"
    return f"{kind}:{'-'.join(words[:16])}"[:256]


def memory_from_record(record: AgentMemoryRecord) -> AgentMemory:
    return AgentMemory(
        id=record.id,
        user_id=record.user_id,
        kind=MemoryKind(record.kind),
        status=MemoryStatus(record.status),
        sensitivity=MemorySensitivity(record.sensitivity),
        source=MemorySource(record.source),
        text=record.text,
        normalized_key=record.normalized_key,
        confidence=record.confidence,
        evidence_session_id=record.evidence_session_id,
        evidence_transcript_id=record.evidence_transcript_id,
        supersedes_memory_id=record.supersedes_memory_id,
        created_by_reflection_id=record.created_by_reflection_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )

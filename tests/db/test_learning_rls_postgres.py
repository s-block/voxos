"""Opt-in tests against a migrated disposable PostgreSQL database with forced RLS."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import orjson
import pytest
import pytest_asyncio
from sqlalchemy import insert, text
from sqlalchemy import select as sa_select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession as SqlAlchemySession
from sqlmodel import col, delete, select

from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.models import MemoryKind
from voxos.agent_memory.records import AgentMemoryRecord
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.history import TenantHistoryService
from voxos.agent_runtime.history_models import EvidenceKind, EvidenceReference, HistoryQuery
from voxos.agent_runtime.improvement_policy import ImprovementCandidate, LearningAssessment, MemoryLearning
from voxos.agent_runtime.improvements import AgentImprovementService, ImprovementReview
from voxos.agent_runtime.records import (
    AgentImprovementEvidenceRecord,
    AgentLearningSettingsRecord,
    AgentReflectionJobRecord,
    AgentReflectionProposalRecord,
    AgentRunEventRecord,
    AgentRunRecord,
    AgentSessionRecord,
    AgentToolAuditEventRecord,
    AgentTranscriptRecord,
)
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_execution import ToolExecutionContext
from voxos.agent_skills.services import AgentSkillService
from voxos.db.engine import create_session_factory
from voxos.db.models import utc_now
from voxos.db.settings import normalize_async_database_url
from voxos.db.tenant import tenant_scoped_session_factory


@pytest_asyncio.fixture
async def learning_postgres_engine() -> AsyncIterator[AsyncEngine]:
    value = os.environ.get("VOXOS_RLS_TEST_DATABASE_URL")
    if value is None:
        pytest.skip("Set VOXOS_RLS_TEST_DATABASE_URL to a disposable migrated database")
    engine = create_async_engine(normalize_async_database_url(value), pool_size=2, max_overflow=0, pool_timeout=5)
    async with engine.connect() as connection:
        role = (
            await connection.execute(
                text("SELECT current_database(), rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        assert "test" in role[0] and role[1:] == (False, False)
        policies = (
            await connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relname IN ('agent_learning_settings', 'agent_improvement_evidence')"
                )
            )
        ).all()
        assert len(policies) == 2 and all(row[1:] == (True, True) for row in policies)
    try:
        yield engine
    finally:
        await engine.dispose()


class CertainAssessment:
    async def assess(self, **kwargs: object) -> LearningAssessment:
        return LearningAssessment(
            confidence=0.99,
            supported=True,
            safe=True,
            applicable=True,
            no_conflict=True,
            explicit_owner_statement=True,
            reason="Owner statement supports the preference",
        )


@pytest.mark.asyncio
async def test_learning_rls_concurrency_and_connection_reuse(learning_postgres_engine: AsyncEngine) -> None:
    base = create_session_factory(learning_postgres_engine)
    user = f"learning-test-{uuid.uuid4()}"
    factory = tenant_scoped_session_factory(base, user)
    other_user = f"other-{uuid.uuid4()}"
    other_factory = tenant_scoped_session_factory(base, other_user)
    agent = await AgentIdentityService(factory).get_or_create_default_agent(user)
    authorization = SqlModelToolAuthorizationService(factory)
    policy = await EffectiveToolPolicyService(authorization).snapshot_for_agent(user_id=user, agent_id=agent.id)
    session = AgentSessionRecord(user_id=user, agent_id=agent.id, provider="openai", model="test", channel="text")
    transcript = AgentTranscriptRecord(
        session_id=session.id,
        user_id=user,
        agent_id=agent.id,
        role="user",
        source="text_input",
        owner_authenticated=True,
        text="I prefer concise answers",
    )
    try:
        async with factory() as db:
            db.add(session)
            db.add(transcript)
            await db.commit()
        run = await SqlModelAgentRunStore(factory).enqueue_run(
            user_id=user,
            agent_id=agent.id,
            kind=AgentRunKind.TEXT_CHAT_TURN,
            session_id=session.id,
            created_by_user_id=user,
            payload_json={},
            effective_tool_policy_json=policy.to_json(),
        )
        context = ToolExecutionContext(
            user_id=user, agent_id=agent.id, run_id=run.id, session_id=session.id, channel="text", source="test"
        )
        history = TenantHistoryService(factory, authorization)
        service = AgentImprovementService(
            factory, history, AgentMemoryService(factory), AgentSkillService(factory), CertainAssessment()
        )
        await service.settings(user_id=user, automatic=True)
        candidate = ImprovementCandidate(
            payload=MemoryLearning(memory_kind=MemoryKind.PREFERENCE, text="I prefer concise answers"),
            reason="Explicit owner preference",
            evidence=(EvidenceReference(kind=EvidenceKind.TRANSCRIPT, key=transcript.id),),
            confidence=0.99,
        )
        first, second = await asyncio.gather(service.submit(context, candidate), service.submit(context, candidate))
        assert first.id == second.id and first.status == second.status == "applied"
        assert first.duplicate != second.duplicate
        async with factory() as db:
            assert (
                len((await db.exec(select(AgentMemoryRecord).where(col(AgentMemoryRecord.user_id) == user))).all()) == 1
            )
        # Wrong tenant SQL cannot read, update or create either new tenant table.
        async with other_factory() as db:
            assert await db.get(AgentLearningSettingsRecord, user) is None
            assert (
                await db.exec(
                    select(AgentImprovementEvidenceRecord).where(col(AgentImprovementEvidenceRecord.user_id) == user)
                )
            ).all() == []
            changed = await db.execute(
                text("UPDATE agent_learning_settings SET automatic = false WHERE user_id = :user"), {"user": user}
            )
            assert changed.rowcount == 0
            with pytest.raises(DBAPIError):
                db.add(AgentLearningSettingsRecord(user_id=user + "-forged"))
                await db.flush()
            await db.rollback()
        async with other_factory() as db:
            with pytest.raises(DBAPIError):
                db.add(
                    AgentImprovementEvidenceRecord(
                        user_id=user,
                        proposal_id=first.id,
                        session_id=session.id,
                        source_kind="tool",
                        source_key=str(uuid.uuid4()),
                    )
                )
                await db.flush()
            await db.rollback()
        # RLS permits an owned row, but composite FKs forbid linking foreign sources/proposals.
        async with other_factory() as db:
            with pytest.raises(DBAPIError):
                db.add(
                    AgentImprovementEvidenceRecord(
                        user_id=other_user,
                        proposal_id=first.id,
                        session_id=session.id,
                        source_kind="tool",
                        source_key=str(uuid.uuid4()),
                    )
                )
                await db.flush()
            await db.rollback()
        async with base() as db:
            assert await db.get(AgentLearningSettingsRecord, user) is None
        # Tenant context is reset when the pooled connection returns to another account.
        foreign_history = TenantHistoryService(other_factory, SqlModelToolAuthorizationService(other_factory))
        assert not (
            await foreign_history.search_owned(user_id=context.user_id, request=HistoryQuery(category="all"))
        ).items
        assert (await history.search(context, HistoryQuery(category="all"))).items
        assert first.content_hash is not None
        undo = ImprovementReview(content_hash=first.content_hash, action="undo")
        results = await asyncio.gather(
            service.review(user_id=user, proposal_id=first.id, request=undo),
            service.review(user_id=user, proposal_id=first.id, request=undo),
        )
        assert all(result.status == "archived" for result in results)
        assert (await service.submit(context, candidate)).status == "archived"
    finally:
        async with factory() as db:
            for model in (
                AgentImprovementEvidenceRecord,
                AgentMemoryRecord,
                AgentReflectionProposalRecord,
                AgentReflectionJobRecord,
                AgentToolAuditEventRecord,
                AgentRunEventRecord,
                AgentTranscriptRecord,
                AgentRunRecord,
                AgentSessionRecord,
                AgentLearningSettingsRecord,
            ):
                await db.exec(delete(model).where(col(model.user_id) == user))
            await db.exec(delete(UserAgentRecord).where(col(UserAgentRecord.owner_user_id) == user))
            await db.commit()


@pytest.mark.asyncio
async def test_history_query_uses_tenant_time_index_on_retained_volume(learning_postgres_engine: AsyncEngine) -> None:
    base = create_session_factory(learning_postgres_engine)
    user = f"learning-volume-test-{uuid.uuid4()}"
    factory = tenant_scoped_session_factory(base, user)
    session = AgentSessionRecord(user_id=user, agent_id=None, provider="openai", model="test", channel="text")
    end = utc_now()
    try:
        async with factory() as db:
            db.add(session)
            await db.flush()
            await SqlAlchemySession.execute(
                db,
                insert(AgentTranscriptRecord),
                [
                    AgentTranscriptRecord(
                        user_id=user,
                        session_id=session.id,
                        agent_id=None,
                        role="assistant",
                        source="text_output",
                        text="Retained test message",
                        created_at=end - timedelta(hours=index),
                    ).model_dump()
                    for index in range(10000)
                ],
            )
            await db.commit()
        sources = TenantHistoryService._sources(user)
        statement = (
            sa_select(sources)
            .where(
                sources.c.created_at >= end - timedelta(days=7),
                sources.c.created_at < end,
                sources.c.kind == "transcript",
            )
            .order_by(sources.c.created_at, sources.c.kind, sources.c.key)
            .limit(51)
        )
        sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        async with factory() as db:
            await SqlAlchemySession.execute(db, text("ANALYZE agent_transcripts"))
            plan = (
                await SqlAlchemySession.execute(db, text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql))
            ).scalar_one()
        serialized = orjson.dumps(plan).decode()
        assert any(
            name in serialized
            for name in ("ix_agent_transcripts_user_created_at", "ix_agent_transcripts_session_created_at")
        ), serialized
        assert plan[0]["Plan"]["Actual Rows"] == 51
        assert plan[0]["Plan"]["Node Type"] == "Limit"
    finally:
        async with factory() as db:
            await db.exec(delete(AgentTranscriptRecord).where(col(AgentTranscriptRecord.user_id) == user))
            await db.exec(delete(AgentSessionRecord).where(col(AgentSessionRecord.user_id) == user))
            await db.commit()

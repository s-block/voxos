import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlmodel import col, select

from voxos.agent_runtime.history_models import EvidenceKind, EvidenceReference, HistoryQuery, HistoryReadRequest
from voxos.agent_runtime.improvements import AgentImprovementService
from voxos.agent_runtime.records import AgentReflectionJobRecord, AgentRunRecord
from voxos.agent_runtime.run_execution import AgentRunExecutionResult
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.service_model import LearningModelUnavailableError, ReflectionModelClient
from voxos.agent_runtime.tool_execution import ToolExecutionContext
from voxos.db.models import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReflectionService:
    improvement_service: AgentImprovementService
    model: ReflectionModelClient
    run_store: SqlModelAgentRunStore

    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        if run.session_id is None:
            message = "Reflection has no retained origin"
            raise ValueError(message)
        context = ToolExecutionContext(
            user_id=run.user_id,
            agent_id=run.agent_id,
            run_id=run.id,
            session_id=run.session_id,
            channel="text",
            source="durable_reflection",
        )
        async with self.improvement_service.session_factory() as db:
            job = (
                await db.exec(
                    select(AgentReflectionJobRecord).where(
                        col(AgentReflectionJobRecord.user_id) == run.user_id,
                        col(AgentReflectionJobRecord.run_id) == run.id,
                    )
                )
            ).one_or_none()
            if job is None:
                job = AgentReflectionJobRecord(
                    user_id=run.user_id, agent_id=run.agent_id, session_id=run.session_id, run_id=run.id
                )
                db.add(job)
            elif job.status in {"completed", "skipped"}:
                return AgentRunExecutionResult(result_json=job.result_json)
            job.status, job.started_at, job.completed_at = "running", utc_now(), None
            job.attempt_count += 1
            await db.commit()
            job_id = job.id
        applied = 0
        try:
            await self.improvement_service.history_service.require_access(context, "record_agent_improvement")
            end = utc_now()
            history = await self.improvement_service.history_service.search(
                context, HistoryQuery(start=end - timedelta(days=7), end=end, category="all", limit=50)
            )
            origin = await self.improvement_service.history_service.read(
                context,
                HistoryReadRequest(references=(EvidenceReference(kind=EvidenceKind.SESSION, key=run.session_id),)),
            )
            seen = {item.reference for item in origin.items}
            history = self.improvement_service.history_service.bound_result(
                history.model_copy(
                    update={
                        "items": (*origin.items, *(item for item in history.items if item.reference not in seen)),
                        "truncated": history.truncated or origin.truncated,
                    }
                )
            )
            candidates = await self.model.generate(user_id=run.user_id, history=history)
            await self.run_store.raise_if_cancel_requested(run_id=run.id, user_id=run.user_id)
            for candidate in candidates.candidates:
                await self.run_store.raise_if_cancel_requested(run_id=run.id, user_id=run.user_id)
                await self.improvement_service.submit(context, candidate, job_id=job_id)
                applied += 1
            await self.run_store.raise_if_cancel_requested(run_id=run.id, user_id=run.user_id)
        except LearningModelUnavailableError as exc:
            async with self.improvement_service.session_factory() as db:
                skipped = await db.get(AgentReflectionJobRecord, job_id)
                if skipped is not None and skipped.user_id == run.user_id:
                    skipped.status, skipped.completed_at, skipped.updated_at = "skipped", utc_now(), utc_now()
                    skipped.error_type = exc.reason
                    skipped.result_json = {"persisted_candidates": applied, "reason": exc.reason}
                    await db.commit()
                    return AgentRunExecutionResult(result_json=skipped.result_json)
            raise
        except (Exception, asyncio.CancelledError) as exc:
            # Each completed candidate is atomic; record explicit partial progress on this job.
            async with self.improvement_service.session_factory() as db:
                failed = await db.get(AgentReflectionJobRecord, job_id)
                if failed is not None and failed.user_id == run.user_id:
                    failed.status, failed.completed_at, failed.updated_at = "failed", utc_now(), utc_now()
                    failed.error_type = type(exc).__name__
                    failed.result_json = {"persisted_candidates": applied, "partial": applied > 0}
                    await db.commit()
            raise
        async with self.improvement_service.session_factory() as db:
            finished = await db.get(AgentReflectionJobRecord, job_id)
            if finished is None or finished.user_id != run.user_id:
                message = "Reflection job disappeared"
                raise ValueError(message)
            finished.status, finished.completed_at, finished.updated_at = "completed", utc_now(), utc_now()
            finished.result_json = {
                "persisted_candidates": applied,
                "model_revision": candidates.model_revision,
                "usage": candidates.usage,
                "coverage": history.coverage,
                "truncated": history.truncated,
                "evidence_start": history.start.isoformat() if history.start else None,
                "evidence_end": history.end.isoformat() if history.end else None,
            }
            await db.commit()
        return AgentRunExecutionResult(result_json=finished.result_json)

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import false, or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_memory.models import MemoryCreateRequest, MemorySource, MemoryStatus
from voxos.agent_memory.records import AgentMemoryRecord
from voxos.agent_memory.services import AgentMemoryService, normalized_memory_key
from voxos.agent_runtime.history import TenantHistoryService, safe_excerpt
from voxos.agent_runtime.improvement_policy import (
    LEARNING_POLICY_VERSION,
    ImprovementCandidate,
    LearningAssessment,
    LearningAssessor,
    MemoryLearning,
    SkillLearning,
    learning_decision,
)
from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.agent_runtime.records import (
    AgentImprovementEvidenceRecord,
    AgentLearningSettingsRecord,
    AgentReflectionJobRecord,
    AgentReflectionProposalRecord,
    AgentSessionRecord,
)
from voxos.agent_runtime.tool_execution import ToolExecutionContext
from voxos.agent_skills.models import SkillCreateRequest, SkillStatus
from voxos.agent_skills.records import AgentSkillRecord
from voxos.agent_skills.services import AgentSkillService, normalized_skill_slug
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory

logger = logging.getLogger(__name__)


class ImprovementView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    status: str
    candidate: ImprovementCandidate | None
    content_hash: str | None
    policy_version: str | None
    decision_mode: str | None
    reasons: tuple[str, ...]
    assessment: LearningAssessment | None
    before: dict[str, object]
    after: dict[str, object]
    created_at: datetime
    reviewed_at: datetime | None
    review_path: str = "/knowledge/improvements"
    duplicate: bool = False


class ImprovementReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    content_hash: str = Field(min_length=64, max_length=64)
    action: Literal["approve", "reject", "undo"]


class LearningSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    automatic: bool = False
    chosen_at: datetime | None = None


def numeric_usage(usage: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in usage.items()
        if key in {"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens"}
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


def digest(value: object) -> str:
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS)).hexdigest()


def target_snapshot(record: AgentMemoryRecord | AgentSkillRecord) -> dict[str, object]:
    fields = (
        {
            "kind",
            "status",
            "text",
            "sensitivity",
            "source",
            "normalized_key",
            "confidence",
            "supersedes_memory_id",
            "updated_at",
        }
        if isinstance(record, AgentMemoryRecord)
        else {
            "name",
            "slug",
            "category",
            "status",
            "description",
            "body_markdown",
            "preferred_capabilities",
            "updated_at",
        }
    )
    return cast(dict[str, object], record.model_dump(mode="json", include=fields))


async def _target(
    db: AsyncSession, proposal: AgentReflectionProposalRecord
) -> AgentMemoryRecord | AgentSkillRecord | None:
    if proposal.target_key is None:
        return None
    if proposal.proposal_type == "memory":
        statement = (
            select(AgentMemoryRecord)
            .where(col(AgentMemoryRecord.user_id) == proposal.user_id, col(AgentMemoryRecord.id) == proposal.target_key)
            .with_for_update()
        )
        return (await db.exec(statement)).one_or_none()
    statement_skill = (
        select(AgentSkillRecord)
        .where(col(AgentSkillRecord.owner_user_id) == proposal.user_id, col(AgentSkillRecord.id) == proposal.target_key)
        .with_for_update()
    )
    return (await db.exec(statement_skill)).one_or_none()


async def undo_in_session(db: AsyncSession, proposal: AgentReflectionProposalRecord) -> None:
    target = await _target(db, proposal)
    if target is None or target_snapshot(target) != proposal.target_after_json:
        message = "Knowledge changed since application; later edits have been preserved"
        raise ValueError(message)
    if proposal.target_before_json:
        for field, value in proposal.target_before_json.items():
            if field != "updated_at":
                setattr(target, field, value)
    else:
        target.status = "archived"
    target.updated_at = utc_now()
    proposal.status = "archived"
    proposal.reviewed_at = utc_now()
    db.add(target)
    db.add(proposal)


async def withdraw_session_learning(db: AsyncSession, *, user_id: str, session_id: str) -> None:
    """Called in the source deletion transaction after taking the account lock."""
    ids = select(AgentImprovementEvidenceRecord.proposal_id).where(
        col(AgentImprovementEvidenceRecord.user_id) == user_id,
        col(AgentImprovementEvidenceRecord.session_id) == session_id,
    )
    origin_jobs = select(AgentReflectionJobRecord.id).where(
        col(AgentReflectionJobRecord.user_id) == user_id, col(AgentReflectionJobRecord.session_id) == session_id
    )
    proposals = (
        await db.exec(
            select(AgentReflectionProposalRecord)
            .where(
                col(AgentReflectionProposalRecord.user_id) == user_id,
                or_(
                    col(AgentReflectionProposalRecord.id).in_(ids),
                    col(AgentReflectionProposalRecord.job_id).in_(origin_jobs),
                ),
            )
            .with_for_update()
        )
    ).all()
    for proposal in proposals:
        if proposal.decision_mode == "auto_policy" and proposal.status in {"applied", "archived"}:
            target = await _target(db, proposal)
            if target is not None and target.created_by_reflection_id == proposal.job_id:
                snapshot = target_snapshot(target)
                content = {key: value for key, value in snapshot.items() if key not in {"status", "updated_at"}}
                applied_content = {
                    key: value
                    for key, value in proposal.target_after_json.items()
                    if key not in {"status", "updated_at"}
                }
                if content == applied_content:
                    target.status = "archived"
                    target.updated_at = utc_now()
                    if isinstance(target, AgentMemoryRecord):
                        target.text = "[supporting evidence withdrawn]"
                        target.normalized_key = f"withdrawn:{target.id}"
                    elif isinstance(target, AgentSkillRecord):
                        target.name = "Withdrawn skill"
                        target.slug = f"withdrawn-{target.id}"
                        target.description = "Supporting evidence withdrawn"
                        target.body_markdown = "[supporting evidence withdrawn]"
                        target.preferred_capabilities = []
                    proposal.status = "archived"
                    db.add(target)
                else:
                    proposal.decision_reasons = ["source_withdrawn_later_owner_edit_preserved"]
        elif proposal.status == "proposed":
            proposal.status = "rejected"
        proposal.payload_json = {}
        proposal.reason = None
        proposal.assessment_json = {}
        proposal.target_before_json = {}
        proposal.target_after_json = {}
        proposal.withdrawn_at = utc_now()
        proposal.decision_reasons = [*proposal.decision_reasons, "source_withdrawn"]
        db.add(proposal)


@dataclass(frozen=True, slots=True)
class AgentImprovementService:
    session_factory: AsyncSessionFactory
    history_service: TenantHistoryService
    memory_service: AgentMemoryService
    skill_service: AgentSkillService
    assessor: LearningAssessor | None = None

    async def settings(self, *, user_id: str, automatic: bool | None = None) -> LearningSettings:
        async with self.session_factory() as db:
            if automatic is None:
                record = await db.get(AgentLearningSettingsRecord, user_id)
            else:
                record = await lock_learning_settings(db, user_id)
                record.automatic = automatic
                record.chosen_at = utc_now()
                await db.commit()
            return (
                LearningSettings(automatic=record.automatic, chosen_at=record.chosen_at)
                if record
                else LearningSettings()
            )

    async def submit(
        self, context: ToolExecutionContext, candidate: ImprovementCandidate, *, job_id: str | None = None
    ) -> ImprovementView:
        await self.history_service.require_access(context, "record_agent_improvement")
        async with self.session_factory() as db:
            evidence = await self.history_service.resolve(db, user_id=context.user_id, references=candidate.evidence)
            knowledge, knowledge_version, complete_knowledge = await self._knowledge(db, context.user_id)
        assessment = None
        if evidence.unavailable == 0 and candidate.evidence and self.assessor is not None and complete_knowledge:
            try:
                assessment = await asyncio.wait_for(
                    self.assessor.assess(
                        user_id=context.user_id,
                        candidate=candidate,
                        evidence=evidence.items,
                        active_knowledge=knowledge,
                    ),
                    timeout=45,
                )
            except Exception:
                # A failed/inconclusive model call routes the persisted candidate to owner review.
                logger.exception("Independent learning assessment unavailable")
                assessment = None
        if assessment is not None:
            assessment = assessment.model_copy(
                update={"reason": safe_excerpt(assessment.reason), "usage": numeric_usage(assessment.usage)}
            )
        identity = candidate.payload.model_dump(mode="json")
        if isinstance(candidate.payload, MemoryLearning):
            identity["text"] = " ".join(candidate.payload.text.split()).casefold()
            identity.pop("sensitivity", None)
        elif isinstance(candidate.payload, SkillLearning):
            identity["slug"] = normalized_skill_slug(candidate.payload.slug)
        fingerprint = digest(identity)
        async with self.session_factory() as db:
            settings = await lock_learning_settings(db, context.user_id)
            await self.history_service.validate_access_in_session(db, context, "record_agent_improvement")
            existing = (
                await db.exec(
                    select(AgentReflectionProposalRecord).where(
                        col(AgentReflectionProposalRecord.user_id) == context.user_id,
                        col(AgentReflectionProposalRecord.fingerprint) == fingerprint,
                    )
                )
            ).one_or_none()
            if existing:
                return self._view(existing, duplicate=True)
            # Serialize source withdrawal with application, then re-read all evidence.
            fresh = await self.history_service.resolve(db, user_id=context.user_id, references=candidate.evidence)
            if context.session_id is None:
                message = "Learning requires a retained owner conversation"
                raise PermissionError(message)
            origin = await db.get(AgentSessionRecord, context.session_id)
            if origin is None or origin.user_id != context.user_id or origin.deleted_at is not None:
                message = "Learning origin is unavailable"
                raise PermissionError(message)
            if job_id is None:
                job = AgentReflectionJobRecord(
                    session_id=origin.id,
                    user_id=context.user_id,
                    agent_id=context.agent_id,
                    status="completed",
                    attempt_count=1,
                    started_at=utc_now(),
                    completed_at=utc_now(),
                )
                db.add(job)
                await db.flush()
                job_id = job.id
            else:
                job_record = await db.get(AgentReflectionJobRecord, job_id)
                if job_record is None or job_record.user_id != context.user_id or job_record.session_id != origin.id:
                    message = "Learning job is unavailable"
                    raise PermissionError(message)
            proposal = AgentReflectionProposalRecord(
                job_id=job_id,
                user_id=context.user_id,
                agent_id=context.agent_id,
                proposal_type=candidate.payload.kind,
                payload_json=cast(dict[str, object], candidate.model_dump(mode="json")),
                reason=safe_excerpt(candidate.reason),
                confidence=candidate.confidence,
                fingerprint=fingerprint,
                content_hash=digest(candidate.model_dump(mode="json")),
                policy_version=LEARNING_POLICY_VERSION,
                assessment_json=cast(dict[str, object], assessment.model_dump(mode="json")) if assessment else {},
            )
            _knowledge, fresh_version, fresh_complete = await self._knowledge(db, context.user_id)
            if fresh_version != knowledge_version or not fresh_complete:
                assessment = None
                proposal.assessment_json = {}
                complete_knowledge = False
            if fresh.items != evidence.items:
                assessment = None
                proposal.assessment_json = {}
            conflict = await self._prepare_target(db, proposal, candidate)
            decision = learning_decision(
                candidate,
                evidence=fresh.items,
                assessment=assessment,
                automatic=settings.automatic,
                conflict=conflict,
                complete_knowledge=complete_knowledge,
            )
            proposal.status = "rejected" if fresh.unavailable else decision.status
            proposal.decision_reasons = ["evidence_unavailable"] if fresh.unavailable else list(decision.reasons)
            proposal.decision_mode = "auto_policy" if proposal.status == "applied" else "policy_review"
            if proposal.status == "rejected":
                proposal.payload_json = {}
                proposal.assessment_json = {}
                proposal.target_before_json = {}
            db.add(proposal)
            await db.flush()
            for item in fresh.items:
                db.add(
                    AgentImprovementEvidenceRecord(
                        user_id=context.user_id,
                        proposal_id=proposal.id,
                        session_id=item.conversation,
                        source_kind=item.reference.kind.value,
                        source_key=item.reference.key,
                    )
                )
            if proposal.status == "applied":
                await self._apply(db, proposal, candidate, human=False)
            await db.commit()
            await db.refresh(proposal)
            return self._view(proposal)

    async def _knowledge(self, db: AsyncSession, user_id: str) -> tuple[tuple[str, ...], str, bool]:
        memories = (
            await db.exec(
                select(AgentMemoryRecord)
                .where(col(AgentMemoryRecord.user_id) == user_id, col(AgentMemoryRecord.status) == "active")
                .order_by(col(AgentMemoryRecord.id))
                .limit(101)
            )
        ).all()
        skills = (
            await db.exec(
                select(AgentSkillRecord)
                .where(col(AgentSkillRecord.owner_user_id) == user_id, col(AgentSkillRecord.status) == "active")
                .order_by(col(AgentSkillRecord.id))
                .limit(101)
            )
        ).all()
        builtin = self.skill_service.builtin_catalog.list_skills()
        texts = (
            tuple(memory.text for memory in memories)
            + tuple(skill.name + "\n" + skill.description + "\n" + skill.body_markdown for skill in skills)
            + tuple(skill.name + "\n" + skill.description + "\n" + skill.body_markdown for skill in builtin)
        )
        knowledge = tuple(safe_excerpt(text, len(text)) for text in texts)
        version = digest(
            {
                "memories": [record.model_dump(mode="json") for record in memories],
                "skills": [record.model_dump(mode="json") for record in skills],
                "builtin": [record.model_dump(mode="json") for record in builtin],
            }
        )
        complete = len(memories) <= 100 and len(skills) <= 100 and texts == knowledge and sum(map(len, texts)) <= 24000
        return knowledge, version, complete

    async def _prepare_target(
        self, db: AsyncSession, proposal: AgentReflectionProposalRecord, candidate: ImprovementCandidate
    ) -> bool:
        payload = candidate.payload
        target: AgentMemoryRecord | AgentSkillRecord | None = None
        conflict = False
        if isinstance(payload, MemoryLearning):
            if payload.target:
                target = (
                    await db.exec(
                        select(AgentMemoryRecord)
                        .where(
                            col(AgentMemoryRecord.user_id) == proposal.user_id,
                            col(AgentMemoryRecord.id) == payload.target,
                        )
                        .with_for_update()
                    )
                ).one_or_none()
                if target is None:
                    message = "Memory target is unavailable"
                    raise ValueError(message)
            else:
                key = normalized_memory_key(kind=payload.memory_kind.value, text=payload.text)
                conflict = (
                    await db.exec(
                        select(AgentMemoryRecord.id)
                        .where(
                            col(AgentMemoryRecord.user_id) == proposal.user_id,
                            col(AgentMemoryRecord.normalized_key) == key,
                            col(AgentMemoryRecord.status) == "active",
                        )
                        .limit(1)
                    )
                ).first() is not None
        elif isinstance(payload, SkillLearning):
            slug = normalized_skill_slug(payload.slug)
            target = (
                await db.exec(
                    select(AgentSkillRecord)
                    .where(col(AgentSkillRecord.owner_user_id) == proposal.user_id, col(AgentSkillRecord.slug) == slug)
                    .with_for_update()
                )
            ).one_or_none()
            if payload.revise and target is None:
                message = "Skill target is unavailable"
                raise ValueError(message)
            conflict = target is not None or self.skill_service.builtin_catalog.get_skill(slug) is not None
            if not payload.revise:
                target = None
        if target:
            proposal.target_key = target.id
            proposal.target_before_json = target_snapshot(target)
            proposal.target_version = digest(proposal.target_before_json)
        return conflict or target is not None

    async def _apply(
        self, db: AsyncSession, proposal: AgentReflectionProposalRecord, candidate: ImprovementCandidate, *, human: bool
    ) -> None:
        target = await _target(db, proposal)
        if proposal.target_key and (target is None or digest(target_snapshot(target)) != proposal.target_version):
            message = "Knowledge changed since this proposal; submit a revised proposal"
            raise ValueError(message)
        payload = candidate.payload
        if isinstance(payload, MemoryLearning):
            if target is None:
                key = normalized_memory_key(kind=payload.memory_kind.value, text=payload.text)
                conflict = (
                    await db.exec(
                        select(AgentMemoryRecord.id)
                        .where(
                            col(AgentMemoryRecord.user_id) == proposal.user_id,
                            col(AgentMemoryRecord.normalized_key) == key,
                            col(AgentMemoryRecord.status) == "active",
                        )
                        .limit(1)
                    )
                ).first()
                if conflict:
                    message = "An active memory already uses this key; propose an explicit revision"
                    raise ValueError(message)
                target = self.memory_service.create_learning_in_session(
                    db,
                    user_id=proposal.user_id,
                    request=MemoryCreateRequest(
                        kind=payload.memory_kind,
                        text=payload.text,
                        sensitivity=payload.sensitivity,
                        source=MemorySource.REFLECTION,
                        confidence=candidate.confidence,
                        created_by_reflection_id=proposal.job_id,
                    ),
                )
            elif isinstance(target, AgentMemoryRecord) and human:
                key = normalized_memory_key(kind=payload.memory_kind.value, text=payload.text)
                duplicate = (
                    await db.exec(
                        select(AgentMemoryRecord.id)
                        .where(
                            col(AgentMemoryRecord.user_id) == proposal.user_id,
                            col(AgentMemoryRecord.normalized_key) == key,
                            col(AgentMemoryRecord.status) == "active",
                            col(AgentMemoryRecord.id) != target.id,
                        )
                        .limit(1)
                    )
                ).first()
                if duplicate is not None:
                    message = "An active memory already uses the revised key"
                    raise ValueError(message)
                target.text = payload.text
                target.kind = payload.memory_kind.value
                target.sensitivity = payload.sensitivity.value
                target.normalized_key = normalized_memory_key(kind=payload.memory_kind.value, text=payload.text)
                target.status = MemoryStatus.ACTIVE.value
                target.source = MemorySource.REFLECTION.value
        elif isinstance(payload, SkillLearning):
            if target is None:
                slug = normalized_skill_slug(payload.slug)
                if (
                    await db.exec(
                        select(AgentSkillRecord.id)
                        .where(
                            col(AgentSkillRecord.owner_user_id) == proposal.user_id, col(AgentSkillRecord.slug) == slug
                        )
                        .limit(1)
                    )
                ).first():
                    message = "A skill already uses this slug; propose an explicit revision"
                    raise ValueError(message)
                target = self.skill_service.create_learning_in_session(
                    db,
                    user_id=proposal.user_id,
                    request=SkillCreateRequest(
                        name=payload.name,
                        slug=slug,
                        category=payload.category,
                        description=payload.description,
                        body_markdown=payload.body_markdown,
                        created_by_reflection_id=proposal.job_id,
                    ),
                )
            elif isinstance(target, AgentSkillRecord) and human:
                target.name, target.description, target.body_markdown = (
                    payload.name,
                    payload.description,
                    payload.body_markdown,
                )
                target.category, target.status = payload.category.value, SkillStatus.ACTIVE.value
        else:
            message = "Diagnostic follow-ups remain in review"
            raise ValueError(message)
        target.updated_at = utc_now()
        db.add(target)
        await db.flush()
        # SQLite round-trips timestamps without timezone; normalize snapshots through a refresh.
        await db.refresh(target)
        proposal.target_key = target.id
        proposal.target_after_json = target_snapshot(target)
        proposal.status = "applied"
        db.add(proposal)

    async def list(
        self, *, user_id: str, limit: int = 50, before: datetime | None = None, before_id: str | None = None
    ) -> tuple[ImprovementView, ...]:
        statement = (
            select(AgentReflectionProposalRecord)
            .where(
                col(AgentReflectionProposalRecord.user_id) == user_id,
                col(AgentReflectionProposalRecord.policy_version).is_not(None),
            )
            .order_by(
                col(AgentReflectionProposalRecord.created_at).desc(), col(AgentReflectionProposalRecord.id).desc()
            )
            .limit(min(max(limit, 1), 100))
        )
        if before:
            statement = statement.where(
                or_(
                    col(AgentReflectionProposalRecord.created_at) < before,
                    (col(AgentReflectionProposalRecord.created_at) == before)
                    & (col(AgentReflectionProposalRecord.id) < before_id)
                    if before_id
                    else false(),
                )
            )
        async with self.session_factory() as db:
            return tuple(self._view(record) for record in (await db.exec(statement)).all())

    async def get(self, *, user_id: str, proposal_id: str) -> ImprovementView | None:
        async with self.session_factory() as db:
            record = await db.get(AgentReflectionProposalRecord, proposal_id)
            return (
                self._view(record)
                if record is not None and record.user_id == user_id and record.policy_version
                else None
            )

    async def review(self, *, user_id: str, proposal_id: str, request: ImprovementReview) -> ImprovementView:
        async with self.session_factory() as db:
            await lock_learning_settings(db, user_id)
            proposal = (
                await db.exec(
                    select(AgentReflectionProposalRecord)
                    .where(
                        col(AgentReflectionProposalRecord.user_id) == user_id,
                        col(AgentReflectionProposalRecord.id) == proposal_id,
                    )
                    .with_for_update()
                )
            ).one_or_none()
            if proposal is None or proposal.withdrawn_at is not None or proposal.content_hash != request.content_hash:
                message = "Proposal is unavailable or has changed"
                raise ValueError(message)
            if request.action == "undo":
                if proposal.status == "archived":
                    return self._view(proposal)
                if proposal.status != "applied":
                    message = "Only applied improvements can be undone"
                    raise ValueError(message)
                await undo_in_session(db, proposal)
            elif request.action == "reject":
                if proposal.status == "rejected":
                    return self._view(proposal)
                if proposal.status != "proposed":
                    message = "Only proposed improvements can be rejected"
                    raise ValueError(message)
                proposal.status = "rejected"
            else:
                if proposal.status == "applied":
                    return self._view(proposal)
                if proposal.status != "proposed":
                    message = "Only proposed improvements can be approved"
                    raise ValueError(message)
                candidate = ImprovementCandidate.model_validate(proposal.payload_json)
                evidence = await self.history_service.resolve(db, user_id=user_id, references=candidate.evidence)
                if evidence.unavailable:
                    message = "Supporting evidence is no longer available"
                    raise ValueError(message)
                if (
                    learning_decision(
                        candidate,
                        evidence=evidence.items,
                        assessment=None,
                        automatic=False,
                        conflict=True,
                        complete_knowledge=True,
                    ).status
                    == "rejected"
                ):
                    message = "Unsafe content cannot be approved"
                    raise ValueError(message)
                await self._apply(db, proposal, candidate, human=True)
            if request.action != "undo":
                proposal.decision_mode = "user_review"
            proposal.reviewer_user_id = user_id
            proposal.reviewed_at = utc_now()
            await db.commit()
            await db.refresh(proposal)
            return self._view(proposal)

    @staticmethod
    def _view(record: AgentReflectionProposalRecord, *, duplicate: bool = False) -> ImprovementView:
        return ImprovementView(
            id=record.id,
            status=record.status,
            candidate=ImprovementCandidate.model_validate(record.payload_json)
            if record.payload_json and record.policy_version
            else None,
            content_hash=record.content_hash,
            policy_version=record.policy_version,
            decision_mode=record.decision_mode,
            reasons=tuple(record.decision_reasons),
            assessment=LearningAssessment.model_validate(record.assessment_json) if record.assessment_json else None,
            before=record.target_before_json,
            after=record.target_after_json,
            created_at=record.created_at,
            reviewed_at=record.reviewed_at,
            duplicate=duplicate,
        )

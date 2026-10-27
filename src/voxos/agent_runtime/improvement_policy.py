import re
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_memory.models import MemoryKind, MemorySensitivity
from voxos.agent_memory.update_policy import contains_sensitive_term
from voxos.agent_runtime.history import safe_excerpt
from voxos.agent_runtime.history_models import EvidenceReference, HistoryItem
from voxos.agent_skills.models import SkillCategory

LEARNING_POLICY_VERSION = "tenant-learning-v1"
_UNSAFE = re.compile(
    r"(?i)(ignore (?:all |previous |developer |system )?instructions|bypass (?:rls|auth|permission|policy)|"
    r"disable (?:logging|validation|security)|(?:push|deploy|merge) (?:to )?main|"
    r"hide (?:errors|failures)|pretend .*success)"
)


class MemoryLearning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    kind: Literal["memory"] = "memory"
    memory_kind: MemoryKind
    text: str = Field(min_length=1, max_length=2048)
    sensitivity: MemorySensitivity = MemorySensitivity.LOW
    target: str | None = Field(default=None, max_length=36)


class SkillLearning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    kind: Literal["skill"] = "skill"
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    category: SkillCategory = SkillCategory.GENERAL
    description: str = Field(min_length=1, max_length=512)
    body_markdown: str = Field(min_length=1, max_length=8000)
    revise: bool = False


class DiagnosticLearning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    kind: Literal["diagnostic"] = "diagnostic"
    text: str = Field(min_length=1, max_length=2048)


LearningPayload = Annotated[MemoryLearning | SkillLearning | DiagnosticLearning, Field(discriminator="kind")]


class ImprovementCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    payload: LearningPayload
    evidence: tuple[EvidenceReference, ...] = Field(default=(), max_length=20)
    reason: str = Field(min_length=1, max_length=2048)
    confidence: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)


class LearningAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    supported: bool
    safe: bool
    applicable: bool
    no_conflict: bool
    explicit_owner_statement: bool = False
    successful_procedure: bool = False
    reason: str = Field(min_length=1, max_length=2048)
    model_revision: str | None = Field(default=None, max_length=256)
    usage: dict[str, object] = Field(default_factory=dict)


class LearningAssessor(Protocol):
    async def assess(
        self,
        *,
        user_id: str,
        candidate: ImprovementCandidate,
        evidence: tuple[HistoryItem, ...],
        active_knowledge: tuple[str, ...],
    ) -> LearningAssessment: ...


class LearningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    status: Literal["proposed", "applied", "rejected"]
    reasons: tuple[str, ...]


def learning_decision(
    candidate: ImprovementCandidate,
    *,
    evidence: tuple[HistoryItem, ...],
    assessment: LearningAssessment | None,
    automatic: bool,
    conflict: bool,
    complete_knowledge: bool,
) -> LearningDecision:
    text = candidate.payload.model_dump_json() + candidate.reason
    if _UNSAFE.search(text) or safe_excerpt(text, len(text)) != text:
        return LearningDecision(status="rejected", reasons=("unsafe_or_sensitive_content",))
    reasons: list[str] = []
    if contains_sensitive_term(candidate.payload.model_dump_json()):
        reasons.append("sensitive_content_requires_owner_review")
    if not automatic:
        reasons.append("account_review_only")
    if any(item.excerpt_truncated or item.context_withheld for item in evidence):
        reasons.append("incomplete_supporting_evidence")
    if not candidate.evidence:
        reasons.append("evidence_required")
    if candidate.confidence < 0.95:
        reasons.append("candidate_uncertain")
    if assessment is None:
        reasons.append("assessment_unavailable")
    elif not all(
        (
            assessment.confidence >= 0.95,
            assessment.supported,
            assessment.safe,
            assessment.applicable,
            assessment.no_conflict,
        )
    ):
        reasons.append("assessment_requires_review")
    if conflict or not complete_knowledge:
        reasons.append("knowledge_conflict_or_incomplete_context")
    observations = {
        item.conversation
        for item in evidence
        if not item.context_withheld
        and (item.owner_statement or (item.successful and item.reference.kind.value == "tool"))
    }
    payload = candidate.payload
    if isinstance(payload, MemoryLearning):
        if (
            payload.target
            or payload.sensitivity != MemorySensitivity.LOW
            or payload.memory_kind
            not in {MemoryKind.FACT, MemoryKind.PREFERENCE, MemoryKind.PROJECT, MemoryKind.TASK_CONTEXT}
        ):
            reasons.append("memory_requires_owner_review")
        explicit = (
            assessment is not None
            and assessment.explicit_owner_statement
            and any(item.owner_statement for item in evidence)
        )
        if not explicit and len(observations) < 2:
            reasons.append("independent_observations_required")
    elif isinstance(payload, SkillLearning):
        if payload.revise:
            reasons.append("revision_requires_owner_review")
        if (
            len(observations) < 2
            or not any(item.successful and item.reference.kind.value == "tool" for item in evidence)
            or assessment is None
            or not assessment.successful_procedure
        ):
            reasons.append("successful_corroborated_procedure_required")
    else:
        reasons.append("diagnostic_follow_up")
    return LearningDecision(
        status="proposed" if reasons else "applied", reasons=tuple(reasons) or ("eligible_additive_learning",)
    )

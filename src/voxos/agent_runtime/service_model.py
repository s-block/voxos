from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_runtime.history_models import HistoryResult
from voxos.agent_runtime.improvement_policy import ImprovementCandidate
from voxos.agent_runtime.models import JsonObject


class LearningModelUnavailableError(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ReflectionCandidates(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[ImprovementCandidate, ...] = Field(default=(), max_length=3)
    model_revision: str | None = None
    usage: JsonObject = Field(default_factory=dict)


class ReflectionModelClient(Protocol):
    async def generate(self, *, user_id: str, history: HistoryResult) -> ReflectionCandidates:
        """Return bounded typed candidates, with server-owned model usage provenance."""
        ...

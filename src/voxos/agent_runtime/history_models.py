from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voxos.db.models import utc_now


class EvidenceKind(StrEnum):
    SESSION = "session"
    TRANSCRIPT = "transcript"
    TOOL = "tool"
    RUN = "run"
    ERROR = "error"


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: EvidenceKind
    key: str = Field(min_length=1, max_length=36)


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start: datetime | None = None
    end: datetime | None = None
    channel: str | None = Field(default=None, max_length=64)
    attribution: str | None = Field(default=None, max_length=36)
    category: str = Field(default="errors", pattern="^(all|errors|transcripts|tools|sessions)$")
    tool_name: str | None = Field(default=None, max_length=256)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_window(self) -> "HistoryQuery":
        for value in (self.start, self.end):
            if value is not None and value.tzinfo is None:
                message = "History timestamps must include a timezone"
                raise ValueError(message)
        if self.start and self.end and not timedelta(0) < self.end - self.start <= timedelta(days=31):
            message = "History window must be positive and at most 31 days"
            raise ValueError(message)
        return self

    def window(self) -> tuple[datetime, datetime]:
        end = (self.end or utc_now()).astimezone(UTC)
        start = (self.start or end - timedelta(days=7)).astimezone(UTC)
        if not timedelta(0) < end - start <= timedelta(days=31):
            message = "History window must be positive and at most 31 days"
            raise ValueError(message)
        return start, end


class HistoryReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    references: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=20)
    include_context: bool = True


class HistoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reference: EvidenceReference
    conversation: str
    attribution: str | None
    tool_name: str | None = None
    channel: str
    created_at: datetime
    category: str
    summary: str
    owner_statement: bool = False
    successful: bool = False
    context_withheld: bool = False
    excerpt_truncated: bool = False


class HistoryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    items: tuple[HistoryItem, ...] = ()
    start: datetime | None = None
    end: datetime | None = None
    next_cursor: str | None = None
    truncated: bool = False
    unavailable: int = 0
    coverage: str = (
        "Retained canonical outcomes; mirrored runtime/tool streams are excluded. Counts describe this page only."
    )

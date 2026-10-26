from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    INSTRUCTION = "instruction"
    RELATIONSHIP = "relationship"
    PROJECT = "project"
    TASK_CONTEXT = "task_context"


class MemoryStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    ARCHIVED = "archived"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    HIDDEN = "hidden"


class MemorySensitivity(StrEnum):
    LOW = "low"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"


class MemorySource(StrEnum):
    USER_EXPLICIT = "user_explicit"
    ASSISTANT_INFERRED = "assistant_inferred"
    TOOL = "tool"
    REFLECTION = "reflection"
    ADMIN = "admin"


class AgentMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    kind: MemoryKind
    status: MemoryStatus
    sensitivity: MemorySensitivity
    source: MemorySource
    text: str = Field(min_length=1, max_length=2048)
    normalized_key: str = Field(min_length=1, max_length=256)
    confidence: float = Field(ge=0, le=1)
    evidence_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    evidence_transcript_id: str | None = Field(default=None, min_length=1, max_length=36)
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_by_reflection_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime
    updated_at: datetime


class MemoryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MemoryKind
    text: str = Field(min_length=1, max_length=2048)
    sensitivity: MemorySensitivity = MemorySensitivity.LOW
    source: MemorySource = MemorySource.USER_EXPLICIT
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    evidence_transcript_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_by_reflection_id: str | None = Field(default=None, min_length=1, max_length=36)
    confirmed: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class MemoryUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str = Field(min_length=1, max_length=36)
    text: str | None = Field(default=None, min_length=1, max_length=2048)
    status: MemoryStatus | None = None
    confirmed: bool = False

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class MemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    statuses: tuple[MemoryStatus, ...] = Field(default=(MemoryStatus.ACTIVE,))
    kinds: tuple[MemoryKind, ...] = Field(default_factory=tuple)
    limit: int = Field(default=12, ge=1, le=50)


class MemoryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: MemoryStatus
    requires_confirmation: bool
    reason: str = Field(min_length=1, max_length=512)

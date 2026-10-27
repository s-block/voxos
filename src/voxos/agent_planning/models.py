from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TaskSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    REFLECTION = "reflection"
    TOOL = "tool"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TaskStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    plan_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    position: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=512)
    status: TaskStatus
    evidence_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime
    updated_at: datetime


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_task_id: str | None = Field(default=None, min_length=1, max_length=36)
    objective: str = Field(min_length=1, max_length=1024)
    acceptance_criteria: tuple[str, ...] = Field(default_factory=tuple)
    source: TaskSource
    priority: TaskPriority
    status: TaskStatus
    allow_parallel_steps: bool = False
    steps: tuple[TaskStep, ...] = Field(default_factory=tuple)
    created_at: datetime
    updated_at: datetime


class TaskPlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str = Field(min_length=1, max_length=1024)
    steps: tuple[str, ...] = Field(min_length=1)
    acceptance_criteria: tuple[str, ...] = Field(default_factory=tuple)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_task_id: str | None = Field(default=None, min_length=1, max_length=36)
    source: TaskSource = TaskSource.USER
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.ACTIVE
    allow_parallel_steps: bool = False

    @field_validator("objective", mode="before")
    @classmethod
    def strip_objective(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("steps", "acceptance_criteria", mode="before")
    @classmethod
    def strip_text_tuple(cls, value: object) -> object:
        if isinstance(value, tuple | list):
            return tuple(item.strip() if isinstance(item, str) else item for item in value)
        return value


class TaskPlanUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str | None = Field(default=None, min_length=1, max_length=1024)
    acceptance_criteria: tuple[str, ...] | None = None
    priority: TaskPriority | None = None
    status: TaskStatus | None = None
    allow_parallel_steps: bool | None = None

    @field_validator("objective", mode="before")
    @classmethod
    def strip_objective(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def strip_acceptance_criteria(cls, value: object) -> object:
        if isinstance(value, tuple | list):
            return tuple(item.strip() if isinstance(item, str) else item for item in value)
        return value


class TaskStepUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step_id: str = Field(min_length=1, max_length=36)
    status: TaskStatus
    note: str | None = Field(default=None, min_length=1, max_length=2048)
    evidence_session_id: str | None = Field(default=None, min_length=1, max_length=36)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

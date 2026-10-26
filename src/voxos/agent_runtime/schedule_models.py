from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.run_models import AgentRunStatus


class ScheduleKind(StrEnum):
    ONE_SHOT = "one_shot"
    INTERVAL = "interval"
    CRON = "cron"


class ScheduleStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ScheduleCatchUpPolicy(StrEnum):
    SKIP_MISSED = "skip_missed"
    RUN_ONCE = "run_once"


class AgentSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    browser_account_id: str | None = Field(default=None, min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    kind: ScheduleKind
    status: ScheduleStatus
    timezone: str = Field(min_length=1, max_length=128)
    payload_json: JsonObject = Field(default_factory=dict)
    run_at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=256)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    last_run_status: AgentRunStatus | None = None
    max_concurrent_runs: int = Field(default=1, ge=1, le=10)
    catch_up_policy: ScheduleCatchUpPolicy
    misfire_grace_seconds: int = Field(default=300, ge=0, le=86400)
    retry_policy_json: JsonObject = Field(default_factory=dict)
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentScheduleCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=36)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    browser_account_id: str | None = Field(default=None, min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    kind: ScheduleKind
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    payload_json: JsonObject = Field(default_factory=dict)
    run_at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=256)
    max_concurrent_runs: int = Field(default=1, ge=1, le=10)
    catch_up_policy: ScheduleCatchUpPolicy = ScheduleCatchUpPolicy.SKIP_MISSED
    misfire_grace_seconds: int = Field(default=300, ge=0, le=86400)
    retry_policy_json: JsonObject = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("name", "timezone", "cron_expression", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> "AgentScheduleCreateRequest":
        if self.kind == ScheduleKind.ONE_SHOT and self.run_at is None:
            message = "run_at is required for one-shot schedules"
            raise ValueError(message)
        if self.kind == ScheduleKind.INTERVAL and self.interval_seconds is None:
            message = "interval_seconds is required for interval schedules"
            raise ValueError(message)
        if self.kind == ScheduleKind.CRON:
            message = "cron schedules are not implemented yet"
            raise ValueError(message)
        return self


class AgentScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    browser_account_id: str | None = Field(default=None, min_length=1, max_length=36)
    timezone: str | None = Field(default=None, min_length=1, max_length=128)
    payload_json: JsonObject | None = None
    run_at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=60)
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=10)
    catch_up_policy: ScheduleCatchUpPolicy | None = None
    misfire_grace_seconds: int | None = Field(default=None, ge=0, le=86400)
    retry_policy_json: JsonObject | None = None

    @field_validator("name", "timezone", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

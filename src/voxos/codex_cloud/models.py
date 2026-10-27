from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from voxos.agent_runtime.run_models import AgentRunDetail

CODEX_CLOUD_QUEUE = "codex-cloud"
CODEX_ENVIRONMENT_ID_PATTERN = r"^[A-Za-z0-9._/@:][A-Za-z0-9._/@:-]*$"
GIT_BRANCH_PATTERN = r"^[A-Za-z0-9._/][A-Za-z0-9._/-]*$"
CodexCloudLegacySummary = Annotated[str, Field(max_length=4096)]


class CodexAccountStatus(StrEnum):
    CONNECTED = "connected"
    REAUTHENTICATION_REQUIRED = "reauthentication_required"


class CodexAuthSessionStatus(StrEnum):
    QUEUED = "queued"
    WAITING_FOR_USER = "waiting_for_user"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class CodexCloudSubmissionState(StrEnum):
    PENDING = "pending"
    SUBMITTING = "submitting"
    SUBMITTED = "submitted"
    SUBMISSION_UNCERTAIN = "submission_uncertain"


class CodexCloudRemoteStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CodexAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connected: bool
    id: str | None = Field(default=None, min_length=1, max_length=36)
    status: CodexAccountStatus | None = None
    email: str | None = Field(default=None, max_length=320)
    plan_type: str | None = Field(default=None, max_length=64)
    environment_id: str | None = Field(default=None, max_length=256)
    environment_label: str | None = Field(default=None, max_length=128)
    authenticated_at: datetime | None = None
    updated_at: datetime | None = None


class CodexAuthSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    status: CodexAuthSessionStatus
    verification_url: HttpUrl | None = None
    user_code: str | None = Field(default=None, min_length=1, max_length=64)
    error_message: str | None = Field(default=None, max_length=1024)
    expires_at: datetime
    created_at: datetime
    updated_at: datetime


class CodexEnvironmentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=CODEX_ENVIRONMENT_ID_PATTERN,
    )
    environment_label: str | None = Field(default=None, max_length=128)


class CodexCloudTaskCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1, max_length=36)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=24000)
    branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=GIT_BRANCH_PATTERN,
    )
    attempts: int = Field(default=1, ge=1, le=4)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class CodexCloudTaskRunPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1, max_length=36)
    environment_id: str = Field(
        min_length=1,
        max_length=256,
        pattern=CODEX_ENVIRONMENT_ID_PATTERN,
    )
    prompt: str = Field(min_length=1, max_length=24000)
    branch: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        pattern=GIT_BRANCH_PATTERN,
    )
    attempts: int = Field(default=1, ge=1, le=4)


class CodexCloudTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    run: AgentRunDetail
    environment_id: str = Field(min_length=1, max_length=256)
    branch: str | None = Field(default=None, max_length=256)
    attempts: int = Field(ge=1, le=4)
    submission_state: CodexCloudSubmissionState
    remote_task_id: str | None = Field(default=None, max_length=128)
    remote_task_url: HttpUrl | None = None
    remote_status: CodexCloudRemoteStatus
    summary: str | None = Field(default=None, max_length=4096)
    created_at: datetime
    updated_at: datetime

    @field_validator("remote_task_url")
    @classmethod
    def validate_remote_task_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _validated_task_url(value)


class CodexCloudTaskListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tasks: tuple[CodexCloudTaskResponse, ...]


class CodexCloudChangeSummary(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    files_changed: int = Field(ge=0)
    lines_added: int = Field(ge=0)
    lines_removed: int = Field(ge=0)


class CodexCloudListTask(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    url: HttpUrl | None = None
    title: str | None = Field(default=None, max_length=4096)
    status: str = Field(min_length=1, max_length=64)
    updated_at: datetime | None = None
    environment_id: str | None = Field(default=None, max_length=256)
    environment_label: str | None = Field(default=None, max_length=256)
    summary: CodexCloudChangeSummary | CodexCloudLegacySummary | None = None

    @field_validator("url")
    @classmethod
    def validate_task_url(cls, value: HttpUrl | None) -> HttpUrl | None:
        return _validated_task_url(value)


class CodexCloudListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tasks: tuple[CodexCloudListTask, ...] = ()
    cursor: str | None = Field(default=None, max_length=1024)


def _validated_task_url(value: HttpUrl | None) -> HttpUrl | None:
    if value is not None and value.host not in {"chatgpt.com", "codex.openai.com"}:
        message = "Codex Cloud returned an unexpected task URL host"
        raise ValueError(message)
    return value

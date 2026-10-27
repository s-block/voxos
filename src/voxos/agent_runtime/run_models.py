from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_runtime.models import JsonObject

JsonValue: TypeAlias = object


class AgentRunCancelledError(RuntimeError):
    def __init__(self, run_id: str) -> None:
        super().__init__("Agent run cancellation was requested.")
        self.run_id = run_id


class AgentRunKind(StrEnum):
    REFLECTION = "reflection"
    TEXT_CHAT_TURN = "text_chat_turn"
    COMMUNICATION_REPLY = "communication_reply"
    SCHEDULED_AGENT_TURN = "scheduled_agent_turn"
    SUBAGENT = "subagent"
    MANUAL_AGENT_TASK = "manual_agent_task"
    WEBHOOK_AGENT_TASK = "webhook_agent_task"
    CODEX_CLOUD_TASK = "codex_cloud_task"


class AgentRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    LOST = "lost"


class AgentRunFailureCategory(StrEnum):
    PROVIDER = "provider"
    TOOL = "tool"
    CREDENTIALS = "credentials"
    CANCELLED = "cancelled"
    VALIDATION = "validation"
    SYSTEM = "system"
    UNKNOWN = "unknown"


class AgentRunCancellationState(StrEnum):
    NONE = "none"
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"


class AgentRunEventKind(StrEnum):
    RUN_ENQUEUED = "run_enqueued"
    RUN_STARTED = "run_started"
    RUN_HEARTBEAT = "run_heartbeat"
    RUN_RETRY_SCHEDULED = "run_retry_scheduled"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCEL_REQUESTED = "run_cancel_requested"
    RUN_CANCELLED = "run_cancelled"
    RUN_LOST = "run_lost"
    RUN_REDACTED = "run_redacted"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_COMPLETED = "message_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    USAGE = "usage"
    SUBAGENT_STARTED = "subagent_started"
    SUBAGENT_COMPLETED = "subagent_completed"
    SCHEDULE_ENQUEUED = "schedule_enqueued"
    PROGRESS = "progress"


class AgentRunDeliveryState(StrEnum):
    PENDING = "pending"
    STREAMED = "streamed"
    ACKNOWLEDGED = "acknowledged"


class AgentRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    kind: AgentRunKind
    status: AgentRunStatus
    queue: str = Field(min_length=1, max_length=128)
    priority: int
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    chat_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    root_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    schedule_id: str | None = Field(default=None, min_length=1, max_length=36)
    webhook_id: str | None = Field(default=None, min_length=1, max_length=36)
    browser_account_id: str | None = Field(default=None, min_length=1, max_length=36)
    task_plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    effective_tool_policy_id: str | None = Field(default=None, min_length=1, max_length=36)
    attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    cancel_reason: str | None = Field(default=None, min_length=1, max_length=512)
    error_type: str | None = Field(default=None, min_length=1, max_length=256)
    error_message: str | None = None
    child_run_count: int = Field(default=0, ge=0)


class AgentRunDetail(AgentRunSummary):
    payload_json: JsonObject = Field(default_factory=dict)
    result_json: JsonObject = Field(default_factory=dict)
    effective_tool_policy_json: JsonObject = Field(default_factory=dict)


class AgentRunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    kind: AgentRunEventKind
    payload_json: JsonObject = Field(default_factory=dict)
    delivery_state: AgentRunDeliveryState
    created_at: datetime


class AgentExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    root_run_id: str = Field(min_length=1, max_length=36)
    channel: str = Field(min_length=1, max_length=64)

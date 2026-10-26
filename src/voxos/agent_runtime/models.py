from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JsonObject: TypeAlias = dict[str, object]


class AgentRuntimeEventKind(StrEnum):
    RUN_STARTED = "run_started"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_COMPLETED = "message_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"
    CHAT_DELETED = "chat_deleted"
    RUN_ENDED = "run_ended"
    USER_TRANSCRIPT = "user_transcript"
    CONVERSATION_ITEM = "conversation_item"
    TOOL_AUTHORIZED = "tool_authorized"
    TOOL_DENIED = "tool_denied"
    TOOL_EXECUTED = "tool_executed"
    TOOL_EXECUTION = "tool_execution"
    USAGE = "usage"
    AGENT_STATE = "agent_state"
    USER_STATE = "user_state"
    SPEECH_CREATED = "speech_created"
    ERROR = "error"
    CLOSE = "close"


class AgentSessionStatus(StrEnum):
    STARTED = "started"
    ENDED = "ended"
    FAILED = "failed"


class AgentTranscriptRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
    UNKNOWN = "unknown"


class AgentTranscriptSource(StrEnum):
    CONVERSATION_ITEM = "conversation_item"
    USER_INPUT_TRANSCRIBED = "user_input_transcribed"
    TEXT_INPUT = "text_input"
    TEXT_OUTPUT = "text_output"
    SUBAGENT_TASK = "subagent_task"
    SUBAGENT_OUTPUT = "subagent_output"
    RUN_UPDATE = "run_update"
    TOOL_OUTPUT = "tool_output"


class ReflectionJobStatus(StrEnum):
    SKIPPED = "skipped"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ReflectionProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ReflectionProposalType(StrEnum):
    MEMORY = "memory"
    PERSONA_UPDATE = "persona_update"
    SKILL = "skill"
    TASK_FOLLOW_UP = "task_follow_up"
    NOOP = "noop"


class AgentSessionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    channel: str = Field(default="livekit", min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=256)
    voice: str | None = Field(default=None, min_length=1, max_length=256)
    status: AgentSessionStatus
    started_at: datetime
    ended_at: datetime | None = None
    deleted_at: datetime | None = None


class AgentTranscriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    role: AgentTranscriptRole
    text: str = Field(min_length=1)
    source: AgentTranscriptSource
    owner_authenticated: bool = False
    client_turn_id: str | None = Field(default=None, min_length=1, max_length=64)
    interrupted: bool = False
    transcript_confidence: float | None = Field(default=None, ge=0, le=1)
    language: str | None = Field(default=None, min_length=1, max_length=64)
    speaker_id: str | None = Field(default=None, min_length=1, max_length=256)
    is_final: bool = True
    livekit_item_id: str | None = Field(default=None, min_length=1, max_length=256)
    notification_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime


class AgentToolEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    arguments_json: JsonObject = Field(default_factory=dict)
    output_text: str | None = None
    is_error: bool = False
    group_id: str | None = Field(default=None, min_length=1, max_length=256)
    created_at: datetime


class AgentUsageSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    usage_type: str = Field(min_length=1, max_length=64)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    audio_duration_seconds: float | None = Field(default=None, ge=0)
    characters_count: int | None = Field(default=None, ge=0)
    total_requests: int | None = Field(default=None, ge=0)
    raw_usage_json: JsonObject = Field(default_factory=dict)
    created_at: datetime


class AgentStateEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    kind: AgentRuntimeEventKind
    payload_json: JsonObject = Field(default_factory=dict)
    created_at: datetime

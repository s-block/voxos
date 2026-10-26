from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from voxos.agent_runtime.models import (
    AgentRuntimeEventKind,
    AgentSessionStatus,
    ReflectionJobStatus,
    ReflectionProposalStatus,
)
from voxos.agent_runtime.run_models import (
    AgentRunDeliveryState,
    AgentRunEventKind,
    AgentRunKind,
    AgentRunStatus,
)
from voxos.agent_runtime.schedule_models import (
    ScheduleCatchUpPolicy,
    ScheduleKind,
    ScheduleStatus,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolGrantStatus, ToolRisk
from voxos.browser_accounts.records import BrowserAccountRecord
from voxos.db.models import new_record_id, utc_now

_BROWSER_ACCOUNTS_TABLE = BrowserAccountRecord.__tablename__


class AgentSessionRecord(SQLModel, table=True):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_agent_sessions_user_id"),
        Index("ix_agent_sessions_channel", "channel"),
        Index("ix_agent_sessions_user_outcome_at", "user_id", text("coalesce(ended_at, started_at)"), "id"),
        Index("ix_agent_sessions_user_status", "user_id", "status"),
        Index("ix_agent_sessions_user_started_at", "user_id", "started_at"),
        Index("ix_agent_sessions_user_channel_started_at", "user_id", "channel", "started_at"),
        Index("ix_agent_sessions_user_agent_status", "user_id", "agent_id", "status"),
        Index(
            "ix_agent_sessions_user_agent_started_at",
            "user_id",
            "agent_id",
            "started_at",
        ),
        Index(
            "ix_agent_sessions_user_agent_channel_started_at",
            "user_id",
            "agent_id",
            "channel",
            "started_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    provider: str = Field(index=True, min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    channel: str = Field(
        default="livekit",
        min_length=1,
        max_length=64,
        sa_column=Column(String(64), nullable=False),
    )
    owner_authenticated: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    title: str | None = Field(default=None, min_length=1, max_length=256)
    voice: str | None = Field(default=None, min_length=1, max_length=256)
    room_name: str | None = Field(default=None, min_length=1, max_length=256)
    room_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    effective_tool_policy_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    status: str = Field(
        default=AgentSessionStatus.STARTED.value,
        sa_column=Column(String(32), nullable=False),
    )
    prompt_version: str | None = Field(default=None, min_length=1, max_length=64)
    prompt_hash: str | None = Field(default=None, min_length=1, max_length=128)
    prompt_snapshot: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    started_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    ended_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    close_reason: str | None = Field(default=None, min_length=1, max_length=128)
    close_error_type: str | None = Field(default=None, min_length=1, max_length=256)
    close_error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentTranscriptRecord(SQLModel, table=True):
    __tablename__ = "agent_transcripts"
    __table_args__ = (
        Index("ix_agent_transcripts_session_created_at", "session_id", "created_at"),
        Index("ix_agent_transcripts_user_created_at", "user_id", "created_at", "id"),
        Index(
            "ix_agent_transcripts_user_agent_created_at",
            "user_id",
            "agent_id",
            "created_at",
        ),
        UniqueConstraint(
            "user_id",
            "session_id",
            "client_turn_id",
            name="uq_agent_transcripts_user_session_client_turn",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    role: str = Field(index=True, min_length=1, max_length=32)
    text: str = Field(sa_column=Column(Text, nullable=False))
    client_turn_id: str | None = Field(
        default=None,
        max_length=64,
        sa_column=Column(String(64), nullable=True),
    )
    source: str = Field(index=True, min_length=1, max_length=64)
    owner_authenticated: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    interrupted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    transcript_confidence: float | None = Field(
        default=None,
        sa_column=Column(Float, nullable=True),
    )
    language: str | None = Field(default=None, min_length=1, max_length=64)
    speaker_id: str | None = Field(default=None, min_length=1, max_length=256)
    is_final: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    livekit_item_id: str | None = Field(default=None, min_length=1, max_length=256)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentToolEventRecord(SQLModel, table=True):
    __tablename__ = "agent_tool_events"
    __table_args__ = (
        Index("ix_agent_tool_events_user_created_at", "user_id", "created_at", "id"),
        Index("ix_agent_tool_events_session_created_at", "session_id", "created_at"),
        Index("ix_agent_tool_events_user_name", "user_id", "name"),
        Index("ix_agent_tool_events_user_agent_name", "user_id", "agent_id", "name"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    call_id: str = Field(index=True, min_length=1, max_length=256)
    name: str = Field(index=True, min_length=1, max_length=256)
    arguments_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    output_text: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    is_error: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    group_id: str | None = Field(default=None, min_length=1, max_length=256)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentUsageRecord(SQLModel, table=True):
    __tablename__ = "agent_usage_events"
    __table_args__ = (
        Index("ix_agent_usage_events_session_created_at", "session_id", "created_at"),
        Index("ix_agent_usage_events_user_type", "user_id", "usage_type"),
        Index("ix_agent_usage_events_user_agent_type", "user_id", "agent_id", "usage_type"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    usage_type: str = Field(index=True, min_length=1, max_length=64)
    input_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    output_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    total_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    duration_seconds: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    audio_duration_seconds: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    characters_count: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    total_requests: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    raw_usage_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentRuntimeEventRecord(SQLModel, table=True):
    __tablename__ = "agent_runtime_events"
    __table_args__ = (
        Index("ix_agent_runtime_events_session_created_at", "session_id", "created_at"),
        Index("ix_agent_runtime_events_user_kind", "user_id", "kind", "created_at", "id"),
        Index("ix_agent_runtime_events_user_agent_kind", "user_id", "agent_id", "kind"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    kind: str = Field(
        default=AgentRuntimeEventKind.ERROR.value,
        sa_column=Column(String(64), nullable=False),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentRunRecord(SQLModel, table=True):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_agent_runs_user_idempotency"),
        Index("ix_agent_runs_active_queue", "status", "queue", "priority", "created_at"),
        Index("ix_agent_runs_due_attempts", "status", "next_attempt_at", "queue"),
        Index("ix_agent_runs_leases", "status", "lease_expires_at"),
        Index("ix_agent_runs_user_status", "user_id", "status", "created_at"),
        Index("ix_agent_runs_user_agent_status", "user_id", "agent_id", "status"),
        Index("ix_agent_runs_parent", "parent_run_id"),
        Index("ix_agent_runs_root", "root_run_id"),
        Index("ix_agent_runs_schedule", "schedule_id", "created_at"),
        Index("ix_agent_runs_webhook", "webhook_id", "created_at"),
        Index("ix_agent_runs_browser_account_id", "browser_account_id"),
        Index("ix_agent_runs_user_outcome_at", "user_id", text("coalesce(completed_at, created_at)"), "id"),
        Index("ix_agent_runs_task_plan", "task_plan_id", "created_at"),
        Index("ix_agent_runs_created_by_user", "created_by_user_id"),
        ForeignKeyConstraint(
            ["user_id", "browser_account_id"],
            [
                f"{_BROWSER_ACCOUNTS_TABLE}.user_id",
                f"{_BROWSER_ACCOUNTS_TABLE}.id",
            ],
            ondelete="RESTRICT",
            name="fk_agent_runs_user_browser_account",
        ),
        ForeignKeyConstraint(
            ["webhook_id"],
            ["agent_webhooks.id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_webhook",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    kind: str = Field(
        default=AgentRunKind.TEXT_CHAT_TURN.value,
        sa_column=Column(String(64), nullable=False),
    )
    status: str = Field(
        default=AgentRunStatus.QUEUED.value,
        sa_column=Column(String(32), nullable=False),
    )
    queue: str = Field(default="agent-default", min_length=1, max_length=128)
    priority: int = Field(default=0)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    result_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    effective_tool_policy_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    session_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    chat_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_session_id: str | None = Field(default=None, min_length=1, max_length=36)
    root_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    schedule_id: str | None = Field(default=None, min_length=1, max_length=36)
    webhook_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    browser_account_id: str | None = Field(default=None, max_length=36)
    browser_account_execution_attempt: int | None = Field(default=None, ge=0)
    task_plan_id: str | None = Field(default=None, min_length=1, max_length=36)
    attempt: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    next_attempt_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    heartbeat_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    cancel_requested_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    cancel_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    error_type: str | None = Field(default=None, min_length=1, max_length=256)
    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_by_user_id: str = Field(min_length=1, max_length=256)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    redacted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentRunEventRecord(SQLModel, table=True):
    __tablename__ = "agent_run_events"
    __table_args__ = (
        Index("ix_agent_run_events_run_created", "run_id", "created_at"),
        Index("ix_agent_run_events_user_created", "user_id", "created_at"),
        Index("ix_agent_run_events_user_agent_created", "user_id", "agent_id", "created_at"),
        Index("ix_agent_run_events_user_kind_created", "user_id", "kind", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    run_id: str = Field(
        foreign_key="agent_runs.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    kind: str = Field(
        default=AgentRunEventKind.PROGRESS.value,
        sa_column=Column(String(64), nullable=False),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    delivery_state: str = Field(
        default=AgentRunDeliveryState.PENDING.value,
        sa_column=Column(String(32), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentWebhookRecord(SQLModel, table=True):
    __tablename__ = "agent_webhooks"
    __table_args__ = (
        Index("ix_agent_webhooks_user_agent", "user_id", "agent_id"),
        Index("ix_agent_webhooks_user_enabled", "user_id", "enabled"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    instruction: str = Field(sa_column=Column(Text, nullable=False))
    token_hash: str = Field(min_length=64, max_length=64)
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentScheduleRecord(SQLModel, table=True):
    __tablename__ = "agent_schedules"
    __table_args__ = (
        Index("ix_agent_schedules_user_status_next", "user_id", "status", "next_run_at"),
        Index("ix_agent_schedules_due", "enabled", "status", "next_run_at"),
        Index("ix_agent_schedules_user_agent_status", "user_id", "agent_id", "status"),
        Index("ix_agent_schedules_user_chat", "user_id", "chat_id"),
        Index("ix_agent_schedules_browser_account_id", "browser_account_id"),
        ForeignKeyConstraint(
            ["chat_id"],
            ["agent_sessions.id"],
            ondelete="RESTRICT",
            name="fk_agent_schedules_chat",
        ),
        ForeignKeyConstraint(
            ["user_id", "browser_account_id"],
            [
                f"{_BROWSER_ACCOUNTS_TABLE}.user_id",
                f"{_BROWSER_ACCOUNTS_TABLE}.id",
            ],
            ondelete="RESTRICT",
            name="fk_agent_schedules_user_browser_account",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    chat_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
    )
    browser_account_id: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    kind: str = Field(
        default=ScheduleKind.ONE_SHOT.value,
        sa_column=Column(String(32), nullable=False),
    )
    status: str = Field(
        default=ScheduleStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    run_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    interval_seconds: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    cron_expression: str | None = Field(default=None, min_length=1, max_length=256)
    next_run_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_run_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    last_run_status: str | None = Field(default=None, min_length=1, max_length=32)
    max_concurrent_runs: int = Field(default=1, ge=1)
    catch_up_policy: str = Field(
        default=ScheduleCatchUpPolicy.SKIP_MISSED.value,
        sa_column=Column(String(32), nullable=False),
    )
    misfire_grace_seconds: int = Field(default=300, ge=0)
    retry_policy_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False))
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentReflectionJobRecord(SQLModel, table=True):
    __tablename__ = "agent_reflection_jobs"
    __table_args__ = (
        Index("ix_agent_reflection_jobs_session", "session_id"),
        UniqueConstraint("user_id", "run_id", name="uq_agent_reflection_jobs_run"),
        Index("ix_agent_reflection_jobs_user_status", "user_id", "status"),
        Index("ix_agent_reflection_jobs_user_agent_status", "user_id", "agent_id", "status"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    session_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    status: str = Field(
        default=ReflectionJobStatus.RUNNING.value,
        sa_column=Column(String(32), nullable=False),
    )
    run_id: str | None = Field(default=None, max_length=36)
    result_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    attempt_count: int = Field(default=0, ge=0)
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_type: str | None = Field(default=None, min_length=1, max_length=256)
    error_message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentReflectionProposalRecord(SQLModel, table=True):
    __tablename__ = "agent_reflection_proposals"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_agent_reflection_proposals_user_id"),
        Index("uq_agent_reflection_proposals_fingerprint", "user_id", "fingerprint", unique=True),
        Index("ix_agent_reflection_proposals_job", "job_id"),
        Index("ix_agent_reflection_proposals_user_status", "user_id", "status"),
        Index("ix_agent_reflection_proposals_user_type", "user_id", "proposal_type"),
        Index(
            "ix_agent_reflection_proposals_user_agent_status",
            "user_id",
            "agent_id",
            "status",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    job_id: str = Field(
        foreign_key="agent_reflection_jobs.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    proposal_type: str = Field(index=True, min_length=1, max_length=64)
    status: str = Field(
        default=ReflectionProposalStatus.PROPOSED.value,
        sa_column=Column(String(32), nullable=False),
    )
    payload_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    confidence: float | None = Field(default=None, sa_column=Column(Float, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    fingerprint: str | None = Field(default=None, max_length=64)
    policy_version: str | None = Field(default=None, max_length=64)
    decision_mode: str | None = Field(default=None, max_length=32)
    decision_reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    assessment_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    content_hash: str | None = Field(default=None, max_length=64)
    target_key: str | None = Field(default=None, max_length=128)
    target_before_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    target_after_json: dict[str, object] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    target_version: str | None = Field(default=None, max_length=64)
    reviewer_user_id: str | None = Field(default=None, max_length=256)
    withdrawn_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class AgentToolGrantRecord(SQLModel, table=True):
    __tablename__ = "agent_tool_grants"
    __table_args__ = (
        Index(
            "uq_agent_tool_grants_internal_tool",
            "user_id",
            "agent_id",
            "tool_source",
            "tool_name",
            unique=True,
            postgresql_where=text(
                "tool_source = 'internal' AND mcp_server_id IS NULL AND integration_id IS NULL",
            ),
            sqlite_where=text(
                "tool_source = 'internal' AND mcp_server_id IS NULL AND integration_id IS NULL",
            ),
        ),
        Index(
            "uq_agent_tool_grants_mcp_tool",
            "user_id",
            "agent_id",
            "tool_source",
            "mcp_server_id",
            "tool_name",
            unique=True,
            postgresql_where=text(
                "tool_source = 'mcp_server' AND mcp_server_id IS NOT NULL AND integration_id IS NULL",
            ),
            sqlite_where=text(
                "tool_source = 'mcp_server' AND mcp_server_id IS NOT NULL AND integration_id IS NULL",
            ),
        ),
        Index(
            "uq_agent_tool_grants_integration_tool",
            "user_id",
            "agent_id",
            "tool_source",
            "integration_id",
            "tool_name",
            unique=True,
            postgresql_where=text(
                "tool_source = 'integration' AND mcp_server_id IS NULL AND integration_id IS NOT NULL",
            ),
            sqlite_where=text(
                "tool_source = 'integration' AND mcp_server_id IS NULL AND integration_id IS NOT NULL",
            ),
        ),
        Index("ix_agent_tool_grants_user_agent", "user_id", "agent_id"),
        Index(
            "ix_agent_tool_grants_user_agent_source",
            "user_id",
            "agent_id",
            "tool_source",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    tool_name: str = Field(index=True, min_length=1, max_length=256)
    tool_source: str = Field(
        default=ToolGrantScope.INTERNAL.value,
        sa_column=Column(String(32), nullable=False),
    )
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    status: str = Field(
        default=ToolGrantStatus.DENIED.value,
        sa_column=Column(String(32), nullable=False),
    )
    risk: str = Field(
        default=ToolRisk.MEDIUM.value,
        sa_column=Column(String(32), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentToolRoutePreferenceRecord(SQLModel, table=True):
    __tablename__ = "agent_tool_route_preferences"
    __table_args__ = (
        CheckConstraint(
            "mode IN ('automatic', 'pinned')",
            name="ck_agent_tool_route_preferences_mode",
        ),
        CheckConstraint(
            "(mode = 'automatic' AND pinned_tool_name IS NULL AND "
            "pinned_tool_source IS NULL AND pinned_mcp_server_id IS NULL AND "
            "pinned_integration_id IS NULL) OR "
            "(mode = 'pinned' AND pinned_tool_name IS NOT NULL AND "
            "pinned_tool_source IS NOT NULL)",
            name="ck_agent_tool_route_preferences_pinned_tool",
        ),
        UniqueConstraint(
            "user_id",
            "agent_id",
            "action",
            name="uq_agent_tool_route_preferences_user_agent_action",
        ),
        Index(
            "ix_agent_tool_route_preferences_user_agent",
            "user_id",
            "agent_id",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    action: str = Field(index=True, min_length=1, max_length=128)
    mode: str = Field(min_length=1, max_length=32)
    pinned_tool_name: str | None = Field(default=None, min_length=1, max_length=256)
    pinned_tool_source: str | None = Field(default=None, min_length=1, max_length=32)
    pinned_mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    pinned_integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentToolAuditEventRecord(SQLModel, table=True):
    __tablename__ = "agent_tool_audit_events"
    __table_args__ = (
        Index("ix_agent_tool_audit_user_agent_created", "user_id", "agent_id", "created_at"),
        Index("ix_agent_tool_audit_user_tool", "user_id", "tool_name"),
        Index("ix_agent_tool_audit_run_created", "run_id", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    session_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, index=True, min_length=1, max_length=36)
    tool_name: str = Field(index=True, min_length=1, max_length=256)
    tool_source: str = Field(
        default=ToolGrantScope.INTERNAL.value,
        sa_column=Column(String(32), nullable=False),
    )
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    decision: str = Field(min_length=1, max_length=32)
    reason: str = Field(min_length=1, max_length=512)
    metadata_json: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentLearningSettingsRecord(SQLModel, table=True):
    __tablename__ = "agent_learning_settings"
    user_id: str = Field(primary_key=True, max_length=256)
    automatic: bool = Field(default=False, sa_column=Column(Boolean, nullable=False))
    chosen_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    analysis_window: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    analysis_count: int = Field(default=0)


class AgentImprovementEvidenceRecord(SQLModel, table=True):
    __tablename__ = "agent_improvement_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "proposal_id"],
            ["agent_reflection_proposals.user_id", "agent_reflection_proposals.id"],
            name="fk_improvement_evidence_owned_proposal",
        ),
        ForeignKeyConstraint(
            ["user_id", "session_id"],
            ["agent_sessions.user_id", "agent_sessions.id"],
            name="fk_improvement_evidence_owned_session",
        ),
        Index("ix_agent_improvement_evidence_source", "user_id", "session_id"),
        UniqueConstraint(
            "user_id", "proposal_id", "source_kind", "source_key", name="uq_agent_improvement_evidence_source"
        ),
    )
    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, max_length=256)
    proposal_id: str = Field(index=True, max_length=36)
    session_id: str = Field(max_length=36)
    source_kind: str = Field(max_length=32)
    source_key: str = Field(max_length=36)

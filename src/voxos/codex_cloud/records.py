from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from voxos.codex_cloud.models import (
    CodexAccountStatus,
    CodexAuthSessionStatus,
    CodexCloudRemoteStatus,
    CodexCloudSubmissionState,
)
from voxos.db.models import new_record_id, utc_now


class CodexAccountRecord(SQLModel, table=True):
    __tablename__ = "codex_accounts"
    __table_args__ = (
        CheckConstraint(
            "operation_generation >= 0",
            name="ck_codex_accounts_operation_generation",
        ),
        UniqueConstraint("user_id", name="uq_codex_accounts_user_id"),
        UniqueConstraint("user_id", "id", name="uq_codex_accounts_user_id_id"),
        Index("ix_codex_accounts_user_status", "user_id", "status"),
        Index("ix_codex_accounts_operation_lease", "operation_lease_expires_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    status: str = Field(default=CodexAccountStatus.CONNECTED.value, max_length=32)
    email: str | None = Field(default=None, max_length=320)
    plan_type: str | None = Field(default=None, max_length=64)
    environment_id: str | None = Field(default=None, max_length=256)
    environment_label: str | None = Field(default=None, max_length=128)
    operation_lease_owner: str | None = Field(default=None, max_length=128)
    operation_lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    operation_generation: int = Field(default=0, ge=0, sa_column=Column(Integer, nullable=False))
    authenticated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CodexAuthSessionRecord(SQLModel, table=True):
    __tablename__ = "codex_auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "account_id"],
            ["codex_accounts.user_id", "codex_accounts.id"],
            ondelete="CASCADE",
        ),
        Index("ix_codex_auth_sessions_user_status", "user_id", "status"),
        Index("ix_codex_auth_sessions_claim", "status", "lease_expires_at", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    account_id: str | None = Field(default=None, max_length=36)
    status: str = Field(default=CodexAuthSessionStatus.QUEUED.value, max_length=32)
    login_id: str | None = Field(default=None, max_length=128)
    verification_url: str | None = Field(default=None, max_length=2048)
    user_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    lease_owner: str | None = Field(default=None, max_length=128)
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class CodexCloudTaskRecord(SQLModel, table=True):
    __tablename__ = "codex_cloud_tasks"
    __table_args__ = (
        CheckConstraint(
            "attempts BETWEEN 1 AND 4",
            name="ck_codex_cloud_tasks_attempts",
        ),
        UniqueConstraint("agent_run_id", name="uq_codex_cloud_tasks_agent_run_id"),
        ForeignKeyConstraint(
            ["user_id", "account_id"],
            ["codex_accounts.user_id", "codex_accounts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        Index("ix_codex_cloud_tasks_user_created", "user_id", "created_at"),
        Index("ix_codex_cloud_tasks_remote", "user_id", "remote_task_id"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    account_id: str = Field(min_length=1, max_length=36)
    agent_run_id: str = Field(index=True, min_length=1, max_length=36)
    environment_id: str = Field(min_length=1, max_length=256)
    branch: str | None = Field(default=None, max_length=256)
    attempts: int = Field(default=1, ge=1, le=4, sa_column=Column(Integer, nullable=False))
    submission_state: str = Field(default=CodexCloudSubmissionState.PENDING.value, max_length=32)
    remote_task_id: str | None = Field(default=None, max_length=128)
    remote_task_url: str | None = Field(default=None, max_length=2048)
    remote_status: str = Field(default=CodexCloudRemoteStatus.PENDING.value, max_length=32)
    summary: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    submitted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    remote_updated_at: datetime | None = Field(
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

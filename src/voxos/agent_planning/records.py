from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Index, String, Text
from sqlmodel import Field, SQLModel

from voxos.agent_planning.models import TaskPriority, TaskSource, TaskStatus
from voxos.db.models import new_record_id, utc_now


class AgentTaskPlanRecord(SQLModel, table=True):
    __tablename__ = "agent_task_plans"
    __table_args__ = (
        Index("ix_agent_task_plans_user_status", "user_id", "status"),
        Index("ix_agent_task_plans_user_updated_at", "user_id", "updated_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    session_id: str | None = Field(
        default=None,
        foreign_key="agent_sessions.id",
        min_length=1,
        max_length=36,
    )
    parent_task_id: str | None = Field(default=None, min_length=1, max_length=36)
    objective: str = Field(sa_column=Column(Text, nullable=False))
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    source: str = Field(default=TaskSource.USER.value, min_length=1, max_length=64)
    priority: str = Field(default=TaskPriority.NORMAL.value, min_length=1, max_length=32)
    status: str = Field(
        default=TaskStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    allow_parallel_steps: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentTaskStepRecord(SQLModel, table=True):
    __tablename__ = "agent_task_steps"
    __table_args__ = (
        Index("ix_agent_task_steps_plan_position", "plan_id", "position"),
        Index("ix_agent_task_steps_user_status", "user_id", "status"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    plan_id: str = Field(
        foreign_key="agent_task_plans.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    position: int = Field(ge=1)
    title: str = Field(sa_column=Column(Text, nullable=False))
    status: str = Field(
        default=TaskStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    evidence_session_id: str | None = Field(
        default=None,
        foreign_key="agent_sessions.id",
        min_length=1,
        max_length=36,
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentTaskEventRecord(SQLModel, table=True):
    __tablename__ = "agent_task_events"
    __table_args__ = (
        Index("ix_agent_task_events_plan_created_at", "plan_id", "created_at"),
        Index("ix_agent_task_events_user_created_at", "user_id", "created_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    plan_id: str = Field(
        foreign_key="agent_task_plans.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    step_id: str | None = Field(
        default=None,
        foreign_key="agent_task_steps.id",
        min_length=1,
        max_length=36,
    )
    user_id: str = Field(index=True, min_length=1, max_length=256)
    event_type: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

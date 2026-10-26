from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from voxos.agent_identity.models import (
    DEFAULT_AGENT_DISPLAY_NAME,
    DEFAULT_AGENT_SLUG,
    AgentStatus,
)
from voxos.db.models import new_record_id, utc_now


class UserAgentRecord(SQLModel, table=True):
    __tablename__ = "user_agents"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "slug", name="uq_user_agents_owner_slug"),
        Index("ix_user_agents_owner_status", "owner_user_id", "status"),
        Index("ix_user_agents_owner_default", "owner_user_id", "is_default"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    owner_user_id: str = Field(index=True, min_length=1, max_length=256)
    slug: str = Field(
        default=DEFAULT_AGENT_SLUG,
        min_length=1,
        max_length=128,
    )
    display_name: str = Field(
        default=DEFAULT_AGENT_DISPLAY_NAME,
        min_length=1,
        max_length=256,
    )
    description: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    status: str = Field(
        default=AgentStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    is_default: bool = Field(
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

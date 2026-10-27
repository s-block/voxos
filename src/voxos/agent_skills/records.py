from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from voxos.agent_skills.models import SkillCategory, SkillScope, SkillStatus
from voxos.db.models import new_record_id, utc_now


class AgentSkillRecord(SQLModel, table=True):
    __tablename__ = "agent_skills"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "owner_user_id",
            "slug",
            name="uq_agent_skills_scope_owner_slug",
        ),
        Index("ix_agent_skills_owner_status", "owner_user_id", "status"),
        Index("ix_agent_skills_scope_status", "scope", "status"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    scope: str = Field(default=SkillScope.USER.value, min_length=1, max_length=32)
    owner_user_id: str | None = Field(default=None, index=True, min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(index=True, min_length=1, max_length=128)
    category: str = Field(default=SkillCategory.GENERAL.value, min_length=1, max_length=64)
    status: str = Field(
        default=SkillStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    description: str = Field(sa_column=Column(Text, nullable=False))
    body_markdown: str = Field(sa_column=Column(Text, nullable=False))
    preferred_capabilities: list[dict[str, object]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    created_by_reflection_id: str | None = Field(
        default=None,
        foreign_key="agent_reflection_jobs.id",
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

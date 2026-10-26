from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, String, Text
from sqlmodel import Field, SQLModel

from voxos.agent_memory.models import (
    MemoryKind,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
)
from voxos.db.models import new_record_id, utc_now


class AgentMemoryRecord(SQLModel, table=True):
    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_user_status_kind", "user_id", "status", "kind"),
        Index("ix_agent_memories_user_updated_at", "user_id", "updated_at"),
        Index("ix_agent_memories_user_normalized_key", "user_id", "normalized_key"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    kind: str = Field(default=MemoryKind.PREFERENCE.value, min_length=1, max_length=64)
    status: str = Field(
        default=MemoryStatus.PROPOSED.value,
        sa_column=Column(String(32), nullable=False),
    )
    sensitivity: str = Field(
        default=MemorySensitivity.LOW.value,
        sa_column=Column(String(32), nullable=False),
    )
    source: str = Field(
        default=MemorySource.USER_EXPLICIT.value,
        sa_column=Column(String(64), nullable=False),
    )
    text: str = Field(sa_column=Column(Text, nullable=False))
    normalized_key: str = Field(index=True, min_length=1, max_length=256)
    confidence: float = Field(default=1.0, sa_column=Column(Float, nullable=False))
    evidence_session_id: str | None = Field(
        default=None,
        foreign_key="agent_sessions.id",
        min_length=1,
        max_length=36,
    )
    evidence_transcript_id: str | None = Field(
        default=None,
        foreign_key="agent_transcripts.id",
        min_length=1,
        max_length=36,
    )
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=36)
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

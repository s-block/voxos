from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now


class MessageAttachmentRecord(SQLModel, table=True):
    __tablename__ = "message_attachments"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'ready', 'attached', 'deleted', 'failed')",
            name="ck_message_attachments_state",
        ),
        CheckConstraint(
            "client_source IN ('web', 'flutter', 'agent')",
            name="ck_message_attachments_client_source",
        ),
        CheckConstraint("byte_size > 0", name="ck_message_attachments_byte_size"),
        CheckConstraint("width > 0", name="ck_message_attachments_width"),
        CheckConstraint("height > 0", name="ck_message_attachments_height"),
        CheckConstraint(
            "position IS NULL OR position >= 0",
            name="ck_message_attachments_position",
        ),
        UniqueConstraint(
            "transcript_id",
            "position",
            name="uq_message_attachments_transcript_position",
        ),
        Index(
            "ix_message_attachments_user_chat_created_at",
            "user_id",
            "chat_id",
            "created_at",
        ),
        Index(
            "ix_message_attachments_user_transcript",
            "user_id",
            "transcript_id",
        ),
        Index(
            "ix_message_attachments_user_state_created_at",
            "user_id",
            "state",
            "created_at",
        ),
        Index(
            "ix_message_attachments_user_draft",
            "user_id",
            "chat_id",
            "client_draft_id",
        ),
        Index(
            "ix_message_attachments_deletion_queue",
            "state",
            "storage_deleted_at",
            "deletion_attempts",
            "deleted_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    chat_id: str = Field(
        foreign_key="agent_sessions.id",
        index=True,
        min_length=1,
        max_length=36,
    )
    transcript_id: str | None = Field(
        default=None,
        foreign_key="agent_transcripts.id",
        index=True,
        max_length=36,
    )
    client_draft_id: str = Field(min_length=1, max_length=64)
    client_source: str = Field(min_length=1, max_length=16)
    position: int | None = Field(
        default=None,
        sa_column=Column(Integer, nullable=True),
    )
    state: str = Field(index=True, min_length=1, max_length=16)
    display_name: str = Field(min_length=1, max_length=128)
    mime_type: str = Field(min_length=1, max_length=32)
    byte_size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(min_length=64, max_length=64)
    width: int = Field(sa_column=Column(Integer, nullable=False))
    height: int = Field(sa_column=Column(Integer, nullable=False))
    object_key: str = Field(
        min_length=1,
        max_length=1024,
        sa_column=Column(Text, nullable=False),
    )
    thumbnail_key: str = Field(
        min_length=1,
        max_length=1024,
        sa_column=Column(Text, nullable=False),
    )
    deletion_attempts: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False),
    )
    deletion_error: str | None = Field(
        default=None,
        sa_column=Column(String(256), nullable=True),
    )
    storage_deleted_at: datetime | None = Field(
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
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

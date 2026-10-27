from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now


class PushPlatform(StrEnum):
    ANDROID = "android"
    IOS = "ios"


class PushOutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY = "retry"
    DELIVERED = "delivered"
    FAILED = "failed"


class PushInstallationRecord(SQLModel, table=True):
    __tablename__ = "push_installations"
    __table_args__ = (
        CheckConstraint(
            "platform IN ('android', 'ios')",
            name="ck_push_installations_platform",
        ),
        UniqueConstraint(
            "installation_id",
            name="uq_push_installations_installation_id",
        ),
        Index(
            "ix_push_installations_user_active",
            "user_id",
            "active",
            "last_seen_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    installation_id: str = Field(index=True, min_length=1, max_length=256)
    registration_token: str = Field(sa_column=Column(Text, nullable=False))
    platform: str = Field(sa_column=Column(String(16), nullable=False))
    active: bool = Field(default=True, nullable=False)
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_seen_at: datetime = Field(
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


class PushNotificationOutboxRecord(SQLModel, table=True):
    __tablename__ = "push_notification_outbox"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', 'failed')",
            name="ck_push_notification_outbox_status",
        ),
        UniqueConstraint("event_key", name="uq_push_notification_outbox_event_key"),
        Index(
            "ix_push_notification_outbox_worker_queue",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "ix_push_notification_outbox_user_created",
            "user_id",
            "created_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    event_key: str = Field(min_length=1, max_length=128)
    kind: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(sa_column=Column(Text, nullable=False))
    data_json: dict[str, str] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    target_installation_ids_json: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    completed_installation_ids_json: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    status: str = Field(
        default=PushOutboxStatus.PENDING.value,
        sa_column=Column(String(32), nullable=False),
    )
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=5, ge=1)
    next_attempt_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    lease_owner: str | None = Field(default=None, min_length=1, max_length=128)
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_error_code: str | None = Field(default=None, min_length=1, max_length=128)
    delivered_at: datetime | None = Field(
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

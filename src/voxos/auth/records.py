import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, UniqueConstraint
from sqlmodel import Column, Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_auth_record_id() -> str:
    return str(uuid.uuid4())


class FirebaseUserRecord(SQLModel, table=True):
    __tablename__ = "auth_firebase_users"
    __table_args__ = (
        UniqueConstraint("firebase_user_id", name="uq_auth_firebase_users_firebase_uid"),
        Index("ix_auth_firebase_users_email", "email"),
        Index("ix_auth_firebase_users_last_seen_at", "last_seen_at"),
    )

    id: str = Field(default_factory=new_auth_record_id, primary_key=True, max_length=36)
    firebase_user_id: str = Field(min_length=1, max_length=256)
    email: str | None = Field(default=None, max_length=320)
    email_verified: bool | None = Field(default=None)
    display_name: str | None = Field(default=None, max_length=256)
    photo_url: str | None = Field(default=None, max_length=2048)
    application_defaults_version: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    last_seen_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

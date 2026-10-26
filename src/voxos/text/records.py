from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now


class UserTextPreferenceRecord(SQLModel, table=True):
    __tablename__ = "user_text_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_text_preferences_user_id"),
        Index("ix_user_text_preferences_user_provider", "user_id", "provider"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    provider: str = Field(index=True, min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    default_credential_id: str | None = Field(
        default=None,
        index=True,
        min_length=1,
        max_length=36,
    )
    provider_config: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

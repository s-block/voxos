from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text, UniqueConstraint
from sqlmodel import Field, SQLModel

from voxos.db.models import new_record_id, utc_now


class UserContactRecord(SQLModel, table=True):
    __tablename__ = "user_contacts"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "phone_number",
            name="uq_user_contacts_user_phone_number",
        ),
        Index(
            "ix_user_contacts_user_display_name",
            "user_id",
            "display_name",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    display_name: str = Field(
        min_length=1,
        max_length=256,
        sa_column=Column(String(256), nullable=False),
    )
    phone_number: str = Field(
        min_length=2,
        max_length=16,
        sa_column=Column(String(16), nullable=False),
    )
    email: str | None = Field(
        default=None,
        sa_column=Column(String(320), nullable=True),
    )
    notes: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    is_admin: bool = Field(
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

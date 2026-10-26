import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Index, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_secret_record_id() -> str:
    return str(uuid.uuid4())


class UserSecretRecord(SQLModel, table=True):
    __tablename__ = "user_secret_vault"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "credential_id",
            "purpose",
            name="uq_user_secret_vault_reference",
        ),
        Index("ix_user_secret_vault_user_purpose", "user_id", "purpose"),
        Index("ix_user_secret_vault_reference", "provider", "credential_id", "purpose"),
    )

    id: str = Field(default_factory=new_secret_record_id, primary_key=True, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    provider: str = Field(min_length=1, max_length=64)
    credential_id: str = Field(min_length=1, max_length=36)
    purpose: str = Field(min_length=1, max_length=64)
    ciphertext: bytes = Field(
        repr=False,
        sa_column=Column(LargeBinary, nullable=False),
    )
    nonce: bytes = Field(
        repr=False,
        sa_column=Column(LargeBinary, nullable=False),
    )
    encrypted_data_key: bytes | None = Field(
        default=None,
        repr=False,
        sa_column=Column(LargeBinary, nullable=True),
    )
    encryption_backend: str = Field(min_length=1, max_length=64)
    encryption_key_id: str | None = Field(default=None, min_length=1, max_length=512)
    encryption_algorithm: str = Field(default="AES-256-GCM", min_length=1, max_length=64)
    encryption_context: dict[str, str] = Field(
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

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, DateTime, Index, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class ProviderCredentialAuthType(StrEnum):
    API_KEY = "api_key"
    BEARER_TOKEN = "bearer_token"
    OAUTH_REFRESH_TOKEN = "oauth_refresh_token"


class ProviderCredentialStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_record_id() -> str:
    return str(uuid.uuid4())


class UserVoicePreferenceRecord(SQLModel, table=True):
    __tablename__ = "user_voice_preferences"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_voice_preferences_user_id"),
        Index("ix_user_voice_preferences_user_provider", "user_id", "provider"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    provider: str = Field(index=True, min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    voice: str = Field(min_length=1, max_length=256)
    default_credential_id: str = Field(
        foreign_key="user_provider_credentials.id",
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


class UserProviderCredentialRecord(SQLModel, table=True):
    __tablename__ = "user_provider_credentials"
    __table_args__ = (
        Index("ix_user_provider_credentials_user_provider", "user_id", "provider"),
        Index(
            "ix_user_provider_credentials_user_provider_status",
            "user_id",
            "provider",
            "status",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    provider: str = Field(index=True, min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=256)
    auth_type: str = Field(
        default=ProviderCredentialAuthType.API_KEY.value,
        min_length=1,
        max_length=64,
    )
    status: str = Field(
        default=ProviderCredentialStatus.ACTIVE.value,
        sa_column=Column(String(32), nullable=False),
    )
    last_four: str | None = Field(default=None, min_length=4, max_length=4)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class UserLiveKitSettingsRecord(SQLModel, table=True):
    __tablename__ = "user_livekit_settings"
    __table_args__ = (UniqueConstraint("user_id", name="uq_user_livekit_settings_user_id"),)

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    livekit_url: str = Field(min_length=1, max_length=2048)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

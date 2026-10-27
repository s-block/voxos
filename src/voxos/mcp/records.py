import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_mcp_record_id() -> str:
    return str(uuid.uuid4())


class UserMcpServerRecord(SQLModel, table=True):
    __tablename__ = "user_mcp_servers"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_user_mcp_servers_user_label"),
        Index("ix_user_mcp_servers_user_status", "user_id", "status"),
        Index("ix_user_mcp_servers_user_enabled", "user_id", "enabled"),
    )

    id: str = Field(default_factory=new_mcp_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    transport: str = Field(min_length=1, max_length=32)
    url: str = Field(min_length=1, max_length=2048)
    auth_type: str = Field(min_length=1, max_length=64)
    enabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    non_admin_tools_enabled: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False),
    )
    trust_state: str = Field(
        default="untrusted",
        sa_column=Column(String(32), nullable=False, server_default="untrusted"),
    )
    status: str = Field(
        default="disabled",
        sa_column=Column(String(32), nullable=False),
    )
    status_message: str | None = Field(default=None, max_length=512)
    auth_config: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    safe_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    allowed_tools: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    discovered_tools: list[str] = Field(
        default_factory=list,
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


class UserMcpSecretRecord(SQLModel, table=True):
    __tablename__ = "user_mcp_secrets"
    __table_args__ = (
        UniqueConstraint("server_id", "secret_kind", name="uq_user_mcp_secrets_kind"),
        ForeignKeyConstraint(
            ["server_id"],
            ["user_mcp_servers.id"],
            ondelete="CASCADE",
        ),
        Index("ix_user_mcp_secrets_user_server", "user_id", "server_id"),
    )

    id: str = Field(default_factory=new_mcp_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    server_id: str = Field(index=True, min_length=1, max_length=36)
    secret_kind: str = Field(min_length=1, max_length=64)
    secret_metadata: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    last_four: str | None = Field(default=None, min_length=4, max_length=4)
    expires_at: datetime | None = Field(
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


class UserMcpOAuthStateRecord(SQLModel, table=True):
    __tablename__ = "user_mcp_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_user_mcp_oauth_states_state_hash"),
        ForeignKeyConstraint(
            ["server_id"],
            ["user_mcp_servers.id"],
            ondelete="CASCADE",
        ),
        Index("ix_user_mcp_oauth_states_user_server", "user_id", "server_id"),
        Index("ix_user_mcp_oauth_states_expires_at", "expires_at"),
    )

    id: str = Field(default_factory=new_mcp_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    server_id: str = Field(index=True, min_length=1, max_length=36)
    state_hash: str = Field(index=True, min_length=64, max_length=64)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    authorization_url: str = Field(min_length=1, max_length=4096)
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel

from voxos.browser_accounts.models import (
    DEFAULT_BROWSER_COUNTRY_CODE,
    BrowserAccountAuthenticationMode,
    BrowserAccountProvider,
    BrowserAccountStatus,
)
from voxos.db.models import new_record_id, utc_now


class BrowserAccountRecord(SQLModel, table=True):
    __tablename__ = "browser_accounts"
    __table_args__ = (
        CheckConstraint(
            "length(browser_country_code) = 2 "
            "AND substr(browser_country_code, 1, 1) BETWEEN 'A' AND 'Z' "
            "AND substr(browser_country_code, 2, 1) BETWEEN 'A' AND 'Z'",
            name="ck_browser_accounts_country_code",
        ),
        Index(
            "uq_browser_accounts_user_label",
            "user_id",
            "label",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        UniqueConstraint(
            "user_id",
            "id",
            name="uq_browser_accounts_user_id_id",
        ),
        ForeignKeyConstraint(
            ["provider_connection_id"],
            ["user_mcp_servers.id"],
            ondelete="RESTRICT",
        ),
        Index("ix_browser_accounts_user_status", "user_id", "status"),
        Index(
            "ix_browser_accounts_user_origin",
            "user_id",
            "primary_origin",
        ),
        Index(
            "ix_browser_accounts_provider_connection",
            "provider_connection_id",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=128)
    provider: str = Field(
        default=BrowserAccountProvider.BROWSER_USE_CLOUD.value,
        max_length=32,
        sa_column=Column(String(32), nullable=False),
    )
    provider_connection_id: str | None = Field(default=None, min_length=1, max_length=36)
    primary_origin: str = Field(min_length=1, max_length=512)
    authentication_origins: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    browser_country_code: str = Field(
        default=DEFAULT_BROWSER_COUNTRY_CODE,
        sa_column=Column(
            String(2),
            nullable=False,
            server_default=DEFAULT_BROWSER_COUNTRY_CODE,
        ),
    )
    authentication_mode: str = Field(
        default=BrowserAccountAuthenticationMode.MANAGED_PROFILE.value,
        max_length=32,
        sa_column=Column(String(32), nullable=False),
    )
    status: str = Field(
        default=BrowserAccountStatus.SETUP_REQUIRED.value,
        max_length=32,
        sa_column=Column(String(32), nullable=False),
    )
    error_category: str | None = Field(default=None, min_length=1, max_length=64)
    setup_started_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_verified_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_used_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    delete_requested_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    version: int = Field(
        default=1,
        ge=1,
        sa_column=Column(Integer, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AgentBrowserAccountGrantRecord(SQLModel, table=True):
    __tablename__ = "agent_browser_account_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "agent_id",
            "browser_account_id",
            name="uq_agent_browser_account_grants_assignment",
        ),
        ForeignKeyConstraint(
            ["browser_account_id"],
            ["browser_accounts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["user_agents.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_agent_browser_account_grants_user_agent",
            "user_id",
            "agent_id",
        ),
        Index(
            "ix_agent_browser_account_grants_user_account",
            "user_id",
            "browser_account_id",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    browser_account_id: str = Field(min_length=1, max_length=36)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BrowserAccountRunAuthorizationRecord(SQLModel, table=True):
    __tablename__ = "browser_account_run_authorizations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["browser_account_id"],
            ["browser_accounts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["agent_id"],
            ["user_agents.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_browser_account_authorizations_user_session",
            "user_id",
            "session_id",
        ),
        Index(
            "ix_browser_account_authorizations_user_run",
            "user_id",
            "run_id",
        ),
        Index(
            "ix_browser_account_authorizations_user_turn",
            "user_id",
            "client_turn_id",
        ),
        Index(
            "ix_browser_account_authorizations_account_expiry",
            "browser_account_id",
            "expires_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    browser_account_id: str = Field(min_length=1, max_length=36)
    agent_id: str = Field(min_length=1, max_length=36)
    run_id: str | None = Field(default=None, max_length=36)
    session_id: str = Field(min_length=1, max_length=36)
    client_turn_id: str = Field(min_length=1, max_length=64)
    approved_origins: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    task_summary: str = Field(sa_column=Column(Text, nullable=False))
    approved_by_user_id: str = Field(min_length=1, max_length=256)
    approved_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    consumed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    consumed_run_id: str | None = Field(default=None, max_length=36)
    revoked_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class BrowserAccountAuditEventRecord(SQLModel, table=True):
    __tablename__ = "browser_account_audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["browser_account_id"],
            ["browser_accounts.id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_browser_account_audit_user_created",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_browser_account_audit_account_created",
            "browser_account_id",
            "created_at",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    browser_account_id: str = Field(min_length=1, max_length=36)
    authorization_id: str | None = Field(default=None, max_length=36)
    agent_id: str | None = Field(default=None, max_length=36)
    run_id: str | None = Field(default=None, max_length=36)
    session_id: str | None = Field(default=None, max_length=36)
    event_kind: str = Field(index=True, min_length=1, max_length=64)
    outcome: str = Field(min_length=1, max_length=32)
    error_category: str | None = Field(default=None, min_length=1, max_length=64)
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class BrowserAccountLeaseRecord(SQLModel, table=True):
    __tablename__ = "browser_account_leases"
    __table_args__ = (
        UniqueConstraint(
            "browser_account_id",
            name="uq_browser_account_leases_account",
        ),
        ForeignKeyConstraint(
            ["browser_account_id"],
            ["browser_accounts.id"],
            ondelete="CASCADE",
        ),
        Index("ix_browser_account_leases_user_expiry", "user_id", "expires_at"),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    browser_account_id: str = Field(min_length=1, max_length=36)
    owner_id: str = Field(min_length=1, max_length=128)
    lease_kind: str = Field(min_length=1, max_length=32)
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    heartbeat_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False),
    )

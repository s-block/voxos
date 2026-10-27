"""add browser accounts

Revision ID: a8f1c2d3e4b5
Revises: 5bab8459bff3
Create Date: 2026-08-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8f1c2d3e4b5"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "5bab8459bff3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "browser_accounts",
    "agent_browser_account_grants",
    "browser_account_run_authorizations",
    "browser_account_audit_events",
    "browser_account_leases",
)
TENANT_CLAUSE = "user_id = current_setting('app.user_id', true)"


def upgrade() -> None:
    op.create_table(
        "browser_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_connection_id", sa.String(length=36), nullable=False),
        sa.Column("primary_origin", sa.String(length=512), nullable=False),
        sa.Column("authentication_origins", sa.JSON(), nullable=False),
        sa.Column("authentication_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("setup_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("provider_connection_id",),
            ("user_mcp_servers.id",),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "label",
            name="uq_browser_accounts_user_label",
        ),
    )
    op.create_index(
        "ix_browser_accounts_provider_connection",
        "browser_accounts",
        ("provider_connection_id",),
    )
    op.create_index("ix_browser_accounts_user_id", "browser_accounts", ("user_id",))
    op.create_index(
        "ix_browser_accounts_user_origin",
        "browser_accounts",
        ("user_id", "primary_origin"),
    )
    op.create_index(
        "ix_browser_accounts_user_status",
        "browser_accounts",
        ("user_id", "status"),
    )
    op.create_table(
        "agent_browser_account_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("browser_account_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(("agent_id",), ("user_agents.id",), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ("browser_account_id",),
            ("browser_accounts.id",),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "agent_id",
            "browser_account_id",
            name="uq_agent_browser_account_grants_assignment",
        ),
    )
    op.create_index(
        "ix_agent_browser_account_grants_user_account",
        "agent_browser_account_grants",
        ("user_id", "browser_account_id"),
    )
    op.create_index(
        "ix_agent_browser_account_grants_user_agent",
        "agent_browser_account_grants",
        ("user_id", "agent_id"),
    )

    op.create_table(
        "browser_account_run_authorizations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("browser_account_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("approved_origins", sa.JSON(), nullable=False),
        sa.Column("task_digest", sa.String(length=64), nullable=False),
        sa.Column("task_summary", sa.Text(), nullable=False),
        sa.Column("approved_by_user_id", sa.String(length=256), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_run_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(("agent_id",), ("user_agents.id",), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ("browser_account_id",),
            ("browser_accounts.id",),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_browser_account_authorizations_account_expiry",
        "browser_account_run_authorizations",
        ("browser_account_id", "expires_at"),
    )
    op.create_index(
        "ix_browser_account_authorizations_user_run",
        "browser_account_run_authorizations",
        ("user_id", "run_id"),
    )
    op.create_index(
        "ix_browser_account_authorizations_user_session",
        "browser_account_run_authorizations",
        ("user_id", "session_id"),
    )

    op.create_table(
        "browser_account_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("browser_account_id", sa.String(length=36), nullable=False),
        sa.Column("authorization_id", sa.String(length=36), nullable=True),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("event_kind", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("browser_account_id",),
            ("browser_accounts.id",),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_browser_account_audit_account_created",
        "browser_account_audit_events",
        ("browser_account_id", "created_at"),
    )
    op.create_index(
        "ix_browser_account_audit_events_event_kind",
        "browser_account_audit_events",
        ("event_kind",),
    )
    op.create_index(
        "ix_browser_account_audit_user_created",
        "browser_account_audit_events",
        ("user_id", "created_at"),
    )

    op.create_table(
        "browser_account_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("browser_account_id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("lease_kind", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ("browser_account_id",),
            ("browser_accounts.id",),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "browser_account_id",
            name="uq_browser_account_leases_account",
        ),
    )
    op.create_index(
        "ix_browser_account_leases_user_expiry",
        "browser_account_leases",
        ("user_id", "expires_at"),
    )

    if op.get_bind().dialect.name == "postgresql":
        for table_name in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
            op.execute(
                f"""
                CREATE POLICY "rls_{table_name}_tenant"
                ON "{table_name}"
                FOR ALL
                USING ({TENANT_CLAUSE})
                WITH CHECK ({TENANT_CLAUSE})
                """,
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"',
            )
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_table("browser_account_leases")
    op.drop_table("browser_account_audit_events")
    op.drop_table("browser_account_run_authorizations")
    op.drop_table("agent_browser_account_grants")
    op.drop_table("browser_accounts")

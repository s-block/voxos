"""add user configured mcp servers

Revision ID: 202606130001
Revises: 202606120002
Create Date: 2026-06-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606130001"
down_revision: str | None = "202606120002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_mcp_servers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("auth_type", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("trust_state", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_message", sa.String(length=512), nullable=True),
        sa.Column(
            "auth_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "allowed_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "discovered_tools",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "label", name="uq_user_mcp_servers_user_label"),
    )
    op.create_index("ix_user_mcp_servers_user_id", "user_mcp_servers", ["user_id"])
    op.create_index(
        "ix_user_mcp_servers_user_status",
        "user_mcp_servers",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_user_mcp_servers_user_enabled",
        "user_mcp_servers",
        ["user_id", "enabled"],
    )

    op.create_table(
        "user_mcp_secrets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("server_id", sa.String(length=36), nullable=False),
        sa.Column("secret_kind", sa.String(length=64), nullable=False),
        sa.Column(
            "secret_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("last_four", sa.String(length=4), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["user_mcp_servers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("server_id", "secret_kind", name="uq_user_mcp_secrets_kind"),
    )
    op.create_index("ix_user_mcp_secrets_user_id", "user_mcp_secrets", ["user_id"])
    op.create_index("ix_user_mcp_secrets_server_id", "user_mcp_secrets", ["server_id"])
    op.create_index(
        "ix_user_mcp_secrets_user_server",
        "user_mcp_secrets",
        ["user_id", "server_id"],
    )

    op.create_table(
        "user_mcp_oauth_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("server_id", sa.String(length=36), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("authorization_url", sa.String(length=4096), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["user_mcp_servers.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "state_hash",
            name="uq_user_mcp_oauth_states_state_hash",
        ),
    )
    op.create_index(
        "ix_user_mcp_oauth_states_user_id",
        "user_mcp_oauth_states",
        ["user_id"],
    )
    op.create_index(
        "ix_user_mcp_oauth_states_server_id",
        "user_mcp_oauth_states",
        ["server_id"],
    )
    op.create_index(
        "ix_user_mcp_oauth_states_state_hash",
        "user_mcp_oauth_states",
        ["state_hash"],
    )
    op.create_index(
        "ix_user_mcp_oauth_states_user_server",
        "user_mcp_oauth_states",
        ["user_id", "server_id"],
    )
    op.create_index(
        "ix_user_mcp_oauth_states_expires_at",
        "user_mcp_oauth_states",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_mcp_oauth_states_expires_at",
        table_name="user_mcp_oauth_states",
    )
    op.drop_index(
        "ix_user_mcp_oauth_states_user_server",
        table_name="user_mcp_oauth_states",
    )
    op.drop_index(
        "ix_user_mcp_oauth_states_state_hash",
        table_name="user_mcp_oauth_states",
    )
    op.drop_index(
        "ix_user_mcp_oauth_states_server_id",
        table_name="user_mcp_oauth_states",
    )
    op.drop_index(
        "ix_user_mcp_oauth_states_user_id",
        table_name="user_mcp_oauth_states",
    )
    op.drop_table("user_mcp_oauth_states")
    op.drop_index("ix_user_mcp_secrets_user_server", table_name="user_mcp_secrets")
    op.drop_index("ix_user_mcp_secrets_server_id", table_name="user_mcp_secrets")
    op.drop_index("ix_user_mcp_secrets_user_id", table_name="user_mcp_secrets")
    op.drop_table("user_mcp_secrets")
    op.drop_index("ix_user_mcp_servers_user_enabled", table_name="user_mcp_servers")
    op.drop_index("ix_user_mcp_servers_user_status", table_name="user_mcp_servers")
    op.drop_index("ix_user_mcp_servers_user_id", table_name="user_mcp_servers")
    op.drop_table("user_mcp_servers")

"""add text chat preferences and session metadata

Revision ID: 202606250002
Revises: 202606250001
Create Date: 2026-06-25 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606250002"
down_revision: str | None = "202606250001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_text_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("default_credential_id", sa.String(length=36), nullable=True),
        sa.Column("provider_config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_text_preferences_user_id"),
    )
    op.create_index(
        "ix_user_text_preferences_default_credential_id",
        "user_text_preferences",
        ["default_credential_id"],
    )
    op.create_index(
        "ix_user_text_preferences_provider",
        "user_text_preferences",
        ["provider"],
    )
    op.create_index(
        "ix_user_text_preferences_user_id",
        "user_text_preferences",
        ["user_id"],
    )
    op.create_index(
        "ix_user_text_preferences_user_provider",
        "user_text_preferences",
        ["user_id", "provider"],
    )

    op.add_column(
        "agent_sessions",
        sa.Column(
            "channel",
            sa.String(length=64),
            nullable=False,
            server_default="livekit",
        ),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("title", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "agent_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_sessions_channel", "agent_sessions", ["channel"])
    op.create_index(
        "ix_agent_sessions_user_channel_started_at",
        "agent_sessions",
        ["user_id", "channel", "started_at"],
    )
    op.create_index(
        "ix_agent_sessions_user_agent_channel_started_at",
        "agent_sessions",
        ["user_id", "agent_id", "channel", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_sessions_user_agent_channel_started_at",
        table_name="agent_sessions",
    )
    op.drop_index(
        "ix_agent_sessions_user_channel_started_at",
        table_name="agent_sessions",
    )
    op.drop_index("ix_agent_sessions_channel", table_name="agent_sessions")
    op.drop_column("agent_sessions", "deleted_at")
    op.drop_column("agent_sessions", "title")
    op.drop_column("agent_sessions", "channel")

    op.drop_index(
        "ix_user_text_preferences_user_provider",
        table_name="user_text_preferences",
    )
    op.drop_index(
        "ix_user_text_preferences_user_id",
        table_name="user_text_preferences",
    )
    op.drop_index(
        "ix_user_text_preferences_provider",
        table_name="user_text_preferences",
    )
    op.drop_index(
        "ix_user_text_preferences_default_credential_id",
        table_name="user_text_preferences",
    )
    op.drop_table("user_text_preferences")

"""add agent tool route preferences

Revision ID: 5a44fb0f86f4
Revises: 7cd844e67d81
Create Date: 2026-08-02 08:54:13.119583

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a44fb0f86f4"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "7cd844e67d81"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "agent_tool_route_preferences"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("pinned_tool_name", sa.String(length=256), nullable=True),
        sa.Column("pinned_tool_source", sa.String(length=32), nullable=True),
        sa.Column("pinned_mcp_server_id", sa.String(length=36), nullable=True),
        sa.Column("pinned_integration_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('automatic', 'pinned')",
            name="ck_agent_tool_route_preferences_mode",
        ),
        sa.CheckConstraint(
            "(mode = 'automatic' AND pinned_tool_name IS NULL AND "
            "pinned_tool_source IS NULL AND pinned_mcp_server_id IS NULL AND "
            "pinned_integration_id IS NULL) OR "
            "(mode = 'pinned' AND pinned_tool_name IS NOT NULL AND "
            "pinned_tool_source IS NOT NULL)",
            name="ck_agent_tool_route_preferences_pinned_tool",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "agent_id",
            "action",
            name="uq_agent_tool_route_preferences_user_agent_action",
        ),
    )
    op.create_index(
        "ix_agent_tool_route_preferences_action",
        TABLE_NAME,
        ("action",),
    )
    op.create_index(
        "ix_agent_tool_route_preferences_agent_id",
        TABLE_NAME,
        ("agent_id",),
    )
    op.create_index(
        "ix_agent_tool_route_preferences_user_agent",
        TABLE_NAME,
        ("user_id", "agent_id"),
    )
    op.create_index(
        "ix_agent_tool_route_preferences_user_id",
        TABLE_NAME,
        ("user_id",),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_tenant"
        ON "{TABLE_NAME}"
        USING (user_id = current_setting('app.user_id', true))
        WITH CHECK (user_id = current_setting('app.user_id', true))
        """,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_tool_route_preferences_user_id", table_name=TABLE_NAME)
    op.drop_index("ix_agent_tool_route_preferences_user_agent", table_name=TABLE_NAME)
    op.drop_index("ix_agent_tool_route_preferences_agent_id", table_name=TABLE_NAME)
    op.drop_index("ix_agent_tool_route_preferences_action", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)

"""add agent webhooks

Revision ID: 98930ac5cc6d
Revises: e7c447235901
Create Date: 2026-08-25 17:35:01.436807

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "98930ac5cc6d"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "e7c447235901"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "agent_webhooks"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"
WEBHOOK_SCOPE_CLAUSE = "current_setting('app.service_scope', true) = 'agent_webhook'"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_webhooks_agent_id", TABLE_NAME, ("agent_id",))
    op.create_index("ix_agent_webhooks_user_agent", TABLE_NAME, ("user_id", "agent_id"))
    op.create_index("ix_agent_webhooks_user_enabled", TABLE_NAME, ("user_id", "enabled"))
    op.create_index("ix_agent_webhooks_user_id", TABLE_NAME, ("user_id",))

    op.add_column("agent_runs", sa.Column("webhook_id", sa.String(length=36), nullable=True))
    op.create_index("ix_agent_runs_webhook", "agent_runs", ("webhook_id", "created_at"))
    op.create_foreign_key(
        "fk_agent_runs_webhook",
        "agent_runs",
        TABLE_NAME,
        ("webhook_id",),
        ("id",),
        ondelete="RESTRICT",
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_tenant"
        ON "{TABLE_NAME}"
        FOR ALL
        USING ({TENANT_CLAUSE})
        WITH CHECK ({TENANT_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_agent_webhook_select"
        ON "{TABLE_NAME}"
        FOR SELECT
        USING ({WEBHOOK_SCOPE_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_agent_webhook_select" ON "{TABLE_NAME}"',
        )
        op.execute(f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_tenant" ON "{TABLE_NAME}"')

    op.drop_constraint("fk_agent_runs_webhook", "agent_runs", type_="foreignkey")
    op.drop_index("ix_agent_runs_webhook", table_name="agent_runs")
    op.drop_column("agent_runs", "webhook_id")
    op.drop_index("ix_agent_webhooks_user_id", table_name=TABLE_NAME)
    op.drop_index("ix_agent_webhooks_user_enabled", table_name=TABLE_NAME)
    op.drop_index("ix_agent_webhooks_user_agent", table_name=TABLE_NAME)
    op.drop_index("ix_agent_webhooks_agent_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)

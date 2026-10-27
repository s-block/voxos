"""link schedules to chats

Revision ID: ca88b0c2bd60
Revises: 096d5fec85c1
Create Date: 2026-08-22 12:28:09.202161

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca88b0c2bd60"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "096d5fec85c1"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_schedules",
        sa.Column("chat_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_agent_schedules_user_chat",
        "agent_schedules",
        ("user_id", "chat_id"),
    )
    op.create_foreign_key(
        "fk_agent_schedules_chat",
        "agent_schedules",
        "agent_sessions",
        ("chat_id",),
        ("id",),
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_agent_schedules_chat",
        "agent_schedules",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_agent_schedules_user_chat",
        table_name="agent_schedules",
    )
    op.drop_column("agent_schedules", "chat_id")

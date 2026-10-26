"""index agent run creator user

Revision ID: 202606270005
Revises: 202606270004
Create Date: 2026-06-27 14:45:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606270005"
down_revision: str | None = "202606270004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_runs_created_by_user",
        "agent_runs",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_created_by_user", table_name="agent_runs")

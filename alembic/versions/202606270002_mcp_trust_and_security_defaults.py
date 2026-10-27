"""harden mcp trust defaults

Revision ID: 202606270002
Revises: 202606270001
Create Date: 2026-06-27 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606270002"
down_revision: str | None = "202606270001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "user_mcp_servers",
        "trust_state",
        server_default=sa.text("'untrusted'"),
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )
    op.execute(
        """
        UPDATE user_mcp_servers
        SET trust_state = 'untrusted'
        WHERE status <> 'connected'
        """,
    )


def downgrade() -> None:
    op.alter_column(
        "user_mcp_servers",
        "trust_state",
        server_default=None,
        existing_type=sa.String(length=32),
        existing_nullable=False,
    )

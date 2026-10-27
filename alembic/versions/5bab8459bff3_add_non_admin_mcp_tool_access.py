"""add non-admin MCP tool access

Revision ID: 5bab8459bff3
Revises: 2f40fe48bfed
Create Date: 2026-08-05 13:12:39.197182

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5bab8459bff3"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "2f40fe48bfed"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_mcp_servers",
        sa.Column(
            "non_admin_tools_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "user_mcp_servers",
        "non_admin_tools_enabled",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("user_mcp_servers", "non_admin_tools_enabled")

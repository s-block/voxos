"""add skill capability preferences

Revision ID: 2f40fe48bfed
Revises: a23104f53d65
Create Date: 2026-08-04 09:39:59.865814

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f40fe48bfed"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "a23104f53d65"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_skills",
        sa.Column(
            "preferred_capabilities",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.alter_column("agent_skills", "preferred_capabilities", server_default=None)


def downgrade() -> None:
    op.drop_column("agent_skills", "preferred_capabilities")

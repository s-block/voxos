"""allow local browser accounts

Revision ID: 826e4de3de09
Revises: e27efebf8898
Create Date: 2026-08-10 20:59:34.444418

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "826e4de3de09"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "e27efebf8898"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "browser_accounts",
        "provider_connection_id",
        existing_type=sa.VARCHAR(length=36),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "browser_accounts",
        "provider_connection_id",
        existing_type=sa.VARCHAR(length=36),
        nullable=False,
    )

"""track application defaults initialization

Revision ID: d88f0d358809
Revises: 202608030001
Create Date: 2026-08-03 06:05:39.920911

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d88f0d358809"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "202608030001"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_firebase_users",
        sa.Column("application_defaults_version", sa.String(length=64), nullable=True),
    )
    users = sa.table(
        "auth_firebase_users",
        sa.column("application_defaults_version", sa.String(length=64)),
    )
    op.execute(
        users.update().values(application_defaults_version="2026-08-03.1"),
    )


def downgrade() -> None:
    op.drop_column("auth_firebase_users", "application_defaults_version")

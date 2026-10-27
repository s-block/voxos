"""expand integration conversation peer

Revision ID: 9ced105cdeee
Revises: 06a519f1b316
Create Date: 2026-07-21 16:07:23.381507

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9ced105cdeee"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "06a519f1b316"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "integration_conversations",
        "remote_peer",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "integration_conversations",
        "remote_peer",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=False,
    )

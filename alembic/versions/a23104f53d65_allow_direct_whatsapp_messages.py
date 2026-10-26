"""allow direct whatsapp messages

Revision ID: a23104f53d65
Revises: d88f0d358809
Create Date: 2026-08-03 21:25:33.546463

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a23104f53d65"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "d88f0d358809"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "whatsapp_outbox",
        "reply_id",
        existing_type=sa.VARCHAR(length=36),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "whatsapp_outbox",
        "reply_id",
        existing_type=sa.VARCHAR(length=36),
        nullable=False,
    )

"""Make voice worker warm requests persistent.

Revision ID: 3ee251b6a41f
Revises: c0fdb7fcd673
Create Date: 2026-08-27 06:30:43.183208
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3ee251b6a41f"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "c0fdb7fcd673"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "voice_worker_warm_requests"


def upgrade() -> None:
    op.alter_column(
        TABLE_NAME,
        "expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=True,
    )
    if op.get_bind().dialect.name == "postgresql":
        # Migrations run as the non-bypass application role. Temporarily let the
        # table owner update all existing desired-state rows, then restore FORCE
        # RLS in the same transactional migration.
        op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    op.execute(
        sa.update(sa.table(TABLE_NAME, sa.column("expires_at"))).values(
            expires_at=None,
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
    table = sa.table(
        TABLE_NAME,
        sa.column("expires_at"),
        sa.column("updated_at"),
    )
    op.execute(
        sa.update(table).where(table.c.expires_at.is_(None)).values(expires_at=table.c.updated_at),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.alter_column(
        TABLE_NAME,
        "expires_at",
        existing_type=postgresql.TIMESTAMP(timezone=True),
        nullable=False,
    )

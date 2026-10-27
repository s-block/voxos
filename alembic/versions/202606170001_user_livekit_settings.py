"""add user livekit settings

Revision ID: 202606170001
Revises: 202606130001
Create Date: 2026-06-17 12:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606170001"
down_revision: str | None = "202606130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_livekit_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("livekit_url", sa.String(length=2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_livekit_settings_user_id"),
    )
    op.create_index(
        "ix_user_livekit_settings_user_id",
        "user_livekit_settings",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_livekit_settings_user_id",
        table_name="user_livekit_settings",
    )
    op.drop_table("user_livekit_settings")

"""add firebase auth users

Revision ID: 202606120002
Revises: 202606120001
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606120002"
down_revision: str | None = "202606120001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_firebase_users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("firebase_user_id", sa.String(length=256), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("photo_url", sa.String(length=2048), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "firebase_user_id",
            name="uq_auth_firebase_users_firebase_uid",
        ),
    )
    op.create_index(
        "ix_auth_firebase_users_email",
        "auth_firebase_users",
        ["email"],
    )
    op.create_index(
        "ix_auth_firebase_users_last_seen_at",
        "auth_firebase_users",
        ["last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_firebase_users_last_seen_at",
        table_name="auth_firebase_users",
    )
    op.drop_index("ix_auth_firebase_users_email", table_name="auth_firebase_users")
    op.drop_table("auth_firebase_users")

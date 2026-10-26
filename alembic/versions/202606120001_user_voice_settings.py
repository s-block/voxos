"""add user voice preferences and encrypted provider credentials

Revision ID: 202606120001
Revises:
Create Date: 2026-06-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "202606120001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_provider_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("auth_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_four", sa.String(length=4), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_provider_credentials_provider",
        "user_provider_credentials",
        ["provider"],
    )
    op.create_index(
        "ix_user_provider_credentials_user_id",
        "user_provider_credentials",
        ["user_id"],
    )
    op.create_index(
        "ix_user_provider_credentials_user_provider",
        "user_provider_credentials",
        ["user_id", "provider"],
    )
    op.create_index(
        "ix_user_provider_credentials_user_provider_status",
        "user_provider_credentials",
        ["user_id", "provider", "status"],
    )

    op.create_table(
        "user_voice_preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("voice", sa.String(length=256), nullable=False),
        sa.Column("default_credential_id", sa.String(length=36), nullable=False),
        sa.Column(
            "provider_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["default_credential_id"],
            ["user_provider_credentials.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_voice_preferences_user_id"),
    )
    op.create_index(
        "ix_user_voice_preferences_default_credential_id",
        "user_voice_preferences",
        ["default_credential_id"],
    )
    op.create_index(
        "ix_user_voice_preferences_provider",
        "user_voice_preferences",
        ["provider"],
    )
    op.create_index(
        "ix_user_voice_preferences_user_id",
        "user_voice_preferences",
        ["user_id"],
    )
    op.create_index(
        "ix_user_voice_preferences_user_provider",
        "user_voice_preferences",
        ["user_id", "provider"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_voice_preferences_user_provider",
        table_name="user_voice_preferences",
    )
    op.drop_index("ix_user_voice_preferences_user_id", table_name="user_voice_preferences")
    op.drop_index("ix_user_voice_preferences_provider", table_name="user_voice_preferences")
    op.drop_index(
        "ix_user_voice_preferences_default_credential_id",
        table_name="user_voice_preferences",
    )
    op.drop_table("user_voice_preferences")
    op.drop_index(
        "ix_user_provider_credentials_user_provider_status",
        table_name="user_provider_credentials",
    )
    op.drop_index(
        "ix_user_provider_credentials_user_provider",
        table_name="user_provider_credentials",
    )
    op.drop_index(
        "ix_user_provider_credentials_user_id",
        table_name="user_provider_credentials",
    )
    op.drop_index(
        "ix_user_provider_credentials_provider",
        table_name="user_provider_credentials",
    )
    op.drop_table("user_provider_credentials")

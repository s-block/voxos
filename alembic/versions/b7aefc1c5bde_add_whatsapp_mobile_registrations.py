"""add WhatsApp mobile registrations

Revision ID: b7aefc1c5bde
Revises: 398ea89de8ce
Create Date: 2026-07-21 06:48:15.640011

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7aefc1c5bde"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "398ea89de8ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "whatsapp_mobile_registrations"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("source_integration_id", sa.String(length=36), nullable=False),
        sa.Column("source_resource_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("masked_phone_number", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("capture_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('armed', 'armed_voice', 'code_received', 'expired')",
            name="ck_whatsapp_mobile_registrations_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "source_resource_id",
            name="uq_whatsapp_mobile_registrations_source_resource",
        ),
        sa.UniqueConstraint(
            "user_id",
            name="uq_whatsapp_mobile_registrations_user",
        ),
    )
    op.create_index(
        "ix_whatsapp_mobile_registrations_capture",
        TABLE_NAME,
        ("user_id", "source_resource_id", "capture_expires_at"),
    )
    op.create_index(
        "ix_whatsapp_mobile_registrations_source_integration_id",
        TABLE_NAME,
        ("source_integration_id",),
    )
    op.create_index(
        "ix_whatsapp_mobile_registrations_source_resource_id",
        TABLE_NAME,
        ("source_resource_id",),
    )
    op.create_index(
        "ix_whatsapp_mobile_registrations_user_id",
        TABLE_NAME,
        ("user_id",),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_tenant"
        ON "{TABLE_NAME}"
        FOR ALL
        USING ({TENANT_CLAUSE})
        WITH CHECK ({TENANT_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_tenant" ON "{TABLE_NAME}"',
        )

    op.drop_index(
        "ix_whatsapp_mobile_registrations_user_id",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_whatsapp_mobile_registrations_source_resource_id",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_whatsapp_mobile_registrations_source_integration_id",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_whatsapp_mobile_registrations_capture",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)

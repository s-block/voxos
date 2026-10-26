"""add push notifications

Revision ID: e7c447235901
Revises: ca88b0c2bd60
Create Date: 2026-08-23 15:29:45.185986

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7c447235901"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "ca88b0c2bd60"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INSTALLATIONS_TABLE = "push_installations"
OUTBOX_TABLE = "push_notification_outbox"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"
WORKER_CLAUSE = "current_setting('app.service_scope', true) = 'notification_worker'"


def upgrade() -> None:
    op.create_table(
        INSTALLATIONS_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("installation_id", sa.String(length=256), nullable=False),
        sa.Column("registration_token", sa.Text(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "platform IN ('android', 'ios')",
            name="ck_push_installations_platform",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installation_id",
            name="uq_push_installations_installation_id",
        ),
    )
    op.create_index(
        "ix_push_installations_installation_id",
        INSTALLATIONS_TABLE,
        ("installation_id",),
    )
    op.create_index(
        "ix_push_installations_user_active",
        INSTALLATIONS_TABLE,
        ("user_id", "active", "last_seen_at"),
    )
    op.create_index(
        "ix_push_installations_user_id",
        INSTALLATIONS_TABLE,
        ("user_id",),
    )

    op.create_table(
        OUTBOX_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("target_installation_ids_json", sa.JSON(), nullable=False),
        sa.Column("completed_installation_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'delivered', 'failed')",
            name="ck_push_notification_outbox_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_key",
            name="uq_push_notification_outbox_event_key",
        ),
    )
    op.create_index(
        "ix_push_notification_outbox_user_created",
        OUTBOX_TABLE,
        ("user_id", "created_at"),
    )
    op.create_index(
        "ix_push_notification_outbox_user_id",
        OUTBOX_TABLE,
        ("user_id",),
    )
    op.create_index(
        "ix_push_notification_outbox_worker_queue",
        OUTBOX_TABLE,
        ("status", "next_attempt_at", "lease_expires_at", "created_at"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name in (INSTALLATIONS_TABLE, OUTBOX_TABLE):
        op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "rls_{table_name}_tenant"
            ON "{table_name}"
            FOR ALL
            USING ({TENANT_CLAUSE})
            WITH CHECK ({TENANT_CLAUSE})
            """,
        )
        op.execute(
            f"""
            CREATE POLICY "rls_{table_name}_notification_worker"
            ON "{table_name}"
            FOR ALL
            USING ({WORKER_CLAUSE})
            WITH CHECK ({WORKER_CLAUSE})
            """,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in (OUTBOX_TABLE, INSTALLATIONS_TABLE):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_notification_worker" ON "{table_name}"',
            )
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"',
            )

    op.drop_index(
        "ix_push_notification_outbox_worker_queue",
        table_name=OUTBOX_TABLE,
    )
    op.drop_index(
        "ix_push_notification_outbox_user_id",
        table_name=OUTBOX_TABLE,
    )
    op.drop_index(
        "ix_push_notification_outbox_user_created",
        table_name=OUTBOX_TABLE,
    )
    op.drop_table(OUTBOX_TABLE)
    op.drop_index(
        "ix_push_installations_user_id",
        table_name=INSTALLATIONS_TABLE,
    )
    op.drop_index(
        "ix_push_installations_user_active",
        table_name=INSTALLATIONS_TABLE,
    )
    op.drop_index(
        "ix_push_installations_installation_id",
        table_name=INSTALLATIONS_TABLE,
    )
    op.drop_table(INSTALLATIONS_TABLE)

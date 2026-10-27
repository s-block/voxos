"""add durable attachment deletion

Revision ID: 7cd844e67d81
Revises: 6a020680226e
Create Date: 2026-07-27 20:02:30.952862

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7cd844e67d81"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "6a020680226e"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "message_attachments"
ATTACHMENT_WORKER_CLAUSE = "current_setting('app.service_scope', true) = 'attachment_worker'"
ATTACHMENT_WORKER_DELETED_CLAUSE = f"{ATTACHMENT_WORKER_CLAUSE} AND state = 'deleted'"


def upgrade() -> None:
    op.add_column(
        TABLE_NAME,
        sa.Column(
            "storage_deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_message_attachments_deletion_queue",
        TABLE_NAME,
        ("state", "storage_deleted_at", "deletion_attempts", "deleted_at"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_attachment_worker_select"
        ON "{TABLE_NAME}"
        FOR SELECT
        USING ({ATTACHMENT_WORKER_DELETED_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_attachment_worker_update"
        ON "{TABLE_NAME}"
        FOR UPDATE
        USING ({ATTACHMENT_WORKER_DELETED_CLAUSE})
        WITH CHECK ({ATTACHMENT_WORKER_DELETED_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_attachment_worker_update" ON "{TABLE_NAME}"',
        )
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_attachment_worker_select" ON "{TABLE_NAME}"',
        )
    op.drop_index(
        "ix_message_attachments_deletion_queue",
        table_name=TABLE_NAME,
    )
    op.drop_column(TABLE_NAME, "storage_deleted_at")

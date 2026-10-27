"""add voice worker warm requests

Revision ID: c0fdb7fcd673
Revises: 98930ac5cc6d
Create Date: 2026-08-27 05:57:51.050526

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0fdb7fcd673"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "98930ac5cc6d"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "voice_worker_warm_requests"
TENANT_CLAUSE = "user_id = current_setting('app.user_id', true)"
VOICE_WORKER_SCOPE_CLAUSE = "current_setting('app.service_scope', true) = 'voice_worker'"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_voice_worker_warm_requests_expires_at",
        TABLE_NAME,
        ("expires_at",),
    )
    op.create_index(
        "ix_voice_worker_warm_requests_project_expires",
        TABLE_NAME,
        ("project_key", "expires_at"),
    )
    op.create_index(
        "ix_voice_worker_warm_requests_project_key",
        TABLE_NAME,
        ("project_key",),
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
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_voice_worker_select"
        ON "{TABLE_NAME}"
        FOR SELECT
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_{TABLE_NAME}_voice_worker_delete"
        ON "{TABLE_NAME}"
        FOR DELETE
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_voice_worker_delete" ON "{TABLE_NAME}"',
        )
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_voice_worker_select" ON "{TABLE_NAME}"',
        )
        op.execute(
            f'DROP POLICY IF EXISTS "rls_{TABLE_NAME}_tenant" ON "{TABLE_NAME}"',
        )
    op.drop_index(
        "ix_voice_worker_warm_requests_project_key",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_voice_worker_warm_requests_project_expires",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_voice_worker_warm_requests_expires_at",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)

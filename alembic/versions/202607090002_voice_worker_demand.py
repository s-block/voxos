"""add demand-driven LiveKit voice worker routing

Revision ID: 202607090002
Revises: 202607090001
Create Date: 2026-07-09 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607090002"
down_revision: str | None = "202607090001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_CLAUSE = "user_id = current_setting('app.user_id', true)"
VOICE_WORKER_SCOPE_CLAUSE = "current_setting('app.service_scope', true) = 'voice_worker'"
LIVEKIT_SETTINGS_POLICY = "rls_user_livekit_settings_agent_worker"
LIVEKIT_VAULT_POLICY = "rls_user_secret_vault_livekit_agent_worker"


def upgrade() -> None:
    op.create_table(
        "voice_call_demands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_session_id", sa.String(length=36), nullable=False),
        sa.Column("room_name", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("worker_ready_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("project_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_type", sa.String(length=256), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'active', 'completed', 'failed', 'expired')",
            name="ck_voice_call_demands_status",
        ),
        sa.UniqueConstraint(
            "user_id",
            "runtime_session_id",
            name="uq_voice_call_demands_user_runtime_session",
        ),
    )
    op.create_index("ix_voice_call_demands_user_id", "voice_call_demands", ("user_id",))
    op.create_index("ix_voice_call_demands_agent_id", "voice_call_demands", ("agent_id",))
    op.create_index(
        "ix_voice_call_demands_project_key",
        "voice_call_demands",
        ("project_key",),
    )
    op.create_index(
        "ix_voice_call_demands_project_status_expires",
        "voice_call_demands",
        ("project_key", "status", "expires_at"),
    )
    op.create_index(
        "ix_voice_call_demands_user_status_updated",
        "voice_call_demands",
        ("user_id", "status", "updated_at"),
    )
    op.create_index(
        "ix_voice_call_demands_status_expires_requested",
        "voice_call_demands",
        ("status", "expires_at", "requested_at"),
    )
    op.create_index(
        "ix_voice_call_demands_status_updated",
        "voice_call_demands",
        ("status", "updated_at"),
    )

    op.create_table(
        "voice_project_worker_leases",
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("project_key"),
        sa.CheckConstraint(
            "status IN ('starting', 'ready')",
            name="ck_voice_project_worker_leases_status",
        ),
    )
    op.create_index(
        "ix_voice_project_worker_leases_owner_id",
        "voice_project_worker_leases",
        ("owner_id",),
    )
    op.create_index(
        "ix_voice_project_worker_leases_lease_expires_at",
        "voice_project_worker_leases",
        ("lease_expires_at",),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f'DROP POLICY IF EXISTS "{LIVEKIT_VAULT_POLICY}" ON "user_secret_vault"',
    )
    op.execute(
        f'DROP POLICY IF EXISTS "{LIVEKIT_SETTINGS_POLICY}" ON "user_livekit_settings"',
    )

    op.execute('ALTER TABLE "voice_call_demands" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "voice_call_demands" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_voice_call_demands_tenant"
        ON "voice_call_demands"
        FOR ALL
        USING ({TENANT_CLAUSE})
        WITH CHECK ({TENANT_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_voice_call_demands_voice_worker_select"
        ON "voice_call_demands"
        FOR SELECT
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_voice_call_demands_voice_worker_update"
        ON "voice_call_demands"
        FOR UPDATE
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        WITH CHECK ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )
    op.execute(
        f"""
        CREATE POLICY "rls_voice_call_demands_voice_worker_delete"
        ON "voice_call_demands"
        FOR DELETE
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )

    op.execute('ALTER TABLE "voice_project_worker_leases" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "voice_project_worker_leases" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_voice_project_worker_leases_voice_worker"
        ON "voice_project_worker_leases"
        FOR ALL
        USING ({VOICE_WORKER_SCOPE_CLAUSE})
        WITH CHECK ({VOICE_WORKER_SCOPE_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "rls_voice_project_worker_leases_voice_worker" ON "voice_project_worker_leases"',
        )
        op.execute(
            'DROP POLICY IF EXISTS "rls_voice_call_demands_voice_worker_delete" ON "voice_call_demands"',
        )
        op.execute(
            'DROP POLICY IF EXISTS "rls_voice_call_demands_voice_worker_update" ON "voice_call_demands"',
        )
        op.execute(
            'DROP POLICY IF EXISTS "rls_voice_call_demands_voice_worker_select" ON "voice_call_demands"',
        )
        op.execute(
            'DROP POLICY IF EXISTS "rls_voice_call_demands_tenant" ON "voice_call_demands"',
        )

    op.drop_index(
        "ix_voice_project_worker_leases_lease_expires_at",
        table_name="voice_project_worker_leases",
    )
    op.drop_index(
        "ix_voice_project_worker_leases_owner_id",
        table_name="voice_project_worker_leases",
    )
    op.drop_table("voice_project_worker_leases")
    op.drop_index(
        "ix_voice_call_demands_status_updated",
        table_name="voice_call_demands",
    )
    op.drop_index(
        "ix_voice_call_demands_status_expires_requested",
        table_name="voice_call_demands",
    )
    op.drop_index(
        "ix_voice_call_demands_user_status_updated",
        table_name="voice_call_demands",
    )
    op.drop_index(
        "ix_voice_call_demands_project_status_expires",
        table_name="voice_call_demands",
    )
    op.drop_index("ix_voice_call_demands_project_key", table_name="voice_call_demands")
    op.drop_index("ix_voice_call_demands_agent_id", table_name="voice_call_demands")
    op.drop_index("ix_voice_call_demands_user_id", table_name="voice_call_demands")
    op.drop_table("voice_call_demands")

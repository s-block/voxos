"""add codex cloud accounts and tasks

Revision ID: 679f69df3d03
Revises: 826e4de3de09
Create Date: 2026-08-20 14:54:25.552571

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "679f69df3d03"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "826e4de3de09"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "codex_accounts",
    "codex_auth_sessions",
    "codex_cloud_tasks",
)
TENANT_CLAUSE = "current_setting('app.user_id', true) = user_id"
AGENT_WORKER_CLAUSE = "current_setting('app.service_scope', true) = 'agent_worker'"


def upgrade() -> None:
    op.create_table(
        "codex_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("plan_type", sa.String(length=64), nullable=True),
        sa.Column("environment_id", sa.String(length=256), nullable=True),
        sa.Column("environment_label", sa.String(length=128), nullable=True),
        sa.Column("operation_lease_owner", sa.String(length=128), nullable=True),
        sa.Column("operation_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operation_generation", sa.Integer(), nullable=False),
        sa.Column("authenticated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation_generation >= 0",
            name="ck_codex_accounts_operation_generation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", name="uq_codex_accounts_user_id_id"),
        sa.UniqueConstraint("user_id", name="uq_codex_accounts_user_id"),
    )
    op.create_index(
        "ix_codex_accounts_operation_lease",
        "codex_accounts",
        ("operation_lease_expires_at",),
    )
    op.create_index("ix_codex_accounts_user_id", "codex_accounts", ("user_id",))
    op.create_index(
        "ix_codex_accounts_user_status",
        "codex_accounts",
        ("user_id", "status"),
    )

    op.create_table(
        "codex_auth_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("login_id", sa.String(length=128), nullable=True),
        sa.Column("verification_url", sa.String(length=2048), nullable=True),
        sa.Column("user_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ("user_id", "account_id"),
            ("codex_accounts.user_id", "codex_accounts.id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_codex_auth_sessions_claim",
        "codex_auth_sessions",
        ("status", "lease_expires_at", "created_at"),
    )
    op.create_index("ix_codex_auth_sessions_user_id", "codex_auth_sessions", ("user_id",))
    op.create_index(
        "ix_codex_auth_sessions_user_status",
        "codex_auth_sessions",
        ("user_id", "status"),
    )

    op.create_table(
        "codex_cloud_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("environment_id", sa.String(length=256), nullable=False),
        sa.Column("branch", sa.String(length=256), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("submission_state", sa.String(length=32), nullable=False),
        sa.Column("remote_task_id", sa.String(length=128), nullable=True),
        sa.Column("remote_task_url", sa.String(length=2048), nullable=True),
        sa.Column("remote_status", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remote_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempts BETWEEN 1 AND 4",
            name="ck_codex_cloud_tasks_attempts",
        ),
        sa.ForeignKeyConstraint(("agent_run_id",), ("agent_runs.id",), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ("user_id", "account_id"),
            ("codex_accounts.user_id", "codex_accounts.id"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", name="uq_codex_cloud_tasks_agent_run_id"),
    )
    op.create_index(
        "ix_codex_cloud_tasks_agent_run_id",
        "codex_cloud_tasks",
        ("agent_run_id",),
    )
    op.create_index(
        "ix_codex_cloud_tasks_remote",
        "codex_cloud_tasks",
        ("user_id", "remote_task_id"),
    )
    op.create_index(
        "ix_codex_cloud_tasks_user_created",
        "codex_cloud_tasks",
        ("user_id", "created_at"),
    )
    op.create_index("ix_codex_cloud_tasks_user_id", "codex_cloud_tasks", ("user_id",))

    if op.get_bind().dialect.name == "postgresql":
        _enable_tenant_rls()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "rls_codex_auth_sessions_agent_worker" ON "codex_auth_sessions"',
        )
        for table_name in reversed(TENANT_TABLES):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"',
            )
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_table("codex_cloud_tasks")
    op.drop_table("codex_auth_sessions")
    op.drop_table("codex_accounts")


def _enable_tenant_rls() -> None:
    for table_name in TENANT_TABLES:
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
        CREATE POLICY "rls_codex_auth_sessions_agent_worker"
        ON "codex_auth_sessions"
        FOR ALL
        USING ({AGENT_WORKER_CLAUSE})
        WITH CHECK ({AGENT_WORKER_CLAUSE})
        """,
    )

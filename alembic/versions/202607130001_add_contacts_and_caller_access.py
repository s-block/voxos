"""add contacts and caller access

Revision ID: 202607130001
Revises: 202607090002
Create Date: 2026-07-13 18:22:30.006927

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202607130001"
down_revision: str | Sequence[str] | None = "202607090002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTACTS_POLICY = "rls_user_contacts_tenant"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"


def upgrade() -> None:
    op.create_table(
        "user_contacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("phone_number", sa.String(length=16), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "phone_number",
            name="uq_user_contacts_user_phone_number",
        ),
    )
    op.create_index(
        "ix_user_contacts_user_display_name",
        "user_contacts",
        ("user_id", "display_name"),
    )
    op.create_index(
        "ix_user_contacts_user_id",
        "user_contacts",
        ("user_id",),
    )

    op.add_column(
        "agent_sessions",
        sa.Column(
            "effective_tool_policy_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column("contact_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column("contact_display_name", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column(
            "caller_is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_integration_call_sessions_contact_id",
        "integration_call_sessions",
        ("contact_id",),
    )
    op.create_index(
        "ix_integration_call_sessions_user_runtime_session",
        "integration_call_sessions",
        ("user_id", "runtime_session_id"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    op.alter_column(
        "agent_sessions",
        "effective_tool_policy_json",
        server_default=None,
    )
    op.alter_column(
        "integration_call_sessions",
        "caller_is_admin",
        server_default=None,
    )
    op.execute('ALTER TABLE "user_contacts" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "user_contacts" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{CONTACTS_POLICY}"
        ON "user_contacts"
        FOR ALL
        USING ({TENANT_CLAUSE})
        WITH CHECK ({TENANT_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "{CONTACTS_POLICY}" ON "user_contacts"',
        )

    op.drop_index(
        "ix_integration_call_sessions_user_runtime_session",
        table_name="integration_call_sessions",
    )
    op.drop_index(
        "ix_integration_call_sessions_contact_id",
        table_name="integration_call_sessions",
    )
    op.drop_column("integration_call_sessions", "caller_is_admin")
    op.drop_column("integration_call_sessions", "contact_display_name")
    op.drop_column("integration_call_sessions", "contact_id")
    op.drop_column("agent_sessions", "effective_tool_policy_json")
    op.drop_index("ix_user_contacts_user_id", table_name="user_contacts")
    op.drop_index(
        "ix_user_contacts_user_display_name",
        table_name="user_contacts",
    )
    op.drop_table("user_contacts")

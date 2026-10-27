"""add communication conversations and replies

Revision ID: e5f3de5e4e06
Revises: 202607130001
Create Date: 2026-07-15 09:00:19.564249

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f3de5e4e06"
down_revision: str | Sequence[str] | None = "202607130001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "integration_conversations",
    "integration_replies",
)
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"


def upgrade() -> None:
    op.create_table(
        "integration_conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("medium", sa.String(length=32), nullable=False),
        sa.Column("remote_peer", sa.String(length=32), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_session_id", sa.String(length=36), nullable=False),
        sa.Column("last_interaction_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "integration_id",
            "resource_id",
            "medium",
            "remote_peer",
            "agent_id",
            name="uq_integration_conversations_peer",
        ),
    )
    op.create_index(
        "ix_integration_conversations_agent_id",
        "integration_conversations",
        ("agent_id",),
    )
    op.create_index(
        "ix_integration_conversations_integration_id",
        "integration_conversations",
        ("integration_id",),
    )
    op.create_index(
        "ix_integration_conversations_resource_id",
        "integration_conversations",
        ("resource_id",),
    )
    op.create_index(
        "ix_integration_conversations_runtime_session_id",
        "integration_conversations",
        ("runtime_session_id",),
    )
    op.create_index(
        "ix_integration_conversations_user_expires",
        "integration_conversations",
        ("user_id", "expires_at"),
    )
    op.create_index(
        "ix_integration_conversations_user_id",
        "integration_conversations",
        ("user_id",),
    )
    op.create_index(
        "ix_integration_conversations_user_resource_peer",
        "integration_conversations",
        ("user_id", "resource_id", "medium", "remote_peer"),
    )

    op.create_table(
        "integration_replies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("inbound_message_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=True),
        sa.Column("outbound_message_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("response_profile", sa.String(length=64), nullable=False),
        sa.Column("generated_body", sa.Text(), nullable=True),
        sa.Column("encoding", sa.String(length=32), nullable=True),
        sa.Column("estimated_segments", sa.Integer(), nullable=True),
        sa.Column("condensation_attempted", sa.Boolean(), nullable=False),
        sa.Column("overflow_fallback_used", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "inbound_transcript_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "outbound_transcript_recorded_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "inbound_message_id",
            name="uq_integration_replies_inbound_message",
        ),
    )
    op.create_index(
        "ix_integration_replies_agent_run_id",
        "integration_replies",
        ("agent_run_id",),
    )
    op.create_index(
        "ix_integration_replies_conversation_id",
        "integration_replies",
        ("conversation_id",),
    )
    op.create_index(
        "ix_integration_replies_inbound_message_id",
        "integration_replies",
        ("inbound_message_id",),
    )
    op.create_index(
        "ix_integration_replies_outbound_message_id",
        "integration_replies",
        ("outbound_message_id",),
    )
    op.create_index(
        "ix_integration_replies_user_conversation_created",
        "integration_replies",
        ("user_id", "conversation_id", "created_at"),
    )
    op.create_index(
        "ix_integration_replies_user_id",
        "integration_replies",
        ("user_id",),
    )
    op.create_index(
        "ix_integration_replies_user_status_updated",
        "integration_replies",
        ("user_id", "status", "updated_at"),
    )

    if op.get_bind().dialect.name == "postgresql":
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


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table_name in reversed(TENANT_TABLES):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"',
            )
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    op.drop_table("integration_replies")
    op.drop_table("integration_conversations")

"""add multimodal chat attachments

Revision ID: 6a020680226e
Revises: 9329a3aa4885
Create Date: 2026-07-27 18:33:40.568726
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a020680226e"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "9329a3aa4885"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "message_attachments"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_id", sa.String(length=36), nullable=True),
        sa.Column("client_draft_id", sa.String(length=64), nullable=False),
        sa.Column("client_source", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("mime_type", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("thumbnail_key", sa.Text(), nullable=False),
        sa.Column("deletion_attempts", sa.Integer(), nullable=False),
        sa.Column("deletion_error", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "client_source IN ('web', 'flutter')",
            name="ck_message_attachments_client_source",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'ready', 'attached', 'deleted', 'failed')",
            name="ck_message_attachments_state",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_message_attachments_byte_size",
        ),
        sa.CheckConstraint("height > 0", name="ck_message_attachments_height"),
        sa.CheckConstraint(
            "position IS NULL OR position >= 0",
            name="ck_message_attachments_position",
        ),
        sa.CheckConstraint("width > 0", name="ck_message_attachments_width"),
        sa.ForeignKeyConstraint(("chat_id",), ("agent_sessions.id",)),
        sa.ForeignKeyConstraint(
            ("transcript_id",),
            ("agent_transcripts.id",),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transcript_id",
            "position",
            name="uq_message_attachments_transcript_position",
        ),
    )
    op.create_index(
        "ix_message_attachments_chat_id",
        TABLE_NAME,
        ("chat_id",),
    )
    op.create_index(
        "ix_message_attachments_state",
        TABLE_NAME,
        ("state",),
    )
    op.create_index(
        "ix_message_attachments_transcript_id",
        TABLE_NAME,
        ("transcript_id",),
    )
    op.create_index(
        "ix_message_attachments_user_chat_created_at",
        TABLE_NAME,
        ("user_id", "chat_id", "created_at"),
    )
    op.create_index(
        "ix_message_attachments_user_draft",
        TABLE_NAME,
        ("user_id", "chat_id", "client_draft_id"),
    )
    op.create_index(
        "ix_message_attachments_user_id",
        TABLE_NAME,
        ("user_id",),
    )
    op.create_index(
        "ix_message_attachments_user_state_created_at",
        TABLE_NAME,
        ("user_id", "state", "created_at"),
    )
    op.create_index(
        "ix_message_attachments_user_transcript",
        TABLE_NAME,
        ("user_id", "transcript_id"),
    )
    op.add_column(
        "agent_transcripts",
        sa.Column("client_turn_id", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_agent_transcripts_user_session_client_turn",
        "agent_transcripts",
        ("user_id", "session_id", "client_turn_id"),
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
    op.drop_constraint(
        "uq_agent_transcripts_user_session_client_turn",
        "agent_transcripts",
        type_="unique",
    )
    op.drop_column("agent_transcripts", "client_turn_id")
    op.drop_index(
        "ix_message_attachments_user_transcript",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_message_attachments_user_state_created_at",
        table_name=TABLE_NAME,
    )
    op.drop_index("ix_message_attachments_user_id", table_name=TABLE_NAME)
    op.drop_index(
        "ix_message_attachments_user_draft",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_message_attachments_user_chat_created_at",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_message_attachments_transcript_id",
        table_name=TABLE_NAME,
    )
    op.drop_index("ix_message_attachments_state", table_name=TABLE_NAME)
    op.drop_index("ix_message_attachments_chat_id", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)

"""add WhatsApp linked-device sessions

Revision ID: 398ea89de8ce
Revises: e5f3de5e4e06
Create Date: 2026-07-20 17:16:27.649778

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "398ea89de8ce"
down_revision: str | Sequence[str] | None = "e5f3de5e4e06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"
WHATSAPP_WORKER_SCOPE_CLAUSE = "current_setting('app.service_scope', true) = 'whatsapp_worker'"
TENANT_TABLES = (
    "whatsapp_sessions",
    "whatsapp_session_keys",
    "whatsapp_outbox",
)
WORKER_READ_WRITE_TABLES = (
    "whatsapp_sessions",
    "whatsapp_outbox",
)


def upgrade() -> None:
    op.create_table(
        "whatsapp_outbox",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("reply_id", sa.String(length=36), nullable=False),
        sa.Column("target_jid", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=True),
        sa.Column("lease_generation", sa.Integer(), nullable=True),
        sa.Column("external_message_id", sa.String(length=256), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "submission_started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'submitting', 'sent', 'failed', 'ambiguous')",
            name="ck_whatsapp_outbox_status",
        ),
        sa.CheckConstraint(
            "lease_generation IS NULL OR lease_generation >= 1",
            name="ck_whatsapp_outbox_lease_generation",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reply_id",
            name="uq_whatsapp_outbox_reply",
        ),
    )
    op.create_index(
        "ix_whatsapp_outbox_integration_id",
        "whatsapp_outbox",
        ("integration_id",),
    )
    op.create_index(
        "ix_whatsapp_outbox_reply_id",
        "whatsapp_outbox",
        ("reply_id",),
    )
    op.create_index(
        "ix_whatsapp_outbox_resource_id",
        "whatsapp_outbox",
        ("resource_id",),
    )
    op.create_index(
        "ix_whatsapp_outbox_user_id",
        "whatsapp_outbox",
        ("user_id",),
    )
    op.create_index(
        "ix_whatsapp_outbox_user_integration",
        "whatsapp_outbox",
        ("user_id", "integration_id"),
    )
    op.create_index(
        "ix_whatsapp_outbox_worker_queue",
        "whatsapp_outbox",
        ("status", "created_at"),
    )

    op.create_table(
        "whatsapp_session_keys",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("key_category", sa.String(length=64), nullable=False),
        sa.Column("logical_key_id", sa.String(length=512), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_whatsapp_session_keys_revision",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "integration_id",
            "key_category",
            "logical_key_id",
            name="uq_whatsapp_session_keys_logical",
        ),
    )
    op.create_index(
        "ix_whatsapp_session_keys_integration_id",
        "whatsapp_session_keys",
        ("integration_id",),
    )
    op.create_index(
        "ix_whatsapp_session_keys_user_id",
        "whatsapp_session_keys",
        ("user_id",),
    )
    op.create_index(
        "ix_whatsapp_session_keys_user_integration",
        "whatsapp_session_keys",
        ("user_id", "integration_id"),
    )

    op.create_table(
        "whatsapp_session_leases",
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column(
            "lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "generation >= 1",
            name="ck_whatsapp_session_leases_generation",
        ),
        sa.PrimaryKeyConstraint("integration_id"),
    )
    op.create_index(
        "ix_whatsapp_session_leases_expiry",
        "whatsapp_session_leases",
        ("lease_expires_at",),
    )
    op.create_index(
        "ix_whatsapp_session_leases_user",
        "whatsapp_session_leases",
        ("user_id",),
    )

    op.create_table(
        "whatsapp_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("source_resource_id", sa.String(length=36), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=False),
        sa.Column("desired_state", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("link_method", sa.String(length=32), nullable=False),
        sa.Column("masked_phone_number", sa.String(length=32), nullable=False),
        sa.Column("challenge_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("challenge_nonce", sa.LargeBinary(), nullable=True),
        sa.Column("challenge_revision", sa.Integer(), nullable=False),
        sa.Column("challenge_request_revision", sa.Integer(), nullable=False),
        sa.Column(
            "challenge_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "validation_fingerprint",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "desired_state IN ('connected', 'stopped')",
            name="ck_whatsapp_sessions_desired_state",
        ),
        sa.CheckConstraint(
            "state IN ("
            "'awaiting_link', 'connecting', 'connected', 'reconnecting', "
            "'waiting_for_capacity', 'needs_relink', 'disconnecting', "
            "'failed', 'disabled'"
            ")",
            name="ck_whatsapp_sessions_state",
        ),
        sa.CheckConstraint(
            "link_method IN ('qr', 'pairing_code')",
            name="ck_whatsapp_sessions_link_method",
        ),
        sa.CheckConstraint(
            "challenge_revision >= 0",
            name="ck_whatsapp_sessions_challenge_revision",
        ),
        sa.CheckConstraint(
            "challenge_request_revision >= 1",
            name="ck_whatsapp_sessions_challenge_request_revision",
        ),
        sa.CheckConstraint(
            "(challenge_ciphertext IS NULL AND challenge_nonce IS NULL) OR "
            "(challenge_ciphertext IS NOT NULL AND challenge_nonce IS NOT NULL)",
            name="ck_whatsapp_sessions_challenge_pair",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "integration_id",
            name="uq_whatsapp_sessions_integration",
        ),
        sa.UniqueConstraint(
            "resource_id",
            name="uq_whatsapp_sessions_resource",
        ),
        sa.UniqueConstraint(
            "user_id",
            "source_resource_id",
            name="uq_whatsapp_sessions_source_resource",
        ),
        sa.UniqueConstraint(
            "user_id",
            name="uq_whatsapp_sessions_user",
        ),
    )
    op.create_index(
        "ix_whatsapp_sessions_integration_id",
        "whatsapp_sessions",
        ("integration_id",),
    )
    op.create_index(
        "ix_whatsapp_sessions_resource_id",
        "whatsapp_sessions",
        ("resource_id",),
    )
    op.create_index(
        "ix_whatsapp_sessions_source_resource_id",
        "whatsapp_sessions",
        ("source_resource_id",),
    )
    op.create_index(
        "ix_whatsapp_sessions_user_id",
        "whatsapp_sessions",
        ("user_id",),
    )
    op.create_index(
        "ix_whatsapp_sessions_worker_demand",
        "whatsapp_sessions",
        ("desired_state", "state", "updated_at"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

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

    for table_name in WORKER_READ_WRITE_TABLES:
        op.execute(
            f"""
            CREATE POLICY "rls_{table_name}_whatsapp_worker_select"
            ON "{table_name}"
            FOR SELECT
            USING ({WHATSAPP_WORKER_SCOPE_CLAUSE})
            """,
        )
        op.execute(
            f"""
            CREATE POLICY "rls_{table_name}_whatsapp_worker_update"
            ON "{table_name}"
            FOR UPDATE
            USING ({WHATSAPP_WORKER_SCOPE_CLAUSE})
            WITH CHECK ({WHATSAPP_WORKER_SCOPE_CLAUSE})
            """,
        )

    op.execute(
        'ALTER TABLE "whatsapp_session_leases" ENABLE ROW LEVEL SECURITY',
    )
    op.execute(
        'ALTER TABLE "whatsapp_session_leases" FORCE ROW LEVEL SECURITY',
    )
    op.execute(
        f"""
        CREATE POLICY "rls_whatsapp_session_leases_whatsapp_worker"
        ON "whatsapp_session_leases"
        FOR ALL
        USING ({WHATSAPP_WORKER_SCOPE_CLAUSE})
        WITH CHECK ({WHATSAPP_WORKER_SCOPE_CLAUSE})
        """,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "rls_whatsapp_session_leases_whatsapp_worker" ON "whatsapp_session_leases"',
        )
        for table_name in reversed(WORKER_READ_WRITE_TABLES):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_whatsapp_worker_update" ON "{table_name}"',
            )
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_whatsapp_worker_select" ON "{table_name}"',
            )
        for table_name in reversed(TENANT_TABLES):
            op.execute(
                f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"',
            )

    op.drop_index(
        "ix_whatsapp_sessions_worker_demand",
        table_name="whatsapp_sessions",
    )
    op.drop_index(
        "ix_whatsapp_sessions_user_id",
        table_name="whatsapp_sessions",
    )
    op.drop_index(
        "ix_whatsapp_sessions_source_resource_id",
        table_name="whatsapp_sessions",
    )
    op.drop_index(
        "ix_whatsapp_sessions_resource_id",
        table_name="whatsapp_sessions",
    )
    op.drop_index(
        "ix_whatsapp_sessions_integration_id",
        table_name="whatsapp_sessions",
    )
    op.drop_table("whatsapp_sessions")

    op.drop_index(
        "ix_whatsapp_session_leases_user",
        table_name="whatsapp_session_leases",
    )
    op.drop_index(
        "ix_whatsapp_session_leases_expiry",
        table_name="whatsapp_session_leases",
    )
    op.drop_table("whatsapp_session_leases")

    op.drop_index(
        "ix_whatsapp_session_keys_user_integration",
        table_name="whatsapp_session_keys",
    )
    op.drop_index(
        "ix_whatsapp_session_keys_user_id",
        table_name="whatsapp_session_keys",
    )
    op.drop_index(
        "ix_whatsapp_session_keys_integration_id",
        table_name="whatsapp_session_keys",
    )
    op.drop_table("whatsapp_session_keys")

    op.drop_index(
        "ix_whatsapp_outbox_worker_queue",
        table_name="whatsapp_outbox",
    )
    op.drop_index(
        "ix_whatsapp_outbox_user_integration",
        table_name="whatsapp_outbox",
    )
    op.drop_index(
        "ix_whatsapp_outbox_user_id",
        table_name="whatsapp_outbox",
    )
    op.drop_index(
        "ix_whatsapp_outbox_resource_id",
        table_name="whatsapp_outbox",
    )
    op.drop_index(
        "ix_whatsapp_outbox_reply_id",
        table_name="whatsapp_outbox",
    )
    op.drop_index(
        "ix_whatsapp_outbox_integration_id",
        table_name="whatsapp_outbox",
    )
    op.drop_table("whatsapp_outbox")

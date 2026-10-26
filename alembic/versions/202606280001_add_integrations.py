"""add integrations and twilio support

Revision ID: 202606280001
Revises: 202606270005
Create Date: 2026-06-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606280001"
down_revision: str | None = "202606270005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "user_integrations",
    "integration_credentials",
    "integration_resources",
    "integration_webhook_routes",
    "integration_events",
    "integration_messages",
    "integration_call_sessions",
    "integration_call_transcripts",
    "integration_audit_logs",
)


def upgrade() -> None:
    prepare_agent_tool_grants_for_integrations()
    deduplicate_agent_tool_grants()
    op.create_index(
        "uq_agent_tool_grants_internal_tool",
        "agent_tool_grants",
        (
            "user_id",
            "agent_id",
            "tool_source",
            "tool_name",
        ),
        unique=True,
        **partial_index_kwargs(
            "tool_source = 'internal' AND mcp_server_id IS NULL AND integration_id IS NULL",
        ),
    )
    op.create_index(
        "uq_agent_tool_grants_mcp_tool",
        "agent_tool_grants",
        (
            "user_id",
            "agent_id",
            "tool_source",
            "mcp_server_id",
            "tool_name",
        ),
        unique=True,
        **partial_index_kwargs(
            "tool_source = 'mcp_server' AND mcp_server_id IS NOT NULL AND integration_id IS NULL",
        ),
    )
    op.create_index(
        "uq_agent_tool_grants_integration_tool",
        "agent_tool_grants",
        (
            "user_id",
            "agent_id",
            "tool_source",
            "integration_id",
            "tool_name",
        ),
        unique=True,
        **partial_index_kwargs(
            "tool_source = 'integration' AND mcp_server_id IS NULL AND integration_id IS NOT NULL",
        ),
    )

    op.create_table(
        "user_integrations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("auth_type", sa.String(length=64), nullable=False),
        sa.Column("setup_state", sa.String(length=64), nullable=False),
        sa.Column("default_agent_id", sa.String(length=36), nullable=True),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_integrations_user_id", "user_integrations", ("user_id",))
    op.create_index(
        "ix_user_integrations_user_provider_status",
        "user_integrations",
        ("user_id", "provider", "status"),
    )
    op.create_index(
        "ix_user_integrations_user_enabled",
        "user_integrations",
        ("user_id", "enabled"),
    )
    op.create_index(
        "uq_user_integrations_active_label",
        "user_integrations",
        ("user_id", "label"),
        unique=True,
        **partial_index_kwargs("deleted_at IS NULL"),
    )

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("secret_kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("public_identifier", sa.String(length=256), nullable=True),
        sa.Column("last_four", sa.String(length=8), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_credentials_user_id", "integration_credentials", ("user_id",))
    op.create_index("ix_integration_credentials_integration_id", "integration_credentials", ("integration_id",))
    op.create_index(
        "ix_integration_credentials_user_integration",
        "integration_credentials",
        ("user_id", "integration_id"),
    )
    op.create_index(
        "ix_integration_credentials_user_kind",
        "integration_credentials",
        ("user_id", "provider", "secret_kind"),
    )

    op.create_table(
        "integration_resources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("retained_metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_resources_user_id", "integration_resources", ("user_id",))
    op.create_index("ix_integration_resources_integration_id", "integration_resources", ("integration_id",))
    op.create_index(
        "ix_integration_resources_user_integration",
        "integration_resources",
        ("user_id", "integration_id"),
    )
    op.create_index(
        "ix_integration_resources_user_status",
        "integration_resources",
        ("user_id", "provider", "status"),
    )
    op.create_index(
        "uq_integration_resources_active_external",
        "integration_resources",
        ("user_id", "provider", "resource_type", "external_id"),
        unique=True,
        **partial_index_kwargs("deleted_at IS NULL"),
    )

    op.create_table(
        "integration_webhook_routes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("route_key_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "route_key_hash", name="uq_integration_webhook_route_hash"),
    )
    op.create_index("ix_integration_webhook_routes_user_id", "integration_webhook_routes", ("user_id",))
    op.create_index("ix_integration_webhook_routes_integration_id", "integration_webhook_routes", ("integration_id",))
    op.create_index("ix_integration_webhook_routes_resource_id", "integration_webhook_routes", ("resource_id",))
    op.create_index(
        "ix_integration_webhook_routes_user_integration",
        "integration_webhook_routes",
        ("user_id", "integration_id"),
    )
    op.create_index(
        "ix_integration_webhook_routes_resource_purpose",
        "integration_webhook_routes",
        ("resource_id", "purpose"),
    )
    op.create_index(
        "uq_integration_webhook_routes_active_resource_purpose",
        "integration_webhook_routes",
        ("provider", "resource_id", "purpose"),
        unique=True,
        **partial_index_kwargs("status = 'active'"),
    )

    op.create_table(
        "integration_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("external_event_id", sa.String(length=256), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("safe_summary_json", sa.JSON(), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "idempotency_key", name="uq_integration_events_idempotency"),
    )
    op.create_index("ix_integration_events_user_id", "integration_events", ("user_id",))
    op.create_index("ix_integration_events_integration_id", "integration_events", ("integration_id",))
    op.create_index("ix_integration_events_resource_id", "integration_events", ("resource_id",))
    op.create_index(
        "ix_integration_events_user_integration_created",
        "integration_events",
        ("user_id", "integration_id", "created_at"),
    )
    op.create_index(
        "ix_integration_events_user_resource_created",
        "integration_events",
        ("user_id", "resource_id", "created_at"),
    )

    op.create_table(
        "integration_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("external_message_id", sa.String(length=256), nullable=False),
        sa.Column("from_number", sa.String(length=32), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("segments", sa.Integer(), nullable=True),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("price_unit", sa.String(length=16), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "external_message_id", name="uq_integration_messages_external"),
    )
    op.create_index("ix_integration_messages_user_id", "integration_messages", ("user_id",))
    op.create_index("ix_integration_messages_integration_id", "integration_messages", ("integration_id",))
    op.create_index("ix_integration_messages_resource_id", "integration_messages", ("resource_id",))
    op.create_index(
        "ix_integration_messages_user_resource_created",
        "integration_messages",
        ("user_id", "resource_id", "created_at"),
    )

    op.create_table(
        "integration_call_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("external_call_id", sa.String(length=256), nullable=False),
        sa.Column("from_number", sa.String(length=32), nullable=True),
        sa.Column("to_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("runtime_session_id", sa.String(length=36), nullable=True),
        sa.Column("livekit_room_name", sa.String(length=512), nullable=True),
        sa.Column("media_stream_id", sa.String(length=256), nullable=True),
        sa.Column("transcript_enabled", sa.Boolean(), nullable=False),
        sa.Column("transcript_status", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "provider", "external_call_id", name="uq_integration_call_sessions_external"),
    )
    op.create_index("ix_integration_call_sessions_user_id", "integration_call_sessions", ("user_id",))
    op.create_index("ix_integration_call_sessions_integration_id", "integration_call_sessions", ("integration_id",))
    op.create_index("ix_integration_call_sessions_resource_id", "integration_call_sessions", ("resource_id",))
    op.create_index("ix_integration_call_sessions_agent_id", "integration_call_sessions", ("agent_id",))
    op.create_index(
        "ix_integration_call_sessions_user_resource_created",
        "integration_call_sessions",
        ("user_id", "resource_id", "created_at"),
    )

    op.create_table(
        "integration_call_transcripts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("call_session_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("speaker", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_call_transcripts_user_id", "integration_call_transcripts", ("user_id",))
    op.create_index(
        "ix_integration_call_transcripts_integration_id",
        "integration_call_transcripts",
        ("integration_id",),
    )
    op.create_index(
        "ix_integration_call_transcripts_call_session_id",
        "integration_call_transcripts",
        ("call_session_id",),
    )
    op.create_index(
        "ix_integration_call_transcripts_user_call_created",
        "integration_call_transcripts",
        ("user_id", "call_session_id", "created_at"),
    )

    op.create_table(
        "integration_audit_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("actor_type", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=True),
        sa.Column("outcome", sa.String(length=64), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_audit_logs_user_id", "integration_audit_logs", ("user_id",))
    op.create_index("ix_integration_audit_logs_integration_id", "integration_audit_logs", ("integration_id",))
    op.create_index(
        "ix_integration_audit_user_integration_created",
        "integration_audit_logs",
        ("user_id", "integration_id", "created_at"),
    )
    op.create_index(
        "ix_integration_audit_user_action_created",
        "integration_audit_logs",
        ("user_id", "action", "created_at"),
    )

    if op.get_bind().dialect.name == "postgresql":
        for table_name in TENANT_TABLES:
            create_tenant_policy(table_name)
            op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
        op.execute(
            """
            CREATE POLICY "rls_integration_webhook_routes_integration_webhook"
            ON "integration_webhook_routes"
            FOR SELECT
            USING (current_setting('app.service_scope', true) = 'integration_webhook')
            """,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            'DROP POLICY IF EXISTS "rls_integration_webhook_routes_integration_webhook" '
            'ON "integration_webhook_routes"',
        )
        for table_name in TENANT_TABLES:
            op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
            op.execute(f'DROP POLICY IF EXISTS "rls_{table_name}_tenant" ON "{table_name}"')
            op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')

    for table_name in reversed(TENANT_TABLES):
        op.drop_table(table_name)

    op.drop_index(
        "uq_agent_tool_grants_integration_tool",
        table_name="agent_tool_grants",
    )
    op.drop_index(
        "uq_agent_tool_grants_mcp_tool",
        table_name="agent_tool_grants",
    )
    op.drop_index(
        "uq_agent_tool_grants_internal_tool",
        table_name="agent_tool_grants",
    )
    restore_agent_tool_grants_unique_constraint()


def create_tenant_policy(table_name: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "rls_{table_name}_tenant"
        ON "{table_name}"
        USING ("user_id" = current_setting('app.user_id', true))
        WITH CHECK ("user_id" = current_setting('app.user_id', true))
        """,
    )


def prepare_agent_tool_grants_for_integrations() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("agent_tool_grants") as batch_op:
            batch_op.add_column(sa.Column("integration_id", sa.String(length=36), nullable=True))
            batch_op.drop_constraint(
                "uq_agent_tool_grants_scope_tool",
                type_="unique",
            )
        with op.batch_alter_table("agent_tool_audit_events") as batch_op:
            batch_op.add_column(sa.Column("integration_id", sa.String(length=36), nullable=True))
        return

    op.add_column(
        "agent_tool_grants",
        sa.Column("integration_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "agent_tool_audit_events",
        sa.Column("integration_id", sa.String(length=36), nullable=True),
    )
    op.drop_constraint(
        "uq_agent_tool_grants_scope_tool",
        "agent_tool_grants",
        type_="unique",
    )


def restore_agent_tool_grants_unique_constraint() -> None:
    columns = (
        "user_id",
        "agent_id",
        "tool_source",
        "mcp_server_id",
        "tool_name",
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("agent_tool_grants") as batch_op:
            batch_op.create_unique_constraint(
                "uq_agent_tool_grants_scope_tool",
                columns,
            )
            batch_op.drop_column("integration_id")
        with op.batch_alter_table("agent_tool_audit_events") as batch_op:
            batch_op.drop_column("integration_id")
        return

    op.create_unique_constraint(
        "uq_agent_tool_grants_scope_tool",
        "agent_tool_grants",
        columns,
    )
    op.drop_column("agent_tool_audit_events", "integration_id")
    op.drop_column("agent_tool_grants", "integration_id")


def deduplicate_agent_tool_grants() -> None:
    op.execute(
        """
        DELETE FROM agent_tool_grants
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY
                            user_id,
                            agent_id,
                            tool_source,
                            COALESCE(mcp_server_id, ''),
                            COALESCE(integration_id, ''),
                            tool_name
                        ORDER BY updated_at DESC, created_at DESC, id DESC
                    ) AS duplicate_rank
                FROM agent_tool_grants
            ) ranked_tool_grants
            WHERE duplicate_rank > 1
        )
        """,
    )


def partial_index_kwargs(where_clause: str) -> dict[str, object]:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "postgresql":
        return {"postgresql_where": sa.text(where_clause)}
    if dialect_name == "sqlite":
        return {"sqlite_where": sa.text(where_clause)}
    return {}

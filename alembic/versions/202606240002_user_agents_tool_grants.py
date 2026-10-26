"""add user agents and tool grants

Revision ID: 202606240002
Revises: 202606240001
Create Date: 2026-06-24 16:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "202606240002"
down_revision: str | None = "202606240001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RUNTIME_TABLES = (
    "agent_sessions",
    "agent_transcripts",
    "agent_tool_events",
    "agent_usage_events",
    "agent_runtime_events",
    "agent_reflection_jobs",
    "agent_reflection_proposals",
)


def upgrade() -> None:
    op.create_table(
        "user_agents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.String(length=256), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", "slug", name="uq_user_agents_owner_slug"),
    )
    op.create_index("ix_user_agents_owner_user_id", "user_agents", ["owner_user_id"])
    op.create_index(
        "ix_user_agents_owner_status",
        "user_agents",
        ["owner_user_id", "status"],
    )
    op.create_index(
        "ix_user_agents_owner_default",
        "user_agents",
        ["owner_user_id", "is_default"],
    )

    for table_name in RUNTIME_TABLES:
        op.add_column(
            table_name,
            sa.Column("agent_id", sa.String(length=36), nullable=True),
        )
        op.create_index(f"ix_{table_name}_agent_id", table_name, ["agent_id"])

    op.create_index(
        "ix_agent_sessions_user_agent_status",
        "agent_sessions",
        ["user_id", "agent_id", "status"],
    )
    op.create_index(
        "ix_agent_sessions_user_agent_started_at",
        "agent_sessions",
        ["user_id", "agent_id", "started_at"],
    )
    op.create_index(
        "ix_agent_transcripts_user_agent_created_at",
        "agent_transcripts",
        ["user_id", "agent_id", "created_at"],
    )
    op.create_index(
        "ix_agent_tool_events_user_agent_name",
        "agent_tool_events",
        ["user_id", "agent_id", "name"],
    )
    op.create_index(
        "ix_agent_usage_events_user_agent_type",
        "agent_usage_events",
        ["user_id", "agent_id", "usage_type"],
    )
    op.create_index(
        "ix_agent_runtime_events_user_agent_kind",
        "agent_runtime_events",
        ["user_id", "agent_id", "kind"],
    )
    op.create_index(
        "ix_agent_reflection_jobs_user_agent_status",
        "agent_reflection_jobs",
        ["user_id", "agent_id", "status"],
    )
    op.create_index(
        "ix_agent_reflection_proposals_user_agent_status",
        "agent_reflection_proposals",
        ["user_id", "agent_id", "status"],
    )

    op.create_table(
        "agent_tool_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=256), nullable=False),
        sa.Column("tool_source", sa.String(length=32), nullable=False),
        sa.Column("mcp_server_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("risk", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "agent_id",
            "tool_source",
            "mcp_server_id",
            "tool_name",
            name="uq_agent_tool_grants_scope_tool",
        ),
    )
    op.create_index("ix_agent_tool_grants_user_id", "agent_tool_grants", ["user_id"])
    op.create_index("ix_agent_tool_grants_agent_id", "agent_tool_grants", ["agent_id"])
    op.create_index(
        "ix_agent_tool_grants_tool_name",
        "agent_tool_grants",
        ["tool_name"],
    )
    op.create_index(
        "ix_agent_tool_grants_user_agent",
        "agent_tool_grants",
        ["user_id", "agent_id"],
    )
    op.create_index(
        "ix_agent_tool_grants_user_agent_source",
        "agent_tool_grants",
        ["user_id", "agent_id", "tool_source"],
    )

    op.create_table(
        "agent_tool_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("tool_name", sa.String(length=256), nullable=False),
        sa.Column("tool_source", sa.String(length=32), nullable=False),
        sa.Column("mcp_server_id", sa.String(length=36), nullable=True),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=512), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_tool_audit_events_user_id",
        "agent_tool_audit_events",
        ["user_id"],
    )
    op.create_index(
        "ix_agent_tool_audit_events_agent_id",
        "agent_tool_audit_events",
        ["agent_id"],
    )
    op.create_index(
        "ix_agent_tool_audit_events_session_id",
        "agent_tool_audit_events",
        ["session_id"],
    )
    op.create_index(
        "ix_agent_tool_audit_events_tool_name",
        "agent_tool_audit_events",
        ["tool_name"],
    )
    op.create_index(
        "ix_agent_tool_audit_user_agent_created",
        "agent_tool_audit_events",
        ["user_id", "agent_id", "created_at"],
    )
    op.create_index(
        "ix_agent_tool_audit_user_tool",
        "agent_tool_audit_events",
        ["user_id", "tool_name"],
    )

    backfill_default_agents()


def downgrade() -> None:
    op.drop_index("ix_agent_tool_audit_user_tool", table_name="agent_tool_audit_events")
    op.drop_index(
        "ix_agent_tool_audit_user_agent_created",
        table_name="agent_tool_audit_events",
    )
    op.drop_index(
        "ix_agent_tool_audit_events_tool_name",
        table_name="agent_tool_audit_events",
    )
    op.drop_index(
        "ix_agent_tool_audit_events_session_id",
        table_name="agent_tool_audit_events",
    )
    op.drop_index(
        "ix_agent_tool_audit_events_agent_id",
        table_name="agent_tool_audit_events",
    )
    op.drop_index(
        "ix_agent_tool_audit_events_user_id",
        table_name="agent_tool_audit_events",
    )
    op.drop_table("agent_tool_audit_events")

    op.drop_index(
        "ix_agent_tool_grants_user_agent_source",
        table_name="agent_tool_grants",
    )
    op.drop_index("ix_agent_tool_grants_user_agent", table_name="agent_tool_grants")
    op.drop_index("ix_agent_tool_grants_tool_name", table_name="agent_tool_grants")
    op.drop_index("ix_agent_tool_grants_agent_id", table_name="agent_tool_grants")
    op.drop_index("ix_agent_tool_grants_user_id", table_name="agent_tool_grants")
    op.drop_table("agent_tool_grants")

    op.drop_index(
        "ix_agent_reflection_proposals_user_agent_status",
        table_name="agent_reflection_proposals",
    )
    op.drop_index(
        "ix_agent_reflection_jobs_user_agent_status",
        table_name="agent_reflection_jobs",
    )
    op.drop_index(
        "ix_agent_runtime_events_user_agent_kind",
        table_name="agent_runtime_events",
    )
    op.drop_index(
        "ix_agent_usage_events_user_agent_type",
        table_name="agent_usage_events",
    )
    op.drop_index(
        "ix_agent_tool_events_user_agent_name",
        table_name="agent_tool_events",
    )
    op.drop_index(
        "ix_agent_transcripts_user_agent_created_at",
        table_name="agent_transcripts",
    )
    op.drop_index(
        "ix_agent_sessions_user_agent_started_at",
        table_name="agent_sessions",
    )
    op.drop_index(
        "ix_agent_sessions_user_agent_status",
        table_name="agent_sessions",
    )

    for table_name in reversed(RUNTIME_TABLES):
        op.drop_index(f"ix_{table_name}_agent_id", table_name=table_name)
        op.drop_column(table_name, "agent_id")

    op.drop_index("ix_user_agents_owner_default", table_name="user_agents")
    op.drop_index("ix_user_agents_owner_status", table_name="user_agents")
    op.drop_index("ix_user_agents_owner_user_id", table_name="user_agents")
    op.drop_table("user_agents")


def backfill_default_agents() -> None:
    connection = op.get_bind()
    users = sa.table(
        "auth_firebase_users",
        sa.column("id", sa.String(length=36)),
    )
    user_agents = sa.table(
        "user_agents",
        sa.column("id", sa.String(length=36)),
        sa.column("owner_user_id", sa.String(length=256)),
        sa.column("slug", sa.String(length=128)),
        sa.column("display_name", sa.String(length=256)),
        sa.column("description", sa.Text()),
        sa.column("status", sa.String(length=32)),
        sa.column("is_default", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    agent_ids_by_user_id: dict[str, str] = {}
    for row in connection.execute(sa.select(users.c.id)):
        user_id = str(row.id)
        agent_id = str(uuid4())
        agent_ids_by_user_id[user_id] = agent_id
        connection.execute(
            user_agents.insert().values(
                id=agent_id,
                owner_user_id=user_id,
                slug="default",
                display_name="Voxos",
                description=None,
                status="active",
                is_default=True,
                created_at=now,
                updated_at=now,
            ),
        )

    for table_name in RUNTIME_TABLES:
        table = sa.table(
            table_name,
            sa.column("user_id", sa.String(length=256)),
            sa.column("agent_id", sa.String(length=36)),
        )
        for user_id, agent_id in agent_ids_by_user_id.items():
            connection.execute(
                table.update().where(table.c.user_id == user_id).values(agent_id=agent_id),
            )

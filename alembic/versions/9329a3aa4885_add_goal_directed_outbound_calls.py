"""add goal directed outbound calls

Revision ID: 9329a3aa4885
Revises: 9d3048bd7659
Create Date: 2026-07-27 11:20:17.791240

"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.selectable import TableClause

revision: str = "9329a3aa4885"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "9d3048bd7659"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_REASON = "outbound_call_contact_search_prerequisite_backfill"
CONTACT_SEARCH_TOOL = "search_contacts"
PLACE_CALL_TOOL_SUFFIX = "twilio_place_call"


def upgrade() -> None:
    op.add_column(
        "integration_call_sessions",
        sa.Column("initiating_run_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column(
            "media_participant_identity",
            sa.String(length=128),
            nullable=True,
        ),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column("outbound_objective", sa.Text(), nullable=True),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column("opening_state", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "integration_call_sessions",
        sa.Column("opening_attempt_count", sa.Integer(), nullable=True),
    )
    for column_name in (
        "worker_registered_at",
        "agent_ready_at",
        "provider_dial_requested_at",
        "media_ready_at",
        "opening_claimed_at",
        "opening_scheduled_at",
        "first_agent_speech_at",
        "opening_completed_at",
    ):
        op.add_column(
            "integration_call_sessions",
            sa.Column(column_name, sa.DateTime(timezone=True), nullable=True),
        )
    op.create_index(
        "ix_integration_call_sessions_initiating_run_id",
        "integration_call_sessions",
        ("initiating_run_id",),
    )
    op.create_check_constraint(
        "ck_integration_call_sessions_opening_state",
        "integration_call_sessions",
        "opening_state IS NULL OR opening_state IN "
        "('pending', 'claimed', 'scheduled', 'completed', 'interrupted', 'failed')",
    )
    backfill_contact_search_grants()


def downgrade() -> None:
    remove_unchanged_contact_search_backfill()
    op.drop_constraint(
        "ck_integration_call_sessions_opening_state",
        "integration_call_sessions",
        type_="check",
    )
    op.drop_index(
        "ix_integration_call_sessions_initiating_run_id",
        table_name="integration_call_sessions",
    )
    for column_name in (
        "opening_completed_at",
        "first_agent_speech_at",
        "opening_scheduled_at",
        "opening_claimed_at",
        "media_ready_at",
        "provider_dial_requested_at",
        "agent_ready_at",
        "worker_registered_at",
        "opening_attempt_count",
        "opening_state",
        "outbound_objective",
        "media_participant_identity",
        "initiating_run_id",
    ):
        op.drop_column("integration_call_sessions", column_name)


def backfill_contact_search_grants() -> None:
    connection = op.get_bind()
    grants = agent_tool_grants_table()
    audits = agent_tool_audits_table()
    place_call_grants = grants.alias("place_call_grants")
    candidates = connection.execute(
        sa.select(
            place_call_grants.c.user_id.label("place_call_user_id"),
            place_call_grants.c.agent_id.label("place_call_agent_id"),
        )
        .where(
            place_call_grants.c.tool_source == "integration",
            place_call_grants.c.status == "allowed",
            place_call_grants.c.integration_id.is_not(None),
            place_call_grants.c.tool_name.like(f"%{PLACE_CALL_TOOL_SUFFIX}"),
            ~sa.exists(
                sa.select(sa.literal(1)).where(
                    grants.c.user_id == place_call_grants.c.user_id,
                    grants.c.agent_id == place_call_grants.c.agent_id,
                    grants.c.tool_source == "internal",
                    grants.c.tool_name == CONTACT_SEARCH_TOOL,
                    grants.c.mcp_server_id.is_(None),
                    grants.c.integration_id.is_(None),
                ),
            ),
        )
        .distinct(),
    ).mappings()
    rows = tuple(candidates)
    if not rows:
        return
    now = datetime.now(UTC)
    grant_rows = []
    audit_rows = []
    for row in rows:
        user_id = str(row["place_call_user_id"])
        agent_id = str(row["place_call_agent_id"])
        grant_rows.append(
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "agent_id": agent_id,
                "tool_name": CONTACT_SEARCH_TOOL,
                "tool_source": "internal",
                "mcp_server_id": None,
                "integration_id": None,
                "status": "allowed",
                "risk": "low",
                "created_at": now,
                "updated_at": now,
            },
        )
        audit_rows.append(
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "agent_id": agent_id,
                "session_id": None,
                "run_id": None,
                "tool_name": CONTACT_SEARCH_TOOL,
                "tool_source": "internal",
                "mcp_server_id": None,
                "integration_id": None,
                "decision": "allowed",
                "reason": BACKFILL_REASON,
                "metadata_json": {},
                "created_at": now,
            },
        )
    connection.execute(grants.insert(), grant_rows)
    connection.execute(audits.insert(), audit_rows)


def remove_unchanged_contact_search_backfill() -> None:
    connection = op.get_bind()
    grants = agent_tool_grants_table()
    audits = agent_tool_audits_table()
    backfilled = tuple(
        connection.execute(
            sa.select(
                audits.c.user_id,
                audits.c.agent_id,
                sa.func.min(audits.c.created_at).label("backfilled_at"),
            )
            .where(
                audits.c.tool_source == "internal",
                audits.c.tool_name == CONTACT_SEARCH_TOOL,
                audits.c.reason == BACKFILL_REASON,
            )
            .group_by(audits.c.user_id, audits.c.agent_id),
        ).mappings(),
    )
    for row in backfilled:
        changed_later = connection.scalar(
            sa.select(sa.literal(1))
            .where(
                audits.c.user_id == row["user_id"],
                audits.c.agent_id == row["agent_id"],
                audits.c.tool_source == "internal",
                audits.c.tool_name == CONTACT_SEARCH_TOOL,
                audits.c.reason != BACKFILL_REASON,
                audits.c.created_at > row["backfilled_at"],
            )
            .limit(1),
        )
        if changed_later:
            continue
        connection.execute(
            grants.delete().where(
                grants.c.user_id == row["user_id"],
                grants.c.agent_id == row["agent_id"],
                grants.c.tool_source == "internal",
                grants.c.tool_name == CONTACT_SEARCH_TOOL,
                grants.c.mcp_server_id.is_(None),
                grants.c.integration_id.is_(None),
                grants.c.status == "allowed",
                grants.c.risk == "low",
            ),
        )
    connection.execute(
        audits.delete().where(audits.c.reason == BACKFILL_REASON),
    )


def agent_tool_grants_table() -> TableClause:
    return sa.table(
        "agent_tool_grants",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("agent_id", sa.String()),
        sa.column("tool_name", sa.String()),
        sa.column("tool_source", sa.String()),
        sa.column("mcp_server_id", sa.String()),
        sa.column("integration_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("risk", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def agent_tool_audits_table() -> TableClause:
    return sa.table(
        "agent_tool_audit_events",
        sa.column("id", sa.String()),
        sa.column("user_id", sa.String()),
        sa.column("agent_id", sa.String()),
        sa.column("session_id", sa.String()),
        sa.column("run_id", sa.String()),
        sa.column("tool_name", sa.String()),
        sa.column("tool_source", sa.String()),
        sa.column("mcp_server_id", sa.String()),
        sa.column("integration_id", sa.String()),
        sa.column("decision", sa.String()),
        sa.column("reason", sa.String()),
        sa.column("metadata_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

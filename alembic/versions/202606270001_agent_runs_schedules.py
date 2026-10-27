"""add durable agent runs and schedules

Revision ID: 202606270001
Revises: 202606250002
Create Date: 2026-06-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606270001"
down_revision: str | None = "202606250002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("queue", sa.String(length=128), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("effective_tool_policy_json", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("chat_id", sa.String(length=36), nullable=True),
        sa.Column("parent_run_id", sa.String(length=36), nullable=True),
        sa.Column("parent_session_id", sa.String(length=36), nullable=True),
        sa.Column("root_run_id", sa.String(length=36), nullable=True),
        sa.Column("schedule_id", sa.String(length=36), nullable=True),
        sa.Column("task_plan_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("error_type", sa.String(length=256), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=256), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=True),
        sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_agent_runs_user_idempotency"),
    )
    op.create_index("ix_agent_runs_user_id", "agent_runs", ["user_id"])
    op.create_index("ix_agent_runs_agent_id", "agent_runs", ["agent_id"])
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index("ix_agent_runs_chat_id", "agent_runs", ["chat_id"])
    op.create_index(
        "ix_agent_runs_active_queue",
        "agent_runs",
        ["status", "queue", "priority", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_due_attempts",
        "agent_runs",
        ["status", "next_attempt_at", "queue"],
    )
    op.create_index(
        "ix_agent_runs_leases",
        "agent_runs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_agent_runs_user_status",
        "agent_runs",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_user_agent_status",
        "agent_runs",
        ["user_id", "agent_id", "status"],
    )
    op.create_index("ix_agent_runs_parent", "agent_runs", ["parent_run_id"])
    op.create_index("ix_agent_runs_root", "agent_runs", ["root_run_id"])
    op.create_index(
        "ix_agent_runs_schedule",
        "agent_runs",
        ["schedule_id", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_task_plan",
        "agent_runs",
        ["task_plan_id", "created_at"],
    )

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("delivery_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_events_run_id", "agent_run_events", ["run_id"])
    op.create_index("ix_agent_run_events_user_id", "agent_run_events", ["user_id"])
    op.create_index("ix_agent_run_events_agent_id", "agent_run_events", ["agent_id"])
    op.create_index(
        "ix_agent_run_events_run_created",
        "agent_run_events",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_events_user_created",
        "agent_run_events",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_events_user_agent_created",
        "agent_run_events",
        ["user_id", "agent_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_events_user_kind_created",
        "agent_run_events",
        ["user_id", "kind", "created_at"],
    )

    op.create_table(
        "agent_schedules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("timezone", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("cron_expression", sa.String(length=256), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_id", sa.String(length=36), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False),
        sa.Column("catch_up_policy", sa.String(length=32), nullable=False),
        sa.Column("misfire_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("retry_policy_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_schedules_user_id", "agent_schedules", ["user_id"])
    op.create_index("ix_agent_schedules_agent_id", "agent_schedules", ["agent_id"])
    op.create_index(
        "ix_agent_schedules_user_status_next",
        "agent_schedules",
        ["user_id", "status", "next_run_at"],
    )
    op.create_index(
        "ix_agent_schedules_due",
        "agent_schedules",
        ["enabled", "status", "next_run_at"],
    )
    op.create_index(
        "ix_agent_schedules_user_agent_status",
        "agent_schedules",
        ["user_id", "agent_id", "status"],
    )

    op.add_column(
        "agent_tool_audit_events",
        sa.Column("run_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_agent_tool_audit_events_run_id",
        "agent_tool_audit_events",
        ["run_id"],
    )
    op.create_index(
        "ix_agent_tool_audit_run_created",
        "agent_tool_audit_events",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_tool_audit_run_created",
        table_name="agent_tool_audit_events",
    )
    op.drop_index(
        "ix_agent_tool_audit_events_run_id",
        table_name="agent_tool_audit_events",
    )
    op.drop_column("agent_tool_audit_events", "run_id")

    op.drop_index(
        "ix_agent_schedules_user_agent_status",
        table_name="agent_schedules",
    )
    op.drop_index("ix_agent_schedules_due", table_name="agent_schedules")
    op.drop_index(
        "ix_agent_schedules_user_status_next",
        table_name="agent_schedules",
    )
    op.drop_index("ix_agent_schedules_agent_id", table_name="agent_schedules")
    op.drop_index("ix_agent_schedules_user_id", table_name="agent_schedules")
    op.drop_table("agent_schedules")

    op.drop_index(
        "ix_agent_run_events_user_kind_created",
        table_name="agent_run_events",
    )
    op.drop_index(
        "ix_agent_run_events_user_agent_created",
        table_name="agent_run_events",
    )
    op.drop_index(
        "ix_agent_run_events_user_created",
        table_name="agent_run_events",
    )
    op.drop_index(
        "ix_agent_run_events_run_created",
        table_name="agent_run_events",
    )
    op.drop_index("ix_agent_run_events_agent_id", table_name="agent_run_events")
    op.drop_index("ix_agent_run_events_user_id", table_name="agent_run_events")
    op.drop_index("ix_agent_run_events_run_id", table_name="agent_run_events")
    op.drop_table("agent_run_events")

    op.drop_index("ix_agent_runs_task_plan", table_name="agent_runs")
    op.drop_index("ix_agent_runs_schedule", table_name="agent_runs")
    op.drop_index("ix_agent_runs_root", table_name="agent_runs")
    op.drop_index("ix_agent_runs_parent", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_agent_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_leases", table_name="agent_runs")
    op.drop_index("ix_agent_runs_due_attempts", table_name="agent_runs")
    op.drop_index("ix_agent_runs_active_queue", table_name="agent_runs")
    op.drop_index("ix_agent_runs_chat_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")

"""add agent runtime memory skills planning

Revision ID: 202606240001
Revises: 202606170001
Create Date: 2026-06-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "202606240001"
down_revision: str | None = "202606170001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("voice", sa.String(length=256), nullable=True),
        sa.Column("room_name", sa.String(length=256), nullable=True),
        sa.Column("room_metadata", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("prompt_hash", sa.String(length=128), nullable=True),
        sa.Column("prompt_snapshot", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=128), nullable=True),
        sa.Column("close_error_type", sa.String(length=256), nullable=True),
        sa.Column("close_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_sessions_provider", "agent_sessions", ["provider"])
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])
    op.create_index(
        "ix_agent_sessions_user_started_at",
        "agent_sessions",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_agent_sessions_user_status",
        "agent_sessions",
        ["user_id", "status"],
    )

    op.create_table(
        "agent_transcripts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("transcript_confidence", sa.Float(), nullable=True),
        sa.Column("language", sa.String(length=64), nullable=True),
        sa.Column("speaker_id", sa.String(length=256), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("livekit_item_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_transcripts_role", "agent_transcripts", ["role"])
    op.create_index("ix_agent_transcripts_session_id", "agent_transcripts", ["session_id"])
    op.create_index("ix_agent_transcripts_source", "agent_transcripts", ["source"])
    op.create_index("ix_agent_transcripts_user_id", "agent_transcripts", ["user_id"])
    op.create_index(
        "ix_agent_transcripts_session_created_at",
        "agent_transcripts",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_transcripts_user_created_at",
        "agent_transcripts",
        ["user_id", "created_at"],
    )

    op.create_table(
        "agent_tool_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("call_id", sa.String(length=256), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("output_text", sa.Text(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False),
        sa.Column("group_id", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tool_events_call_id", "agent_tool_events", ["call_id"])
    op.create_index("ix_agent_tool_events_name", "agent_tool_events", ["name"])
    op.create_index("ix_agent_tool_events_session_id", "agent_tool_events", ["session_id"])
    op.create_index("ix_agent_tool_events_user_id", "agent_tool_events", ["user_id"])
    op.create_index(
        "ix_agent_tool_events_session_created_at",
        "agent_tool_events",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_tool_events_user_name",
        "agent_tool_events",
        ["user_id", "name"],
    )

    op.create_table(
        "agent_usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("usage_type", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("audio_duration_seconds", sa.Float(), nullable=True),
        sa.Column("characters_count", sa.Integer(), nullable=True),
        sa.Column("total_requests", sa.Integer(), nullable=True),
        sa.Column("raw_usage_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_usage_events_session_id", "agent_usage_events", ["session_id"])
    op.create_index("ix_agent_usage_events_usage_type", "agent_usage_events", ["usage_type"])
    op.create_index("ix_agent_usage_events_user_id", "agent_usage_events", ["user_id"])
    op.create_index(
        "ix_agent_usage_events_session_created_at",
        "agent_usage_events",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_usage_events_user_type",
        "agent_usage_events",
        ["user_id", "usage_type"],
    )

    op.create_table(
        "agent_runtime_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runtime_events_session_id", "agent_runtime_events", ["session_id"])
    op.create_index("ix_agent_runtime_events_user_id", "agent_runtime_events", ["user_id"])
    op.create_index(
        "ix_agent_runtime_events_session_created_at",
        "agent_runtime_events",
        ["session_id", "created_at"],
    )
    op.create_index(
        "ix_agent_runtime_events_user_kind",
        "agent_runtime_events",
        ["user_id", "kind"],
    )

    op.create_table(
        "agent_reflection_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_type", sa.String(length=256), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_reflection_jobs_session", "agent_reflection_jobs", ["session_id"])
    op.create_index("ix_agent_reflection_jobs_session_id", "agent_reflection_jobs", ["session_id"])
    op.create_index("ix_agent_reflection_jobs_user_id", "agent_reflection_jobs", ["user_id"])
    op.create_index(
        "ix_agent_reflection_jobs_user_status",
        "agent_reflection_jobs",
        ["user_id", "status"],
    )

    op.create_table(
        "agent_reflection_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("proposal_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["agent_reflection_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_reflection_proposals_job", "agent_reflection_proposals", ["job_id"])
    op.create_index("ix_agent_reflection_proposals_job_id", "agent_reflection_proposals", ["job_id"])
    op.create_index("ix_agent_reflection_proposals_user_id", "agent_reflection_proposals", ["user_id"])
    op.create_index(
        "ix_agent_reflection_proposals_user_status",
        "agent_reflection_proposals",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_agent_reflection_proposals_user_type",
        "agent_reflection_proposals",
        ["user_id", "proposal_type"],
    )

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_key", sa.String(length=256), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence_session_id", sa.String(length=36), nullable=True),
        sa.Column("evidence_transcript_id", sa.String(length=36), nullable=True),
        sa.Column("supersedes_memory_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_reflection_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_reflection_id"], ["agent_reflection_jobs.id"]),
        sa.ForeignKeyConstraint(["evidence_session_id"], ["agent_sessions.id"]),
        sa.ForeignKeyConstraint(["evidence_transcript_id"], ["agent_transcripts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memories_normalized_key", "agent_memories", ["normalized_key"])
    op.create_index("ix_agent_memories_user_id", "agent_memories", ["user_id"])
    op.create_index(
        "ix_agent_memories_user_normalized_key",
        "agent_memories",
        ["user_id", "normalized_key"],
    )
    op.create_index(
        "ix_agent_memories_user_status_kind",
        "agent_memories",
        ["user_id", "status", "kind"],
    )
    op.create_index(
        "ix_agent_memories_user_updated_at",
        "agent_memories",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "agent_skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.String(length=256), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body_markdown", sa.Text(), nullable=False),
        sa.Column("created_by_reflection_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_reflection_id"], ["agent_reflection_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope",
            "owner_user_id",
            "slug",
            name="uq_agent_skills_scope_owner_slug",
        ),
    )
    op.create_index("ix_agent_skills_owner_user_id", "agent_skills", ["owner_user_id"])
    op.create_index("ix_agent_skills_slug", "agent_skills", ["slug"])
    op.create_index(
        "ix_agent_skills_owner_status",
        "agent_skills",
        ["owner_user_id", "status"],
    )
    op.create_index("ix_agent_skills_scope_status", "agent_skills", ["scope", "status"])

    op.create_table(
        "agent_task_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("allow_parallel_steps", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_task_plans_user_id", "agent_task_plans", ["user_id"])
    op.create_index(
        "ix_agent_task_plans_user_status",
        "agent_task_plans",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_agent_task_plans_user_updated_at",
        "agent_task_plans",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "agent_task_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_session_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evidence_session_id"], ["agent_sessions.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["agent_task_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_task_steps_plan_id", "agent_task_steps", ["plan_id"])
    op.create_index(
        "ix_agent_task_steps_plan_position",
        "agent_task_steps",
        ["plan_id", "position"],
    )
    op.create_index("ix_agent_task_steps_user_id", "agent_task_steps", ["user_id"])
    op.create_index(
        "ix_agent_task_steps_user_status",
        "agent_task_steps",
        ["user_id", "status"],
    )

    op.create_table(
        "agent_task_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("plan_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["agent_task_plans.id"]),
        sa.ForeignKeyConstraint(["step_id"], ["agent_task_steps.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_task_events_plan_id", "agent_task_events", ["plan_id"])
    op.create_index("ix_agent_task_events_user_id", "agent_task_events", ["user_id"])
    op.create_index(
        "ix_agent_task_events_plan_created_at",
        "agent_task_events",
        ["plan_id", "created_at"],
    )
    op.create_index(
        "ix_agent_task_events_user_created_at",
        "agent_task_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_task_events_user_created_at", table_name="agent_task_events")
    op.drop_index("ix_agent_task_events_plan_created_at", table_name="agent_task_events")
    op.drop_index("ix_agent_task_events_user_id", table_name="agent_task_events")
    op.drop_index("ix_agent_task_events_plan_id", table_name="agent_task_events")
    op.drop_table("agent_task_events")
    op.drop_index("ix_agent_task_steps_user_status", table_name="agent_task_steps")
    op.drop_index("ix_agent_task_steps_user_id", table_name="agent_task_steps")
    op.drop_index("ix_agent_task_steps_plan_position", table_name="agent_task_steps")
    op.drop_index("ix_agent_task_steps_plan_id", table_name="agent_task_steps")
    op.drop_table("agent_task_steps")
    op.drop_index("ix_agent_task_plans_user_updated_at", table_name="agent_task_plans")
    op.drop_index("ix_agent_task_plans_user_status", table_name="agent_task_plans")
    op.drop_index("ix_agent_task_plans_user_id", table_name="agent_task_plans")
    op.drop_table("agent_task_plans")
    op.drop_index("ix_agent_skills_scope_status", table_name="agent_skills")
    op.drop_index("ix_agent_skills_owner_status", table_name="agent_skills")
    op.drop_index("ix_agent_skills_slug", table_name="agent_skills")
    op.drop_index("ix_agent_skills_owner_user_id", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_index("ix_agent_memories_user_updated_at", table_name="agent_memories")
    op.drop_index("ix_agent_memories_user_status_kind", table_name="agent_memories")
    op.drop_index("ix_agent_memories_user_normalized_key", table_name="agent_memories")
    op.drop_index("ix_agent_memories_user_id", table_name="agent_memories")
    op.drop_index("ix_agent_memories_normalized_key", table_name="agent_memories")
    op.drop_table("agent_memories")
    op.drop_index(
        "ix_agent_reflection_proposals_user_type",
        table_name="agent_reflection_proposals",
    )
    op.drop_index(
        "ix_agent_reflection_proposals_user_status",
        table_name="agent_reflection_proposals",
    )
    op.drop_index(
        "ix_agent_reflection_proposals_user_id",
        table_name="agent_reflection_proposals",
    )
    op.drop_index(
        "ix_agent_reflection_proposals_job_id",
        table_name="agent_reflection_proposals",
    )
    op.drop_index(
        "ix_agent_reflection_proposals_job",
        table_name="agent_reflection_proposals",
    )
    op.drop_table("agent_reflection_proposals")
    op.drop_index("ix_agent_reflection_jobs_user_status", table_name="agent_reflection_jobs")
    op.drop_index("ix_agent_reflection_jobs_user_id", table_name="agent_reflection_jobs")
    op.drop_index("ix_agent_reflection_jobs_session_id", table_name="agent_reflection_jobs")
    op.drop_index("ix_agent_reflection_jobs_session", table_name="agent_reflection_jobs")
    op.drop_table("agent_reflection_jobs")
    op.drop_index("ix_agent_runtime_events_user_kind", table_name="agent_runtime_events")
    op.drop_index(
        "ix_agent_runtime_events_session_created_at",
        table_name="agent_runtime_events",
    )
    op.drop_index("ix_agent_runtime_events_user_id", table_name="agent_runtime_events")
    op.drop_index("ix_agent_runtime_events_session_id", table_name="agent_runtime_events")
    op.drop_table("agent_runtime_events")
    op.drop_index("ix_agent_usage_events_user_type", table_name="agent_usage_events")
    op.drop_index(
        "ix_agent_usage_events_session_created_at",
        table_name="agent_usage_events",
    )
    op.drop_index("ix_agent_usage_events_user_id", table_name="agent_usage_events")
    op.drop_index("ix_agent_usage_events_usage_type", table_name="agent_usage_events")
    op.drop_index("ix_agent_usage_events_session_id", table_name="agent_usage_events")
    op.drop_table("agent_usage_events")
    op.drop_index("ix_agent_tool_events_user_name", table_name="agent_tool_events")
    op.drop_index(
        "ix_agent_tool_events_session_created_at",
        table_name="agent_tool_events",
    )
    op.drop_index("ix_agent_tool_events_user_id", table_name="agent_tool_events")
    op.drop_index("ix_agent_tool_events_session_id", table_name="agent_tool_events")
    op.drop_index("ix_agent_tool_events_name", table_name="agent_tool_events")
    op.drop_index("ix_agent_tool_events_call_id", table_name="agent_tool_events")
    op.drop_table("agent_tool_events")
    op.drop_index("ix_agent_transcripts_user_created_at", table_name="agent_transcripts")
    op.drop_index(
        "ix_agent_transcripts_session_created_at",
        table_name="agent_transcripts",
    )
    op.drop_index("ix_agent_transcripts_user_id", table_name="agent_transcripts")
    op.drop_index("ix_agent_transcripts_source", table_name="agent_transcripts")
    op.drop_index("ix_agent_transcripts_session_id", table_name="agent_transcripts")
    op.drop_index("ix_agent_transcripts_role", table_name="agent_transcripts")
    op.drop_table("agent_transcripts")
    op.drop_index("ix_agent_sessions_user_status", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_user_started_at", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_index("ix_agent_sessions_provider", table_name="agent_sessions")
    op.drop_table("agent_sessions")

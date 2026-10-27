"""add postgres tenant rls policies

Revision ID: 202606270003
Revises: 202606270002
Create Date: 2026-06-27 14:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606270003"
down_revision: str | None = "202606270002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ID_TABLES = (
    "user_voice_preferences",
    "user_provider_credentials",
    "user_livekit_settings",
    "user_text_preferences",
    "user_secret_vault",
    "agent_memories",
    "agent_task_plans",
    "agent_task_steps",
    "agent_task_events",
    "agent_sessions",
    "agent_transcripts",
    "agent_tool_events",
    "agent_usage_events",
    "agent_runtime_events",
    "agent_runs",
    "agent_run_events",
    "agent_schedules",
    "agent_reflection_jobs",
    "agent_reflection_proposals",
    "agent_tool_grants",
    "agent_tool_audit_events",
    "user_mcp_servers",
    "user_mcp_secrets",
    "user_mcp_oauth_states",
)

OWNER_USER_ID_TABLES = (
    "user_agents",
    "agent_skills",
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in USER_ID_TABLES:
        create_policy(table_name=table_name, column_name="user_id", allow_null_owner=False)
    for table_name in OWNER_USER_ID_TABLES:
        create_policy(
            table_name=table_name,
            column_name="owner_user_id",
            allow_null_owner=(table_name == "agent_skills"),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in (*USER_ID_TABLES, *OWNER_USER_ID_TABLES):
        policy_name = policy_name_for_table(table_name)
        op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
        op.execute(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY')


def create_policy(
    *,
    table_name: str,
    column_name: str,
    allow_null_owner: bool,
) -> None:
    policy_name = policy_name_for_table(table_name)
    owner_clause = (
        f'"{column_name}" IS NULL OR "{column_name}" = current_setting(\'app.user_id\', true)'
        if allow_null_owner
        else f"\"{column_name}\" = current_setting('app.user_id', true)"
    )
    write_clause = f"\"{column_name}\" = current_setting('app.user_id', true)"
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY "{policy_name}"
        ON "{table_name}"
        USING ({owner_clause})
        WITH CHECK ({write_clause})
        """,
    )


def policy_name_for_table(table_name: str) -> str:
    return f"rls_{table_name}_tenant"

"""force postgres tenant rls

Revision ID: 202606270004
Revises: 202606270003
Create Date: 2026-06-27 16:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "202606270004"
down_revision: str | None = "202606270003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
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
    "user_agents",
    "agent_skills",
)

AGENT_WORKER_ALL_TABLES = (
    "agent_runs",
    "agent_run_events",
    "agent_schedules",
)

AGENT_WORKER_SELECT_TABLES = ("agent_tool_grants",)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in AGENT_WORKER_ALL_TABLES:
        create_agent_worker_policy(table_name=table_name, command="ALL")
    for table_name in AGENT_WORKER_SELECT_TABLES:
        create_agent_worker_policy(table_name=table_name, command="SELECT")
    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    for table_name in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table_name}" NO FORCE ROW LEVEL SECURITY')
    for table_name in (*AGENT_WORKER_ALL_TABLES, *AGENT_WORKER_SELECT_TABLES):
        op.execute(
            f'DROP POLICY IF EXISTS "{agent_worker_policy_name(table_name)}" ON "{table_name}"',
        )


def create_agent_worker_policy(*, table_name: str, command: str) -> None:
    policy_name = agent_worker_policy_name(table_name)
    clause = "current_setting('app.service_scope', true) = 'agent_worker'"
    if command == "SELECT":
        op.execute(
            f"""
            CREATE POLICY "{policy_name}"
            ON "{table_name}"
            FOR SELECT
            USING ({clause})
            """,
        )
        return
    op.execute(
        f"""
        CREATE POLICY "{policy_name}"
        ON "{table_name}"
        FOR ALL
        USING ({clause})
        WITH CHECK ({clause})
        """,
    )


def agent_worker_policy_name(table_name: str) -> str:
    return f"rls_{table_name}_agent_worker"

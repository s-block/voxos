"""complete browser account schema

Revision ID: 60e7e59bdfc3
Revises: a8f1c2d3e4b5
Create Date: 2026-08-08 16:17:01.861538

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "60e7e59bdfc3"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "a8f1c2d3e4b5"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_browser_accounts_user_id_id",
        "browser_accounts",
        ("user_id", "id"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("browser_account_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("browser_account_execution_attempt", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_agent_runs_browser_account_id",
        "agent_runs",
        ("browser_account_id",),
    )
    op.create_foreign_key(
        "fk_agent_runs_user_browser_account",
        "agent_runs",
        "browser_accounts",
        ("user_id", "browser_account_id"),
        ("user_id", "id"),
        ondelete="RESTRICT",
    )
    op.add_column(
        "agent_schedules",
        sa.Column("browser_account_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        "ix_agent_schedules_browser_account_id",
        "agent_schedules",
        ("browser_account_id",),
    )
    op.create_foreign_key(
        "fk_agent_schedules_user_browser_account",
        "agent_schedules",
        "browser_accounts",
        ("user_id", "browser_account_id"),
        ("user_id", "id"),
        ondelete="RESTRICT",
    )

    op.add_column(
        "browser_account_run_authorizations",
        sa.Column("client_turn_id", sa.String(length=64), nullable=True),
    )
    _backfill_client_turn_ids()
    op.alter_column(
        "browser_account_run_authorizations",
        "client_turn_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.create_index(
        "ix_browser_account_authorizations_user_turn",
        "browser_account_run_authorizations",
        ("user_id", "client_turn_id"),
    )
    op.drop_column("browser_account_run_authorizations", "task_digest")

    op.drop_constraint(
        "uq_browser_accounts_user_label",
        "browser_accounts",
        type_="unique",
    )
    op.create_index(
        "uq_browser_accounts_user_label",
        "browser_accounts",
        ("user_id", "label"),
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_browser_accounts_user_label", table_name="browser_accounts")
    op.create_unique_constraint(
        "uq_browser_accounts_user_label",
        "browser_accounts",
        ("user_id", "label"),
    )

    op.add_column(
        "browser_account_run_authorizations",
        sa.Column("task_digest", sa.String(length=64), nullable=True),
    )
    _backfill_task_digests()
    op.alter_column(
        "browser_account_run_authorizations",
        "task_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_index(
        "ix_browser_account_authorizations_user_turn",
        table_name="browser_account_run_authorizations",
    )
    op.drop_column("browser_account_run_authorizations", "client_turn_id")

    op.drop_constraint(
        "fk_agent_schedules_user_browser_account",
        "agent_schedules",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_agent_schedules_browser_account_id",
        table_name="agent_schedules",
    )
    op.drop_column("agent_schedules", "browser_account_id")
    op.drop_constraint(
        "fk_agent_runs_user_browser_account",
        "agent_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_agent_runs_browser_account_id", table_name="agent_runs")
    op.drop_column("agent_runs", "browser_account_execution_attempt")
    op.drop_column("agent_runs", "browser_account_id")
    op.drop_constraint(
        "uq_browser_accounts_user_id_id",
        "browser_accounts",
        type_="unique",
    )


def _backfill_client_turn_ids() -> None:
    connection = op.get_bind()
    authorizations = _authorization_table()
    for user_id in _user_ids(connection):
        _set_tenant_user(connection, user_id)
        connection.execute(
            authorizations.update()
            .where(
                authorizations.c.user_id == user_id,
                authorizations.c.client_turn_id.is_(None),
            )
            .values(
                client_turn_id=authorizations.c.id,
                revoked_at=sa.func.coalesce(
                    authorizations.c.revoked_at,
                    sa.func.now(),
                ),
            ),
        )


def _backfill_task_digests() -> None:
    connection = op.get_bind()
    authorizations = _authorization_table()
    for user_id in _user_ids(connection):
        _set_tenant_user(connection, user_id)
        connection.execute(
            authorizations.update()
            .where(
                authorizations.c.user_id == user_id,
                authorizations.c.task_digest.is_(None),
            )
            .values(task_digest=authorizations.c.id),
        )


def _authorization_table() -> sa.TableClause:
    return sa.table(
        "browser_account_run_authorizations",
        sa.column("id", sa.String(length=36)),
        sa.column("user_id", sa.String(length=256)),
        sa.column("client_turn_id", sa.String(length=64)),
        sa.column("task_digest", sa.String(length=64)),
        sa.column("revoked_at", sa.DateTime(timezone=True)),
    )


def _user_ids(connection: sa.Connection) -> tuple[str, ...]:
    users = sa.table(
        "auth_firebase_users",
        sa.column("id", sa.String(length=36)),
    )
    return tuple(str(row.id) for row in connection.execute(sa.select(users.c.id)))


def _set_tenant_user(connection: sa.Connection, user_id: str) -> None:
    if connection.dialect.name != "postgresql":
        return
    connection.execute(
        sa.text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )

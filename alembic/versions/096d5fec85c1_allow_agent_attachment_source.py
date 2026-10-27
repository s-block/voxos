"""allow agent attachment source

Revision ID: 096d5fec85c1
Revises: 679f69df3d03
Create Date: 2026-08-22 07:29:52.472635

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "096d5fec85c1"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "679f69df3d03"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "message_attachments"
CONSTRAINT_NAME = "ck_message_attachments_client_source"


def upgrade() -> None:
    replace_client_source_constraint(
        "client_source IN ('web', 'flutter', 'agent')",
    )


def downgrade() -> None:
    connection = op.get_bind()
    agent_rows = connection.execute(
        sa.text(
            "SELECT count(*) FROM message_attachments WHERE client_source = 'agent'",
        ),
    ).scalar_one()
    if agent_rows:
        message = "Cannot downgrade while agent-generated attachments exist; retain or migrate those rows first"
        raise RuntimeError(message)
    replace_client_source_constraint(
        "client_source IN ('web', 'flutter')",
    )


def replace_client_source_constraint(expression: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(TABLE_NAME) as batch_op:
            batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.create_check_constraint(CONSTRAINT_NAME, expression)
        return
    op.drop_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        type_="check",
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        TABLE_NAME,
        expression,
    )

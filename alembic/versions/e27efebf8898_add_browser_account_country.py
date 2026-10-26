"""add browser account country

Revision ID: e27efebf8898
Revises: 60e7e59bdfc3
Create Date: 2026-08-10 13:47:04.560792

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e27efebf8898"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "60e7e59bdfc3"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "browser_accounts",
        sa.Column(
            "browser_country_code",
            sa.String(length=2),
            server_default="GB",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_browser_accounts_country_code",
        "browser_accounts",
        "length(browser_country_code) = 2 "
        "AND substr(browser_country_code, 1, 1) BETWEEN 'A' AND 'Z' "
        "AND substr(browser_country_code, 2, 1) BETWEEN 'A' AND 'Z'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_browser_accounts_country_code",
        "browser_accounts",
        type_="check",
    )
    op.drop_column("browser_accounts", "browser_country_code")

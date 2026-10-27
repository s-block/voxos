"""reserve the LiveKit worker routing revision

Revision ID: 202607090001
Revises: 202607050001
Create Date: 2026-07-09 20:10:00.000000

This revision intentionally has no operations. An unreleased local version
briefly granted the broad ``agent_worker`` scope access to every tenant's
LiveKit settings. The demand-driven design in the following revision never
needs that access and defensively removes either legacy policy if present.

"""

from collections.abc import Sequence

revision: str = "202607090001"
down_revision: str | None = "202607050001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return

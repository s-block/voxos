"""store unmasked whatsapp phone numbers

Revision ID: 9d3048bd7659
Revises: 9ced105cdeee
Create Date: 2026-07-21 17:08:43.700168

"""

from collections.abc import Sequence

from alembic import op

revision: str = "9d3048bd7659"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "9ced105cdeee"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "whatsapp_mobile_registrations",
        "masked_phone_number",
        new_column_name="phone_number",
    )
    op.alter_column(
        "whatsapp_sessions",
        "masked_phone_number",
        new_column_name="phone_number",
    )
    op.execute(
        """
        UPDATE whatsapp_mobile_registrations AS registration
        SET phone_number = resource.display_name
        FROM integration_resources AS resource
        WHERE resource.id = registration.source_resource_id
          AND resource.user_id = registration.user_id
        """,
    )
    op.execute(
        """
        UPDATE whatsapp_sessions AS session
        SET phone_number = resource.display_name
        FROM integration_resources AS resource
        WHERE resource.id = session.source_resource_id
          AND resource.user_id = session.user_id
        """,
    )
    op.execute(
        """
        UPDATE agent_sessions AS session
        SET title = 'SMS from ' || conversation.remote_peer
        FROM integration_conversations AS conversation
        WHERE conversation.runtime_session_id = session.id
          AND conversation.user_id = session.user_id
          AND conversation.medium = 'sms'
        """,
    )
    op.execute(
        """
        UPDATE agent_sessions AS session
        SET title = 'SMS from ' || conversation.remote_peer
        FROM agent_runs AS run
        JOIN integration_conversations AS conversation
          ON conversation.id = run.payload_json ->> 'conversation_id'
         AND conversation.user_id = run.user_id
        WHERE run.session_id = session.id
          AND run.user_id = session.user_id
          AND conversation.medium = 'sms'
        """,
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE agent_sessions
        SET title = 'SMS from ***' || right(title, 4)
        WHERE title LIKE 'SMS from +%'
        """,
    )
    op.execute(
        """
        UPDATE whatsapp_mobile_registrations
        SET phone_number = CASE
            WHEN length(phone_number) <= 4 THEN '****'
            ELSE '***' || right(phone_number, 4)
        END
        """,
    )
    op.execute(
        """
        UPDATE whatsapp_sessions
        SET phone_number = CASE
            WHEN length(phone_number) <= 4 THEN '****'
            ELSE '***' || right(phone_number, 4)
        END
        """,
    )
    op.alter_column(
        "whatsapp_sessions",
        "phone_number",
        new_column_name="masked_phone_number",
    )
    op.alter_column(
        "whatsapp_mobile_registrations",
        "phone_number",
        new_column_name="masked_phone_number",
    )

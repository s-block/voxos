"""add whatsapp contact identities

Revision ID: 06a519f1b316
Revises: b7aefc1c5bde
Create Date: 2026-07-21 13:54:46.975441

"""

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "06a519f1b316"  # pragma: allowlist secret
down_revision: str | Sequence[str] | None = "b7aefc1c5bde"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "whatsapp_contact_identities"
RLS_POLICY = "rls_whatsapp_contact_identities_tenant"
TENANT_CLAUSE = "\"user_id\" = current_setting('app.user_id', true)"
PN_JID_PATTERN = re.compile(
    r"^([1-9][0-9]{5,14})@(s\.whatsapp\.net|hosted)$",
)
LID_JID_PATTERN = re.compile(
    r"^[1-9][0-9]{5,19}@(lid|hosted\.lid)$",
)


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("integration_id", sa.String(length=36), nullable=False),
        sa.Column("contact_id", sa.String(length=36), nullable=True),
        sa.Column("phone_number", sa.String(length=16), nullable=True),
        sa.Column("pn_jid", sa.String(length=128), nullable=True),
        sa.Column("lid_jid", sa.String(length=128), nullable=True),
        sa.Column("username", sa.String(length=256), nullable=True),
        sa.Column("display_name", sa.String(length=256), nullable=True),
        sa.Column("resolution_state", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pn_jid IS NOT NULL OR lid_jid IS NOT NULL OR username IS NOT NULL",
            name="ck_whatsapp_contact_identities_has_identifier",
        ),
        sa.CheckConstraint(
            "resolution_state IN ('unlinked', 'linked', 'conflict')",
            name="ck_whatsapp_contact_identities_resolution_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "integration_id",
            "lid_jid",
            name="uq_whatsapp_contact_identities_lid",
        ),
        sa.UniqueConstraint(
            "user_id",
            "integration_id",
            "pn_jid",
            name="uq_whatsapp_contact_identities_pn",
        ),
        sa.UniqueConstraint(
            "user_id",
            "integration_id",
            "username",
            name="uq_whatsapp_contact_identities_username",
        ),
    )
    op.create_index(
        "ix_whatsapp_contact_identities_user_id",
        TABLE_NAME,
        ("user_id",),
    )
    op.create_index(
        "ix_whatsapp_contact_identities_user_contact",
        TABLE_NAME,
        ("user_id", "contact_id"),
    )
    op.create_index(
        "ix_whatsapp_contact_identities_user_integration",
        TABLE_NAME,
        ("user_id", "integration_id"),
    )
    op.add_column(
        "integration_messages",
        sa.Column("contact_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "integration_messages",
        sa.Column("contact_display_name", sa.String(length=256), nullable=True),
    )
    op.create_index(
        "ix_integration_messages_contact_id",
        "integration_messages",
        ("contact_id",),
    )

    _backfill_whatsapp_identities()
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f'ALTER TABLE "{TABLE_NAME}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE_NAME}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f"""
            CREATE POLICY "{RLS_POLICY}"
            ON "{TABLE_NAME}"
            FOR ALL
            USING ({TENANT_CLAUSE})
            WITH CHECK ({TENANT_CLAUSE})
            """,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f'DROP POLICY IF EXISTS "{RLS_POLICY}" ON "{TABLE_NAME}"',
        )
        op.execute(f'ALTER TABLE "{TABLE_NAME}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{TABLE_NAME}" DISABLE ROW LEVEL SECURITY')
    op.drop_index(
        "ix_integration_messages_contact_id",
        table_name="integration_messages",
    )
    op.drop_column("integration_messages", "contact_display_name")
    op.drop_column("integration_messages", "contact_id")
    op.drop_index(
        "ix_whatsapp_contact_identities_user_integration",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_whatsapp_contact_identities_user_contact",
        table_name=TABLE_NAME,
    )
    op.drop_index(
        "ix_whatsapp_contact_identities_user_id",
        table_name=TABLE_NAME,
    )
    op.drop_table(TABLE_NAME)


def _backfill_whatsapp_identities() -> None:
    connection = op.get_bind()
    messages = connection.execute(
        sa.text(
            """
            SELECT user_id, integration_id, payload_json,
                   COALESCE(received_at, created_at) AS observed_at
            FROM integration_messages
            WHERE provider = 'whatsapp' AND direction = 'inbound'
            ORDER BY created_at ASC, id ASC
            """,
        ),
    ).mappings()
    contacts = {
        (row.user_id, row.phone_number): (row.id, row.display_name)
        for row in connection.execute(
            sa.text(
                "SELECT id, user_id, phone_number, display_name FROM user_contacts",
            ),
        ).mappings()
    }
    identities: list[dict[str, object | None]] = []
    pn_index: dict[tuple[str, str, str], dict[str, object | None]] = {}
    lid_index: dict[tuple[str, str, str], dict[str, object | None]] = {}
    for message in messages:
        payload = message.payload_json
        if not isinstance(payload, dict):
            continue
        pn_jid = _valid_pn_jid(payload.get("remote_pn_jid"))
        lid_jid = _valid_lid_jid(payload.get("remote_lid_jid"))
        if pn_jid is None and lid_jid is None:
            continue
        user_id = str(message.user_id)
        integration_id = str(message.integration_id)
        pn_identity = pn_index.get((user_id, integration_id, pn_jid)) if pn_jid is not None else None
        lid_identity = lid_index.get((user_id, integration_id, lid_jid)) if lid_jid is not None else None
        if pn_identity is not None and lid_identity is not None and pn_identity is not lid_identity:
            pn_identity["contact_id"] = None
            pn_identity["resolution_state"] = "conflict"
            lid_identity["contact_id"] = None
            lid_identity["resolution_state"] = "conflict"
            continue
        identity = pn_identity or lid_identity
        observed_at = message.observed_at or datetime.now(tz=UTC)
        if identity is None:
            phone_number = f"+{pn_jid.split('@', maxsplit=1)[0]}" if pn_jid is not None else None
            contact = contacts.get((user_id, phone_number))
            identity = {
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "integration_id": integration_id,
                "contact_id": contact[0] if contact is not None else None,
                "phone_number": phone_number,
                "pn_jid": pn_jid,
                "lid_jid": lid_jid,
                "username": None,
                "display_name": contact[1] if contact is not None else None,
                "resolution_state": "linked" if contact is not None else "unlinked",
                "first_seen_at": observed_at,
                "last_seen_at": observed_at,
                "created_at": observed_at,
                "updated_at": observed_at,
            }
            identities.append(identity)
        else:
            if identity["pn_jid"] not in {None, pn_jid} or identity["lid_jid"] not in {None, lid_jid}:
                identity["contact_id"] = None
                identity["resolution_state"] = "conflict"
                continue
            identity["pn_jid"] = pn_jid or identity["pn_jid"]
            identity["lid_jid"] = lid_jid or identity["lid_jid"]
            identity["last_seen_at"] = observed_at
            identity["updated_at"] = observed_at
        if pn_jid is not None:
            pn_index[(user_id, integration_id, pn_jid)] = identity
        if lid_jid is not None:
            lid_index[(user_id, integration_id, lid_jid)] = identity
    if identities:
        identity_table = sa.table(
            TABLE_NAME,
            *(sa.column(column_name) for column_name in identities[0]),
        )
        op.bulk_insert(identity_table, identities)


def _valid_pn_jid(value: object) -> str | None:
    return value if isinstance(value, str) and PN_JID_PATTERN.fullmatch(value) else None


def _valid_lid_jid(value: object) -> str | None:
    return value if isinstance(value, str) and LID_JID_PATTERN.fullmatch(value) else None

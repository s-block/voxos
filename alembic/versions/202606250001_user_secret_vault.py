"""move encrypted user secrets into dedicated vault table

Revision ID: 202606250001
Revises: 202606240002
Create Date: 2026-06-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

revision: str = "202606250001"
down_revision: str | None = "202606240002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    op.create_table(
        "user_secret_vault",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=256), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("encrypted_data_key", sa.LargeBinary(), nullable=True),
        sa.Column("encryption_backend", sa.String(length=64), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=512), nullable=True),
        sa.Column("encryption_algorithm", sa.String(length=64), nullable=False),
        sa.Column(
            "encryption_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "credential_id",
            "purpose",
            name="uq_user_secret_vault_reference",
        ),
    )
    op.create_index(
        "ix_user_secret_vault_user_purpose",
        "user_secret_vault",
        ["user_id", "purpose"],
    )
    op.create_index(
        "ix_user_secret_vault_reference",
        "user_secret_vault",
        ["provider", "credential_id", "purpose"],
    )

    if _column_exists(connection, "user_provider_credentials", "encrypted_secret"):
        op.execute(
            sa.text(
                """
                INSERT INTO user_secret_vault (
                    id,
                    user_id,
                    provider,
                    credential_id,
                    purpose,
                    ciphertext,
                    nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    user_id,
                    provider,
                    id,
                    'voice_provider_credential',
                    encrypted_secret,
                    encryption_nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                FROM user_provider_credentials
                """,
            ),
        )
        _drop_encrypted_payload_columns("user_provider_credentials", "encrypted_secret")

    if _column_exists(connection, "user_mcp_secrets", "encrypted_secret"):
        op.execute(
            sa.text(
                """
                INSERT INTO user_secret_vault (
                    id,
                    user_id,
                    provider,
                    credential_id,
                    purpose,
                    ciphertext,
                    nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    user_id,
                    'mcp_server',
                    id,
                    'mcp_server_secret',
                    encrypted_secret,
                    encryption_nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                FROM user_mcp_secrets
                """,
            ),
        )
        _drop_encrypted_payload_columns("user_mcp_secrets", "encrypted_secret")

    if _column_exists(connection, "user_mcp_oauth_states", "encrypted_code_verifier"):
        op.execute(
            sa.text(
                """
                INSERT INTO user_secret_vault (
                    id,
                    user_id,
                    provider,
                    credential_id,
                    purpose,
                    ciphertext,
                    nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    user_id,
                    'mcp_server',
                    id,
                    'mcp_oauth_state',
                    encrypted_code_verifier,
                    encryption_nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    created_at
                FROM user_mcp_oauth_states
                """,
            ),
        )
        _drop_encrypted_payload_columns(
            "user_mcp_oauth_states",
            "encrypted_code_verifier",
        )

    if _column_exists(connection, "user_livekit_settings", "encrypted_credentials"):
        op.execute(
            sa.text(
                """
                INSERT INTO user_secret_vault (
                    id,
                    user_id,
                    provider,
                    credential_id,
                    purpose,
                    ciphertext,
                    nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    user_id,
                    'livekit',
                    id,
                    'livekit_credentials',
                    encrypted_credentials,
                    encryption_nonce,
                    encrypted_data_key,
                    encryption_backend,
                    encryption_key_id,
                    encryption_algorithm,
                    encryption_context,
                    created_at,
                    updated_at
                FROM user_livekit_settings
                """,
            ),
        )
        _drop_encrypted_payload_columns(
            "user_livekit_settings",
            "encrypted_credentials",
        )


def downgrade() -> None:
    op.drop_index("ix_user_secret_vault_reference", table_name="user_secret_vault")
    op.drop_index("ix_user_secret_vault_user_purpose", table_name="user_secret_vault")
    op.drop_table("user_secret_vault")


def _column_exists(connection: Connection, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(connection)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _drop_encrypted_payload_columns(
    table_name: str,
    secret_column_name: str,
) -> None:
    op.drop_column(table_name, "encryption_context")
    op.drop_column(table_name, "encryption_algorithm")
    op.drop_column(table_name, "encryption_key_id")
    op.drop_column(table_name, "encryption_backend")
    op.drop_column(table_name, "encrypted_data_key")
    op.drop_column(table_name, "encryption_nonce")
    op.drop_column(table_name, secret_column_name)

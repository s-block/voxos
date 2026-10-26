"""Opt-in PostgreSQL RLS enforcement tests for tenant-owned contacts.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database. The configured application role must be non-superuser and
``NOBYPASSRLS``. The test runs in a transaction and retains no contact rows.
"""

import os
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from voxos.db.settings import normalize_async_database_url

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
CONTACTS_POLICY = "rls_user_contacts_tenant"
COMMUNICATIONS_POLICIES = {
    "integration_conversations": "rls_integration_conversations_tenant",
    "integration_replies": "rls_integration_replies_tenant",
}


@pytest_asyncio.fixture
async def postgres_contacts_connection() -> AsyncIterator[AsyncConnection]:
    database_url = os.environ.get(POSTGRES_RLS_TEST_URL_ENV)
    if database_url is None:
        pytest.skip(f"set {POSTGRES_RLS_TEST_URL_ENV} to run PostgreSQL RLS tests")

    async_database_url = normalize_async_database_url(database_url)
    if make_url(async_database_url).get_backend_name() != "postgresql":
        pytest.fail(f"{POSTGRES_RLS_TEST_URL_ENV} must use PostgreSQL")

    engine = create_async_engine(async_database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await assert_safe_test_database(connection)
            await assert_contacts_schema_is_migrated(connection)
            await connection.rollback()
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await dispose_engine(engine)


@pytest.mark.asyncio
async def test_contacts_enforce_tenant_read_and_write_isolation(
    postgres_contacts_connection: AsyncConnection,
) -> None:
    connection = postgres_contacts_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    contact_a = contact_values(user_id=tenant_a)

    await set_tenant_context(connection, tenant_a)
    await insert_contact(connection, contact_a)

    await set_tenant_context(connection, tenant_b)
    assert await visible_contact_count(connection) == 0
    update_result = await connection.execute(
        text(
            """
            UPDATE user_contacts
            SET display_name = 'forbidden-update'
            WHERE id = :contact_id
            """,
        ),
        {"contact_id": contact_a["id"]},
    )
    assert update_result.rowcount == 0
    delete_result = await connection.execute(
        text("DELETE FROM user_contacts WHERE id = :contact_id"),
        {"contact_id": contact_a["id"]},
    )
    assert delete_result.rowcount == 0
    await assert_insert_rejected(
        connection,
        contact_a | {"id": new_id(), "phone_number": "+12025550198"},
    )

    contact_b = contact_values(
        user_id=tenant_b,
        phone_number=str(contact_a["phone_number"]),
    )
    await insert_contact(connection, contact_b)
    assert await visible_contact_count(connection) == 1

    await set_tenant_context(connection, tenant_a)
    visible_ids = await connection.execute(
        text(
            """
            SELECT id
            FROM user_contacts
            WHERE id IN (:contact_a, :contact_b)
            ORDER BY id
            """,
        ),
        {"contact_a": contact_a["id"], "contact_b": contact_b["id"]},
    )
    assert tuple(visible_ids.scalars()) == (contact_a["id"],)


@pytest.mark.asyncio
async def test_communication_records_enforce_tenant_read_and_write_isolation(
    postgres_contacts_connection: AsyncConnection,
) -> None:
    connection = postgres_contacts_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    conversation_id = new_id()
    reply_id = new_id()
    now = datetime.now(UTC)

    await set_tenant_context(connection, tenant_a)
    await connection.execute(
        text(
            """
            INSERT INTO integration_conversations (
                id, user_id, integration_id, resource_id, provider, medium,
                remote_peer, agent_id, runtime_session_id, last_interaction_at,
                expires_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, :integration_id, :resource_id, 'telnyx', 'sms',
                '+447700900123', :agent_id, :runtime_session_id, :now,
                :expires_at, :now, :now
            )
            """,
        ),
        {
            "id": conversation_id,
            "user_id": tenant_a,
            "integration_id": new_id(),
            "resource_id": new_id(),
            "agent_id": new_id(),
            "runtime_session_id": new_id(),
            "now": now,
            "expires_at": now,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO integration_replies (
                id, user_id, inbound_message_id, conversation_id, status,
                response_profile, condensation_attempted, overflow_fallback_used,
                created_at, updated_at
            ) VALUES (
                :id, :user_id, :inbound_message_id, :conversation_id, 'pending',
                'sms:v1', false, false, :now, :now
            )
            """,
        ),
        {
            "id": reply_id,
            "user_id": tenant_a,
            "inbound_message_id": new_id(),
            "conversation_id": conversation_id,
            "now": now,
        },
    )

    await set_tenant_context(connection, tenant_b)
    for table_name in COMMUNICATIONS_POLICIES:
        result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
        assert result.scalar_one() == 0
        update_result = await connection.execute(
            text(f'UPDATE "{table_name}" SET updated_at = :now'),
            {"now": now},
        )
        assert update_result.rowcount == 0

    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    """
                    INSERT INTO integration_replies (
                        id, user_id, inbound_message_id, conversation_id, status,
                        response_profile, condensation_attempted, overflow_fallback_used,
                        created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :inbound_message_id, :conversation_id, 'pending',
                        'sms:v1', false, false, :now, :now
                    )
                    """,
                ),
                {
                    "id": new_id(),
                    "user_id": tenant_a,
                    "inbound_message_id": new_id(),
                    "conversation_id": conversation_id,
                    "now": now,
                },
            )
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def assert_safe_test_database(connection: AsyncConnection) -> None:
    result = await connection.execute(
        text(
            """
            SELECT current_database() AS database_name,
                   roles.rolsuper AS is_superuser,
                   roles.rolbypassrls AS bypasses_rls
            FROM pg_roles AS roles
            WHERE roles.rolname = current_user
            """,
        ),
    )
    role = result.mappings().one()
    if "test" not in str(role["database_name"]).lower():
        pytest.fail(
            f"{POSTGRES_RLS_TEST_URL_ENV} must name a disposable test database",
        )
    assert role["is_superuser"] is False, "RLS tests require a non-superuser role"
    assert role["bypasses_rls"] is False, "RLS tests require a NOBYPASSRLS role"


async def assert_contacts_schema_is_migrated(connection: AsyncConnection) -> None:
    security_result = await connection.execute(
        text(
            """
            SELECT rel.relrowsecurity AS rls_enabled,
                   rel.relforcerowsecurity AS rls_forced
            FROM pg_class AS rel
            JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
            WHERE namespace.nspname = current_schema()
              AND rel.relname = 'user_contacts'
            """,
        ),
    )
    security = security_result.mappings().one()
    assert security == {"rls_enabled": True, "rls_forced": True}
    policy_result = await connection.execute(
        text(
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename = 'user_contacts'
            """,
        ),
    )
    assert set(policy_result.scalars()) == {CONTACTS_POLICY}
    for table_name, policy_name in COMMUNICATIONS_POLICIES.items():
        await assert_forced_tenant_policy(
            connection,
            table_name=table_name,
            policy_name=policy_name,
        )


async def assert_forced_tenant_policy(
    connection: AsyncConnection,
    *,
    table_name: str,
    policy_name: str,
) -> None:
    security_result = await connection.execute(
        text(
            """
            SELECT rel.relrowsecurity AS rls_enabled,
                   rel.relforcerowsecurity AS rls_forced
            FROM pg_class AS rel
            JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
            WHERE namespace.nspname = current_schema()
              AND rel.relname = :table_name
            """,
        ),
        {"table_name": table_name},
    )
    assert security_result.mappings().one() == {
        "rls_enabled": True,
        "rls_forced": True,
    }
    policy_result = await connection.execute(
        text(
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename = :table_name
            """,
        ),
        {"table_name": table_name},
    )
    assert set(policy_result.scalars()) == {policy_name}


async def set_tenant_context(connection: AsyncConnection, user_id: str) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def visible_contact_count(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT count(*) FROM user_contacts"))
    return int(result.scalar_one())


async def assert_insert_rejected(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await insert_contact(connection, values)
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def insert_contact(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO user_contacts (
                id, user_id, display_name, phone_number, email, notes,
                is_admin, created_at, updated_at
            ) VALUES (
                :id, :user_id, :display_name, :phone_number, NULL, NULL,
                false, :created_at, :updated_at
            )
            """,
        ),
        values,
    )


def contact_values(
    *,
    user_id: str,
    phone_number: str = "+12025550199",
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": new_id(),
        "user_id": user_id,
        "display_name": "RLS contact",
        "phone_number": phone_number,
        "created_at": now,
        "updated_at": now,
    }


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

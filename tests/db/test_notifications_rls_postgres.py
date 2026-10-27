"""Opt-in PostgreSQL RLS enforcement tests for push notifications.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database. The configured application role must be non-superuser and
``NOBYPASSRLS``. Each test runs in a transaction and retains no fixture rows.
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
from sqlalchemy.sql.elements import TextClause

from voxos.db.settings import normalize_async_database_url

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
INSTALLATIONS_TABLE = "push_installations"
OUTBOX_TABLE = "push_notification_outbox"
TABLE_POLICIES = {
    INSTALLATIONS_TABLE: {
        "rls_push_installations_tenant",
        "rls_push_installations_notification_worker",
    },
    OUTBOX_TABLE: {
        "rls_push_notification_outbox_tenant",
        "rls_push_notification_outbox_notification_worker",
    },
}


@pytest_asyncio.fixture
async def postgres_notifications_connection() -> AsyncIterator[AsyncConnection]:
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
            await assert_notification_schema_is_migrated(connection)
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
async def test_push_records_enforce_tenant_read_and_write_isolation(
    postgres_notifications_connection: AsyncConnection,
) -> None:
    connection = postgres_notifications_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    installation_a = installation_values(user_id=tenant_a)
    outbox_a = outbox_values(user_id=tenant_a)

    await set_rls_context(connection, user_id=tenant_a)
    await insert_installation(connection, installation_a)
    await insert_outbox(connection, outbox_a)

    await set_rls_context(connection, user_id=tenant_b)
    for table_name in TABLE_POLICIES:
        assert await visible_row_count(connection, table_name) == 0

    installation_update = await connection.execute(
        text("UPDATE push_installations SET active = false WHERE id = :record_id"),
        {"record_id": installation_a["id"]},
    )
    outbox_update = await connection.execute(
        text(
            "UPDATE push_notification_outbox SET status = 'failed' WHERE id = :record_id",
        ),
        {"record_id": outbox_a["id"]},
    )
    assert installation_update.rowcount == 0
    assert outbox_update.rowcount == 0

    for table_name, record_id in (
        (INSTALLATIONS_TABLE, installation_a["id"]),
        (OUTBOX_TABLE, outbox_a["id"]),
    ):
        delete_result = await connection.execute(
            text(f'DELETE FROM "{table_name}" WHERE id = :record_id'),
            {"record_id": record_id},
        )
        assert delete_result.rowcount == 0

    await assert_statement_rejected(
        connection,
        installation_insert_statement(),
        installation_a | {"id": new_id(), "installation_id": unique_value("fid")},
    )
    await assert_statement_rejected(
        connection,
        outbox_insert_statement(),
        outbox_a | {"id": new_id(), "event_key": unique_value("event")},
    )

    await insert_installation(connection, installation_values(user_id=tenant_b))
    await insert_outbox(connection, outbox_values(user_id=tenant_b))
    for table_name in TABLE_POLICIES:
        assert await visible_row_count(connection, table_name) == 1

    await set_rls_context(connection, user_id=tenant_a)
    for table_name in TABLE_POLICIES:
        assert await visible_row_count(connection, table_name) == 1


@pytest.mark.asyncio
async def test_only_notification_worker_can_access_push_records_across_tenants(
    postgres_notifications_connection: AsyncConnection,
) -> None:
    connection = postgres_notifications_connection
    for tenant in (unique_value("tenant-a"), unique_value("tenant-b")):
        await set_rls_context(connection, user_id=tenant)
        await insert_installation(connection, installation_values(user_id=tenant))
        await insert_outbox(connection, outbox_values(user_id=tenant))

    await set_rls_context(connection, service_scope="agent_worker")
    for table_name in TABLE_POLICIES:
        assert await visible_row_count(connection, table_name) == 0

    await set_rls_context(connection, service_scope="notification_worker")
    for table_name in TABLE_POLICIES:
        assert await visible_row_count(connection, table_name) == 2

    installation_update = await connection.execute(
        text("UPDATE push_installations SET active = false"),
    )
    outbox_update = await connection.execute(
        text("UPDATE push_notification_outbox SET status = 'failed'"),
    )
    assert installation_update.rowcount == 2
    assert outbox_update.rowcount == 2


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


async def assert_notification_schema_is_migrated(
    connection: AsyncConnection,
) -> None:
    security_result = await connection.execute(
        text(
            """
            SELECT rel.relname,
                   rel.relrowsecurity AS rls_enabled,
                   rel.relforcerowsecurity AS rls_forced
            FROM pg_class AS rel
            JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
            WHERE namespace.nspname = current_schema()
              AND rel.relname IN (
                  'push_installations',
                  'push_notification_outbox'
              )
            """,
        ),
    )
    security = {str(row["relname"]): (row["rls_enabled"], row["rls_forced"]) for row in security_result.mappings()}
    assert security == {
        INSTALLATIONS_TABLE: (True, True),
        OUTBOX_TABLE: (True, True),
    }, "apply the push notifications migration to the disposable test database"

    policy_result = await connection.execute(
        text(
            """
            SELECT tablename, policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename IN (
                  'push_installations',
                  'push_notification_outbox'
              )
            """,
        ),
    )
    policies: dict[str, set[str]] = {
        INSTALLATIONS_TABLE: set(),
        OUTBOX_TABLE: set(),
    }
    for row in policy_result.mappings():
        policies[str(row["tablename"])].add(str(row["policyname"]))
    assert policies == TABLE_POLICIES


async def set_rls_context(
    connection: AsyncConnection,
    *,
    user_id: str | None = None,
    service_scope: str | None = None,
) -> None:
    await connection.execute(
        text(
            """
            SELECT set_config('app.user_id', :user_id, true),
                   set_config('app.service_scope', :service_scope, true)
            """,
        ),
        {
            "user_id": user_id or "",
            "service_scope": service_scope or "",
        },
    )


async def visible_row_count(connection: AsyncConnection, table_name: str) -> int:
    if table_name not in TABLE_POLICIES:
        message = "unsupported test table"
        raise ValueError(message)
    result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
    return int(result.scalar_one())


async def assert_statement_rejected(
    connection: AsyncConnection,
    statement: TextClause,
    parameters: Mapping[str, object],
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await connection.execute(statement, parameters)
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


def installation_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO push_installations (
            id, user_id, installation_id, registration_token, platform,
            active, revoked_at, last_seen_at, created_at, updated_at
        ) VALUES (
            :id, :user_id, :installation_id, :registration_token, 'ios',
            true, NULL, :now, :now, :now
        )
        """,
    )


async def insert_installation(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(installation_insert_statement(), values)


def installation_values(*, user_id: str) -> dict[str, object]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "installation_id": unique_value("fid"),
        "registration_token": unique_value("registration"),
        "now": datetime.now(UTC),
    }


def outbox_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO push_notification_outbox (
            id, user_id, event_key, kind, title, body, data_json,
            target_installation_ids_json, completed_installation_ids_json,
            status, attempt_count, max_attempts, next_attempt_at,
            lease_owner, lease_expires_at, last_error_code, delivered_at,
            created_at, updated_at
        ) VALUES (
            :id, :user_id, :event_key, 'chat_message', 'New message',
            'Open VoxOS to view the message.', '{}'::json, '[]'::json,
            '[]'::json, 'pending', 0, 5, :now, NULL, NULL, NULL, NULL,
            :now, :now
        )
        """,
    )


async def insert_outbox(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(outbox_insert_statement(), values)


def outbox_values(*, user_id: str) -> dict[str, object]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "event_key": unique_value("event"),
        "now": datetime.now(UTC),
    }


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

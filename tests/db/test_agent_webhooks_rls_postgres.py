"""Opt-in PostgreSQL RLS enforcement tests for agent webhook definitions.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database whose application role is non-superuser and ``NOBYPASSRLS``.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from voxos.db.settings import normalize_async_database_url

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
TABLE_NAME = "agent_webhooks"
EXPECTED_POLICIES = {
    "rls_agent_webhooks_tenant",
    "rls_agent_webhooks_agent_webhook_select",
}


@pytest_asyncio.fixture
async def postgres_webhook_connection() -> AsyncIterator[AsyncConnection]:
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
            await assert_webhook_schema_is_migrated(connection)
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
async def test_agent_webhook_records_enforce_tenant_isolation(
    postgres_webhook_connection: AsyncConnection,
) -> None:
    connection = postgres_webhook_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    webhook_id = new_id()

    await set_rls_context(connection, user_id=tenant_a)
    await insert_webhook(connection, webhook_id=webhook_id, user_id=tenant_a)
    assert await visible_row_count(connection) == 1

    await set_rls_context(connection, user_id=tenant_b)
    assert await visible_row_count(connection) == 0
    updated = await connection.execute(
        text("UPDATE agent_webhooks SET enabled = false WHERE id = :webhook_id"),
        {"webhook_id": webhook_id},
    )
    deleted = await connection.execute(
        text("DELETE FROM agent_webhooks WHERE id = :webhook_id"),
        {"webhook_id": webhook_id},
    )
    assert updated.rowcount == 0
    assert deleted.rowcount == 0
    await assert_insert_rejected(connection, webhook_id=new_id(), user_id=tenant_a)


@pytest.mark.asyncio
async def test_agent_webhook_service_scope_is_read_only(
    postgres_webhook_connection: AsyncConnection,
) -> None:
    connection = postgres_webhook_connection
    tenant = unique_value("tenant")
    webhook_id = new_id()
    await set_rls_context(connection, user_id=tenant)
    await insert_webhook(connection, webhook_id=webhook_id, user_id=tenant)

    await set_rls_context(connection, service_scope="agent_worker")
    assert await visible_row_count(connection) == 0

    await set_rls_context(connection, service_scope="agent_webhook")
    assert await visible_row_count(connection) == 1
    updated = await connection.execute(
        text("UPDATE agent_webhooks SET enabled = false WHERE id = :webhook_id"),
        {"webhook_id": webhook_id},
    )
    deleted = await connection.execute(
        text("DELETE FROM agent_webhooks WHERE id = :webhook_id"),
        {"webhook_id": webhook_id},
    )
    assert updated.rowcount == 0
    assert deleted.rowcount == 0
    await assert_insert_rejected(connection, webhook_id=new_id(), user_id=tenant)


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
        pytest.fail(f"{POSTGRES_RLS_TEST_URL_ENV} must name a disposable test database")
    assert role["is_superuser"] is False
    assert role["bypasses_rls"] is False


async def assert_webhook_schema_is_migrated(connection: AsyncConnection) -> None:
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
        {"table_name": TABLE_NAME},
    )
    security = security_result.mappings().one_or_none()
    if security is None:
        pytest.fail("apply the agent webhooks migration to the disposable test database")
    assert (security["rls_enabled"], security["rls_forced"]) == (True, True)

    policy_result = await connection.execute(
        text(
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename = :table_name
            """,
        ),
        {"table_name": TABLE_NAME},
    )
    assert {str(row["policyname"]) for row in policy_result.mappings()} == EXPECTED_POLICIES


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
        {"user_id": user_id or "", "service_scope": service_scope or ""},
    )


async def insert_webhook(
    connection: AsyncConnection,
    *,
    webhook_id: str,
    user_id: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_webhooks (
                id, user_id, agent_id, name, instruction, token_hash, enabled,
                created_at, updated_at
            ) VALUES (
                :id, :user_id, :agent_id, 'Test webhook', 'Handle the event',
                :token_hash, true, now(), now()
            )
            """,
        ),
        {
            "id": webhook_id,
            "user_id": user_id,
            "agent_id": new_id(),
            "token_hash": "a" * 64,
        },
    )


async def assert_insert_rejected(
    connection: AsyncConnection,
    *,
    webhook_id: str,
    user_id: str,
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await insert_webhook(connection, webhook_id=webhook_id, user_id=user_id)
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def visible_row_count(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT count(*) FROM agent_webhooks"))
    return int(result.scalar_one())


def new_id() -> str:
    return str(uuid.uuid4())


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

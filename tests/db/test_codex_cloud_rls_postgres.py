"""Opt-in PostgreSQL RLS checks for Codex Cloud tenant state.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database whose application role is non-superuser and ``NOBYPASSRLS``.
"""

import os
import uuid
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from voxos.db.settings import normalize_async_database_url

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
TENANT_TABLES = ("codex_accounts", "codex_auth_sessions", "codex_cloud_tasks")


@pytest_asyncio.fixture
async def postgres_codex_connection() -> AsyncIterator[AsyncConnection]:
    database_url = os.environ.get(POSTGRES_RLS_TEST_URL_ENV)
    if database_url is None:
        pytest.skip(f"set {POSTGRES_RLS_TEST_URL_ENV} to run PostgreSQL RLS tests")
    async_database_url = normalize_async_database_url(database_url)
    if make_url(async_database_url).get_backend_name() != "postgresql":
        pytest.fail(f"{POSTGRES_RLS_TEST_URL_ENV} must use PostgreSQL")

    engine = create_async_engine(async_database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await _assert_safe_database(connection)
            await _assert_schema(connection)
            await connection.rollback()
            transaction = await connection.begin()
            try:
                yield connection
            finally:
                if transaction.is_active:
                    await transaction.rollback()
    finally:
        await _dispose_engine(engine)


@pytest.mark.asyncio
async def test_codex_account_and_auth_session_are_isolated_by_tenant(
    postgres_codex_connection: AsyncConnection,
) -> None:
    connection = postgres_codex_connection
    tenant_a = f"tenant-a-{uuid.uuid4()}"
    tenant_b = f"tenant-b-{uuid.uuid4()}"
    account_id = str(uuid.uuid4())
    auth_session_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    await _set_tenant(connection, tenant_a)
    await connection.execute(
        text(
            """
            INSERT INTO codex_accounts (
                id, user_id, status, operation_generation, authenticated_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, 'connected', 0, :now, :now, :now
            )
            """,
        ),
        {"id": account_id, "user_id": tenant_a, "now": now},
    )
    await connection.execute(
        text(
            """
            INSERT INTO codex_auth_sessions (
                id, user_id, account_id, status, expires_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, :account_id, 'queued', :expires_at, :now, :now
            )
            """,
        ),
        {
            "id": auth_session_id,
            "user_id": tenant_a,
            "account_id": account_id,
            "expires_at": now + timedelta(minutes=15),
            "now": now,
        },
    )

    await _set_tenant(connection, tenant_b)
    assert await _count(connection, "codex_accounts") == 0
    assert await _count(connection, "codex_auth_sessions") == 0
    update = await connection.execute(text("UPDATE codex_accounts SET status = 'connected'"))
    assert update.rowcount == 0
    await _assert_rls_rejected(
        connection,
        connection.execute(
            text(
                """
                INSERT INTO codex_accounts (
                    id, user_id, status, operation_generation, authenticated_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id, 'connected', 0, :now, :now, :now
                )
                """,
            ),
            {"id": str(uuid.uuid4()), "user_id": tenant_a, "now": now},
        ),
    )


@pytest.mark.asyncio
async def test_agent_worker_scope_can_claim_only_auth_sessions(
    postgres_codex_connection: AsyncConnection,
) -> None:
    connection = postgres_codex_connection
    tenant = f"tenant-{uuid.uuid4()}"
    account_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    await _set_tenant(connection, tenant)
    await connection.execute(
        text(
            """
            INSERT INTO codex_accounts (
                id, user_id, status, operation_generation, authenticated_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, 'connected', 0, :now, :now, :now
            )
            """,
        ),
        {"id": account_id, "user_id": tenant, "now": now},
    )
    await connection.execute(
        text(
            """
            INSERT INTO codex_auth_sessions (
                id, user_id, account_id, status, expires_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, :account_id, 'queued', :expires_at, :now, :now
            )
            """,
        ),
        {
            "id": str(uuid.uuid4()),
            "user_id": tenant,
            "account_id": account_id,
            "expires_at": now + timedelta(minutes=15),
            "now": now,
        },
    )

    await connection.execute(
        text("SELECT set_config('app.user_id', '', true)"),
    )
    await connection.execute(
        text("SELECT set_config('app.service_scope', 'agent_worker', true)"),
    )
    assert await _count(connection, "codex_auth_sessions") == 1
    assert await _count(connection, "codex_accounts") == 0
    assert await _count(connection, "codex_cloud_tasks") == 0


async def _assert_schema(connection: AsyncConnection) -> None:
    for table_name in TENANT_TABLES:
        result = await connection.execute(
            text(
                """
                SELECT rel.relrowsecurity, rel.relforcerowsecurity
                FROM pg_class AS rel
                JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
                WHERE namespace.nspname = current_schema() AND rel.relname = :table_name
                """,
            ),
            {"table_name": table_name},
        )
        assert result.one() == (True, True)


async def _assert_safe_database(connection: AsyncConnection) -> None:
    result = await connection.execute(
        text(
            """
            SELECT current_database(), roles.rolsuper, roles.rolbypassrls
            FROM pg_roles AS roles WHERE roles.rolname = current_user
            """,
        ),
    )
    database_name, is_superuser, bypasses_rls = result.one()
    if "test" not in str(database_name).lower():
        pytest.fail(f"{POSTGRES_RLS_TEST_URL_ENV} must name a disposable test database")
    assert is_superuser is False
    assert bypasses_rls is False


async def _set_tenant(connection: AsyncConnection, user_id: str) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def _count(connection: AsyncConnection, table_name: str) -> int:
    result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
    return int(result.scalar_one())


async def _assert_rls_rejected(
    connection: AsyncConnection,
    operation: Awaitable[object],
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await operation
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def _dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

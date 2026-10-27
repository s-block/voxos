"""Opt-in PostgreSQL RLS tests for Browser Account state.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database. The application role must be non-superuser and ``NOBYPASSRLS``.
The test runs in a transaction and retains no rows.
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
TENANT_TABLES = (
    "browser_accounts",
    "agent_browser_account_grants",
    "browser_account_run_authorizations",
    "browser_account_audit_events",
    "browser_account_leases",
)


@pytest_asyncio.fixture
async def postgres_browser_account_connection() -> AsyncIterator[AsyncConnection]:
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
            await assert_browser_account_schema_is_migrated(connection)
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
async def test_browser_account_tables_enforce_tenant_crud_isolation(
    postgres_browser_account_connection: AsyncConnection,
) -> None:
    connection = postgres_browser_account_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    now = datetime.now(UTC)
    provider_connection_id = new_id()
    agent_id = new_id()
    alternate_agent_id = new_id()
    session_id = new_id()
    alternate_session_id = new_id()
    account_id = new_id()
    alternate_account_id = new_id()
    authorization_id = new_id()

    await set_tenant_context(connection, tenant_a)
    await insert_dependencies(
        connection,
        user_id=tenant_a,
        provider_connection_id=provider_connection_id,
        agent_ids=(agent_id, alternate_agent_id),
        session_ids=(session_id, alternate_session_id),
        now=now,
    )
    await insert_account(
        connection,
        account_id=account_id,
        user_id=tenant_a,
        provider_connection_id=provider_connection_id,
        label="Primary account",
        now=now,
    )
    await insert_account(
        connection,
        account_id=alternate_account_id,
        user_id=tenant_a,
        provider_connection_id=provider_connection_id,
        label="Alternate account",
        now=now,
    )
    await insert_grant(
        connection,
        row_id=new_id(),
        user_id=tenant_a,
        agent_id=agent_id,
        account_id=account_id,
        now=now,
    )
    await insert_authorization(
        connection,
        row_id=authorization_id,
        user_id=tenant_a,
        account_id=account_id,
        agent_id=agent_id,
        session_id=session_id,
        now=now,
    )
    await insert_audit_event(
        connection,
        row_id=new_id(),
        user_id=tenant_a,
        account_id=account_id,
        authorization_id=authorization_id,
        now=now,
    )
    await insert_lease(
        connection,
        row_id=new_id(),
        user_id=tenant_a,
        account_id=account_id,
        now=now,
    )

    await set_tenant_context(connection, tenant_b)
    for table_name in TENANT_TABLES:
        assert await visible_row_count(connection, table_name) == 0
        update_result = await connection.execute(
            text(f'UPDATE "{table_name}" SET user_id = :user_id'),
            {"user_id": tenant_b},
        )
        assert update_result.rowcount == 0
        delete_result = await connection.execute(text(f'DELETE FROM "{table_name}"'))
        assert delete_result.rowcount == 0

    await assert_rls_insert_rejected(
        connection,
        insert_account(
            connection,
            account_id=new_id(),
            user_id=tenant_a,
            provider_connection_id=provider_connection_id,
            label="Forbidden account",
            now=now,
        ),
    )
    await assert_rls_insert_rejected(
        connection,
        insert_grant(
            connection,
            row_id=new_id(),
            user_id=tenant_a,
            agent_id=alternate_agent_id,
            account_id=account_id,
            now=now,
        ),
    )
    await assert_rls_insert_rejected(
        connection,
        insert_authorization(
            connection,
            row_id=new_id(),
            user_id=tenant_a,
            account_id=account_id,
            agent_id=alternate_agent_id,
            session_id=alternate_session_id,
            now=now,
        ),
    )
    await assert_rls_insert_rejected(
        connection,
        insert_audit_event(
            connection,
            row_id=new_id(),
            user_id=tenant_a,
            account_id=account_id,
            authorization_id=authorization_id,
            now=now,
        ),
    )
    await assert_rls_insert_rejected(
        connection,
        insert_lease(
            connection,
            row_id=new_id(),
            user_id=tenant_a,
            account_id=alternate_account_id,
            now=now,
        ),
    )


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
    assert role["is_superuser"] is False, "RLS tests require a non-superuser role"
    assert role["bypasses_rls"] is False, "RLS tests require a NOBYPASSRLS role"


async def assert_browser_account_schema_is_migrated(
    connection: AsyncConnection,
) -> None:
    for table_name in TENANT_TABLES:
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
        assert set(policy_result.scalars()) == {f"rls_{table_name}_tenant"}


async def set_tenant_context(connection: AsyncConnection, user_id: str) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def visible_row_count(connection: AsyncConnection, table_name: str) -> int:
    result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
    return int(result.scalar_one())


async def assert_rls_insert_rejected(
    connection: AsyncConnection,
    operation: Awaitable[None],
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await operation
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def insert_dependencies(
    connection: AsyncConnection,
    *,
    user_id: str,
    provider_connection_id: str,
    agent_ids: tuple[str, str],
    session_ids: tuple[str, str],
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO user_mcp_servers (
                id, user_id, label, transport, url, auth_type, enabled,
                non_admin_tools_enabled, trust_state, status, auth_config,
                safe_metadata, allowed_tools, discovered_tools, created_at, updated_at
            ) VALUES (
                :id, :user_id, 'Browser Use RLS', 'streamable_http',
                'https://api.browser-use.com/mcp', 'api_key_header', true,
                false, 'trusted', 'connected', '{}'::json, '{}'::json,
                '["run_session"]'::json, '["run_session"]'::json, :now, :now
            )
            """,
        ),
        {"id": provider_connection_id, "user_id": user_id, "now": now},
    )
    for index, agent_id in enumerate(agent_ids):
        await connection.execute(
            text(
                """
                INSERT INTO user_agents (
                    id, owner_user_id, slug, display_name, status, is_default,
                    created_at, updated_at
                ) VALUES (
                    :id, :user_id, :slug, 'RLS agent', 'active', false, :now, :now
                )
                """,
            ),
            {
                "id": agent_id,
                "user_id": user_id,
                "slug": f"browser-rls-{index}",
                "now": now,
            },
        )
    for session_id, agent_id in zip(session_ids, agent_ids, strict=True):
        await connection.execute(
            text(
                """
                INSERT INTO agent_sessions (
                    id, user_id, agent_id, provider, model, channel, title,
                    room_metadata, effective_tool_policy_json, status,
                    started_at, created_at, updated_at
                ) VALUES (
                    :id, :user_id, :agent_id, 'openai', 'gpt-5.6-sol', 'text',
                    'Browser RLS test', '{}'::json, '{}'::json, 'started',
                    :now, :now, :now
                )
                """,
            ),
            {"id": session_id, "user_id": user_id, "agent_id": agent_id, "now": now},
        )


async def insert_account(
    connection: AsyncConnection,
    *,
    account_id: str,
    user_id: str,
    provider_connection_id: str,
    label: str,
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO browser_accounts (
                id, user_id, label, provider, provider_connection_id,
                primary_origin, authentication_origins, authentication_mode,
                status, version, created_at, updated_at
            ) VALUES (
                :id, :user_id, :label, 'browser_use_cloud', :provider_connection_id,
                'https://example.com', '[]'::json, 'managed_profile',
                'ready', 1, :now, :now
            )
            """,
        ),
        {
            "id": account_id,
            "user_id": user_id,
            "label": label,
            "provider_connection_id": provider_connection_id,
            "now": now,
        },
    )


async def insert_grant(
    connection: AsyncConnection,
    *,
    row_id: str,
    user_id: str,
    agent_id: str,
    account_id: str,
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_browser_account_grants (
                id, user_id, agent_id, browser_account_id, created_at
            ) VALUES (:id, :user_id, :agent_id, :account_id, :now)
            """,
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "account_id": account_id,
            "now": now,
        },
    )


async def insert_authorization(
    connection: AsyncConnection,
    *,
    row_id: str,
    user_id: str,
    account_id: str,
    agent_id: str,
    session_id: str,
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO browser_account_run_authorizations (
                id, user_id, browser_account_id, agent_id, session_id, client_turn_id,
                approved_origins, task_summary, approved_by_user_id,
                approved_at, expires_at
            ) VALUES (
                :id, :user_id, :account_id, :agent_id, :session_id, :client_turn_id,
                '["https://example.com"]'::json, 'Read account data',
                :user_id, :now, :expires_at
            )
            """,
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "account_id": account_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "client_turn_id": new_id(),
            "now": now,
            "expires_at": now + timedelta(minutes=10),
        },
    )


async def insert_audit_event(
    connection: AsyncConnection,
    *,
    row_id: str,
    user_id: str,
    account_id: str,
    authorization_id: str,
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO browser_account_audit_events (
                id, user_id, browser_account_id, authorization_id,
                event_kind, outcome, created_at
            ) VALUES (
                :id, :user_id, :account_id, :authorization_id,
                'approval_created', 'succeeded', :now
            )
            """,
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "account_id": account_id,
            "authorization_id": authorization_id,
            "now": now,
        },
    )


async def insert_lease(
    connection: AsyncConnection,
    *,
    row_id: str,
    user_id: str,
    account_id: str,
    now: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO browser_account_leases (
                id, user_id, browser_account_id, owner_id, lease_kind,
                expires_at, heartbeat_at, active
            ) VALUES (
                :id, :user_id, :account_id, 'rls-test', 'task',
                :expires_at, :now, true
            )
            """,
        ),
        {
            "id": row_id,
            "user_id": user_id,
            "account_id": account_id,
            "expires_at": now + timedelta(minutes=5),
            "now": now,
        },
    )


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

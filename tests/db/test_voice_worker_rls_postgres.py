"""PostgreSQL enforcement tests for demand-driven voice worker RLS.

These tests are intentionally opt-in because SQLite cannot enforce PostgreSQL row
security. Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated, disposable PostgreSQL
database whose name contains ``test``. The configured role must be the same
non-superuser, non-``BYPASSRLS`` application role used by Voxos. Every test runs in
one transaction and rolls it back, so no fixture rows are retained.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Protocol, cast

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.elements import TextClause
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.db.settings import normalize_async_database_url
from voxos.voice.demand import database_now

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
VOICE_DEMAND_TABLE = "voice_call_demands"
VOICE_LEASE_TABLE = "voice_project_worker_leases"
VOICE_WARM_REQUEST_TABLE = "voice_worker_warm_requests"
VOICE_DEMAND_INDEXES = {
    "ix_voice_call_demands_agent_id",
    "ix_voice_call_demands_project_key",
    "ix_voice_call_demands_project_status_expires",
    "ix_voice_call_demands_status_expires_requested",
    "ix_voice_call_demands_status_updated",
    "ix_voice_call_demands_user_id",
    "ix_voice_call_demands_user_status_updated",
}
VOICE_LEASE_INDEXES = {
    "ix_voice_project_worker_leases_lease_expires_at",
    "ix_voice_project_worker_leases_owner_id",
}
VOICE_DEMAND_POLICIES = {
    "rls_voice_call_demands_tenant",
    "rls_voice_call_demands_voice_worker_delete",
    "rls_voice_call_demands_voice_worker_select",
    "rls_voice_call_demands_voice_worker_update",
}
VOICE_LEASE_POLICIES = {"rls_voice_project_worker_leases_voice_worker"}
VOICE_WARM_REQUEST_POLICIES = {
    "rls_voice_worker_warm_requests_tenant",
    "rls_voice_worker_warm_requests_voice_worker_delete",
    "rls_voice_worker_warm_requests_voice_worker_select",
}
VOICE_DEMAND_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "202607090002_voice_worker_demand.py"
)


class VoiceDemandMigration(Protocol):
    def upgrade(self) -> None: ...

    def downgrade(self) -> None: ...


def load_voice_demand_migration() -> VoiceDemandMigration:
    spec = spec_from_file_location(
        "voxos_voice_demand_migration",
        VOICE_DEMAND_MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(VoiceDemandMigration, module)


@pytest.mark.asyncio
async def test_voice_demand_migration_upgrades_from_reserved_revision(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    migration = load_voice_demand_migration()

    await connection.run_sync(run_voice_demand_downgrade, migration)
    await connection.execute(
        text("UPDATE alembic_version SET version_num = '202607090001'"),
    )

    revision_result = await connection.execute(
        text("SELECT version_num FROM alembic_version"),
    )
    assert revision_result.scalar_one() == "202607090001"
    table_result = await connection.execute(
        text("SELECT to_regclass('voice_call_demands')"),
    )
    assert table_result.scalar_one() is None

    await connection.run_sync(run_voice_demand_upgrade, migration)
    await assert_voice_schema_is_migrated(connection)


@pytest.mark.asyncio
async def test_voice_lifecycle_clock_is_not_transaction_start_time(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    transaction_time_result = await connection.execute(text("SELECT now()"))
    transaction_time = transaction_time_result.scalar_one()
    await asyncio.sleep(0.02)
    session = AsyncSession(bind=connection)
    try:
        observed_time = await database_now(session)
    finally:
        await session.close()

    assert observed_time > transaction_time


@pytest_asyncio.fixture
async def postgres_rls_connection() -> AsyncIterator[AsyncConnection]:
    database_url = os.environ.get(POSTGRES_RLS_TEST_URL_ENV)
    if database_url is None:
        pytest.skip(
            f"set {POSTGRES_RLS_TEST_URL_ENV} to run PostgreSQL RLS tests",
        )

    async_database_url = normalize_async_database_url(database_url)
    url = make_url(async_database_url)
    if url.get_backend_name() != "postgresql":
        pytest.fail(f"{POSTGRES_RLS_TEST_URL_ENV} must use PostgreSQL")

    engine = create_async_engine(async_database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await assert_safe_test_database(connection)
            await assert_voice_schema_is_migrated(connection)
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
async def test_voice_demands_enforce_tenant_read_and_write_isolation(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    demand_a = voice_demand_values(user_id=tenant_a)
    demand_b = voice_demand_values(user_id=tenant_b)

    await set_rls_context(connection, user_id=tenant_a)
    await insert_voice_demand(connection, demand_a)

    await set_rls_context(connection, user_id=tenant_b)
    assert await visible_row_count(connection, VOICE_DEMAND_TABLE) == 0
    update_result = await connection.execute(
        text(
            """
            UPDATE voice_call_demands
            SET room_name = 'forbidden-update'
            WHERE id = :demand_id
            """,
        ),
        {"demand_id": demand_a["id"]},
    )
    assert update_result.rowcount == 0
    delete_result = await connection.execute(
        text("DELETE FROM voice_call_demands WHERE id = :demand_id"),
        {"demand_id": demand_a["id"]},
    )
    assert delete_result.rowcount == 0
    await assert_statement_rejected(
        connection,
        voice_demand_insert_statement(),
        demand_a | {"id": new_id(), "runtime_session_id": new_id()},
    )

    await insert_voice_demand(connection, demand_b)
    assert await visible_row_count(connection, VOICE_DEMAND_TABLE) == 1

    await set_rls_context(connection, user_id=tenant_a)
    visible_ids = await connection.execute(
        text(
            """
            SELECT id
            FROM voice_call_demands
            WHERE id IN (:demand_a, :demand_b)
            ORDER BY id
            """,
        ),
        {"demand_a": demand_a["id"], "demand_b": demand_b["id"]},
    )
    assert tuple(visible_ids.scalars()) == (demand_a["id"],)


@pytest.mark.asyncio
async def test_voice_warm_requests_are_tenant_owned_and_worker_read_only(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    await assert_voice_warm_request_schema_is_migrated(connection)
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    warm_a = voice_warm_request_values(user_id=tenant_a)
    warm_b = voice_warm_request_values(user_id=tenant_b)

    await set_rls_context(connection, user_id=tenant_a)
    await connection.execute(voice_warm_request_insert_statement(), warm_a)

    await set_rls_context(connection, user_id=tenant_b)
    assert await visible_row_count(connection, VOICE_WARM_REQUEST_TABLE) == 0
    update_result = await connection.execute(
        text(
            """
            UPDATE voice_worker_warm_requests
            SET expires_at = :expires_at
            WHERE user_id = :user_id
            """,
        ),
        {
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "user_id": tenant_a,
        },
    )
    assert update_result.rowcount == 0
    delete_result = await connection.execute(
        text("DELETE FROM voice_worker_warm_requests WHERE user_id = :user_id"),
        {"user_id": tenant_a},
    )
    assert delete_result.rowcount == 0
    await assert_statement_rejected(
        connection,
        voice_warm_request_insert_statement(),
        warm_a | {"project_key": uuid.uuid4().hex + uuid.uuid4().hex},
    )
    await connection.execute(voice_warm_request_insert_statement(), warm_b)
    assert await visible_row_count(connection, VOICE_WARM_REQUEST_TABLE) == 1

    await set_rls_context(connection, service_scope="voice_worker")
    assert await visible_row_count(connection, VOICE_WARM_REQUEST_TABLE) == 2
    worker_update = await connection.execute(
        text(
            """
            UPDATE voice_worker_warm_requests
            SET expires_at = :expires_at
            WHERE user_id = :user_id
            """,
        ),
        {
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "user_id": tenant_a,
        },
    )
    assert worker_update.rowcount == 0
    worker_delete = await connection.execute(
        text("DELETE FROM voice_worker_warm_requests WHERE user_id = :user_id"),
        {"user_id": tenant_a},
    )
    assert worker_delete.rowcount == 1


@pytest.mark.asyncio
async def test_voice_worker_can_manage_demands_but_cannot_insert_them(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    demand_a = voice_demand_values(user_id=tenant_a)
    demand_b = voice_demand_values(user_id=tenant_b)

    await set_rls_context(connection, user_id=tenant_a)
    await insert_voice_demand(connection, demand_a)
    await set_rls_context(connection, user_id=tenant_b)
    await insert_voice_demand(connection, demand_b)

    await set_rls_context(connection, service_scope="voice_worker")
    visible_ids = await connection.execute(
        text(
            """
            SELECT id
            FROM voice_call_demands
            WHERE id IN (:demand_a, :demand_b)
            ORDER BY id
            """,
        ),
        {"demand_a": demand_a["id"], "demand_b": demand_b["id"]},
    )
    assert set(visible_ids.scalars()) == {demand_a["id"], demand_b["id"]}

    update_result = await connection.execute(
        text(
            """
            UPDATE voice_call_demands
            SET status = 'ready', worker_id = :worker_id
            WHERE id IN (:demand_a, :demand_b)
            """,
        ),
        {
            "worker_id": unique_value("worker"),
            "demand_a": demand_a["id"],
            "demand_b": demand_b["id"],
        },
    )
    assert update_result.rowcount == 2

    delete_result = await connection.execute(
        text("DELETE FROM voice_call_demands WHERE id = :demand_id"),
        {"demand_id": demand_b["id"]},
    )
    assert delete_result.rowcount == 1

    await assert_statement_rejected(
        connection,
        voice_demand_insert_statement(),
        voice_demand_values(user_id=tenant_a),
    )


@pytest.mark.asyncio
async def test_voice_worker_exclusively_manages_project_leases(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    lease = voice_lease_values()

    await set_rls_context(connection, service_scope="voice_worker")
    await connection.execute(voice_lease_insert_statement(), lease)
    assert await visible_lease_count(connection, str(lease["project_key"])) == 1

    await set_rls_context(connection, user_id=unique_value("tenant"))
    assert await visible_lease_count(connection, str(lease["project_key"])) == 0
    update_result = await connection.execute(
        text(
            """
            UPDATE voice_project_worker_leases
            SET status = 'ready'
            WHERE project_key = :project_key
            """,
        ),
        {"project_key": lease["project_key"]},
    )
    assert update_result.rowcount == 0
    delete_result = await connection.execute(
        text(
            "DELETE FROM voice_project_worker_leases WHERE project_key = :project_key",
        ),
        {"project_key": lease["project_key"]},
    )
    assert delete_result.rowcount == 0
    await assert_statement_rejected(
        connection,
        voice_lease_insert_statement(),
        voice_lease_values(),
    )

    await set_rls_context(connection, service_scope="voice_worker")
    update_result = await connection.execute(
        text(
            """
            UPDATE voice_project_worker_leases
            SET status = 'ready', worker_id = :worker_id
            WHERE project_key = :project_key
            """,
        ),
        {
            "project_key": lease["project_key"],
            "worker_id": unique_value("worker"),
        },
    )
    assert update_result.rowcount == 1
    delete_result = await connection.execute(
        text(
            "DELETE FROM voice_project_worker_leases WHERE project_key = :project_key",
        ),
        {"project_key": lease["project_key"]},
    )
    assert delete_result.rowcount == 1


@pytest.mark.asyncio
async def test_voice_worker_cannot_read_livekit_settings_or_secret_vault(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_id = unique_value("tenant")
    now = datetime.now(UTC)

    await set_rls_context(connection, user_id=tenant_id)
    await connection.execute(
        text(
            """
            INSERT INTO user_livekit_settings (
                id, user_id, livekit_url, created_at, updated_at
            ) VALUES (
                :id, :user_id, :livekit_url, :created_at, :updated_at
            )
            """,
        ),
        {
            "id": new_id(),
            "user_id": tenant_id,
            "livekit_url": "wss://tenant.livekit.invalid",
            "created_at": now,
            "updated_at": now,
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO user_secret_vault (
                id, user_id, provider, credential_id, purpose, ciphertext,
                nonce, encrypted_data_key, encryption_backend,
                encryption_key_id, encryption_algorithm, encryption_context,
                created_at, updated_at
            ) VALUES (
                :id, :user_id, 'livekit', :credential_id, 'livekit_api_key',
                :ciphertext, :nonce, NULL, 'local_aes_gcm', NULL,
                'AES-256-GCM', CAST(:encryption_context AS jsonb),
                :created_at, :updated_at
            )
            """,
        ),
        {
            "id": new_id(),
            "user_id": tenant_id,
            "credential_id": new_id(),
            "ciphertext": b"encrypted-secret",
            "nonce": b"unique-nonce",
            "encryption_context": "{}",
            "created_at": now,
            "updated_at": now,
        },
    )
    assert await visible_row_count(connection, "user_livekit_settings") == 1
    assert await visible_row_count(connection, "user_secret_vault") == 1

    await set_rls_context(connection, service_scope="voice_worker")
    assert await visible_row_count(connection, "user_livekit_settings") == 0
    assert await visible_row_count(connection, "user_secret_vault") == 0

    await set_rls_context(connection, service_scope="agent_worker")
    assert await visible_row_count(connection, "user_livekit_settings") == 0
    assert await visible_row_count(connection, "user_secret_vault") == 0

    policies = await connection.execute(
        text(
            """
            SELECT tablename, policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename IN ('user_livekit_settings', 'user_secret_vault')
              AND (
                  qual LIKE '%voice_worker%'
                  OR with_check LIKE '%voice_worker%'
                  OR policyname IN (
                      'rls_user_livekit_settings_agent_worker',
                      'rls_user_secret_vault_livekit_agent_worker'
                  )
              )
            """,
        ),
    )
    assert policies.all() == []


async def assert_safe_test_database(connection: AsyncConnection) -> None:
    result = await connection.execute(
        text(
            """
            SELECT current_database() AS database_name,
                   current_user AS role_name,
                   roles.rolsuper AS is_superuser,
                   roles.rolbypassrls AS bypasses_rls
            FROM pg_roles AS roles
            WHERE roles.rolname = current_user
            """,
        ),
    )
    role = result.mappings().one()
    database_name = str(role["database_name"])
    if "test" not in database_name.lower():
        pytest.fail(
            f"{POSTGRES_RLS_TEST_URL_ENV} must name a disposable test database",
        )
    assert role["is_superuser"] is False, "RLS tests require a non-superuser role"
    assert role["bypasses_rls"] is False, "RLS tests require a NOBYPASSRLS role"


async def assert_voice_schema_is_migrated(
    connection: AsyncConnection,
) -> None:
    result = await connection.execute(
        text(
            """
            SELECT rel.relname,
                   rel.relrowsecurity AS rls_enabled,
                   rel.relforcerowsecurity AS rls_forced
            FROM pg_class AS rel
            JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
            WHERE namespace.nspname = current_schema()
              AND rel.relname IN (
                  'voice_call_demands',
                  'voice_project_worker_leases'
              )
            """,
        ),
    )
    table_security = {str(row["relname"]): (row["rls_enabled"], row["rls_forced"]) for row in result.mappings()}
    assert table_security == {
        VOICE_DEMAND_TABLE: (True, True),
        VOICE_LEASE_TABLE: (True, True),
    }, "apply the voice worker demand migration to the disposable test database"

    index_result = await connection.execute(
        text(
            """
            SELECT tablename, indexname
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename IN (
                  'voice_call_demands',
                  'voice_project_worker_leases'
              )
            """,
        ),
    )
    indexes_by_table: dict[str, set[str]] = {
        VOICE_DEMAND_TABLE: set(),
        VOICE_LEASE_TABLE: set(),
    }
    for row in index_result.mappings():
        indexes_by_table[str(row["tablename"])].add(str(row["indexname"]))
    assert indexes_by_table[VOICE_DEMAND_TABLE] >= VOICE_DEMAND_INDEXES
    assert indexes_by_table[VOICE_LEASE_TABLE] >= VOICE_LEASE_INDEXES

    policy_result = await connection.execute(
        text(
            """
            SELECT tablename, policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename IN (
                  'voice_call_demands',
                  'voice_project_worker_leases'
              )
            """,
        ),
    )
    policies_by_table: dict[str, set[str]] = {
        VOICE_DEMAND_TABLE: set(),
        VOICE_LEASE_TABLE: set(),
    }
    for row in policy_result.mappings():
        policies_by_table[str(row["tablename"])].add(str(row["policyname"]))
    assert policies_by_table == {
        VOICE_DEMAND_TABLE: VOICE_DEMAND_POLICIES,
        VOICE_LEASE_TABLE: VOICE_LEASE_POLICIES,
    }


async def assert_voice_warm_request_schema_is_migrated(
    connection: AsyncConnection,
) -> None:
    security_result = await connection.execute(
        text(
            """
            SELECT rel.relrowsecurity AS rls_enabled,
                   rel.relforcerowsecurity AS rls_forced
            FROM pg_class AS rel
            JOIN pg_namespace AS namespace ON namespace.oid = rel.relnamespace
            WHERE namespace.nspname = current_schema()
              AND rel.relname = 'voice_worker_warm_requests'
            """,
        ),
    )
    security = security_result.mappings().one_or_none()
    assert security is not None, "apply the voice worker warm request migration"
    assert (security["rls_enabled"], security["rls_forced"]) == (True, True)
    nullable_result = await connection.execute(
        text(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'voice_worker_warm_requests'
              AND column_name = 'expires_at'
            """,
        ),
    )
    assert nullable_result.scalar_one() == "YES"
    policy_result = await connection.execute(
        text(
            """
            SELECT policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename = 'voice_worker_warm_requests'
            """,
        ),
    )
    assert {str(row["policyname"]) for row in policy_result.mappings()} == (VOICE_WARM_REQUEST_POLICIES)


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
    if table_name not in {
        VOICE_DEMAND_TABLE,
        VOICE_LEASE_TABLE,
        VOICE_WARM_REQUEST_TABLE,
        "user_livekit_settings",
        "user_secret_vault",
    }:
        message = "unsupported test table"
        raise ValueError(message)
    result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
    return int(result.scalar_one())


async def visible_lease_count(
    connection: AsyncConnection,
    project_key: str,
) -> int:
    result = await connection.execute(
        text(
            """
            SELECT count(*)
            FROM voice_project_worker_leases
            WHERE project_key = :project_key
            """,
        ),
        {"project_key": project_key},
    )
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


def voice_demand_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO voice_call_demands (
            id, user_id, agent_id, runtime_session_id, room_name, source,
            project_key, status, worker_id, failure_code, failure_type,
            requested_at, ready_at, active_at, completed_at, expires_at,
            created_at, updated_at
        ) VALUES (
            :id, :user_id, :agent_id, :runtime_session_id, :room_name, :source,
            :project_key, :status, NULL, NULL, NULL,
            :requested_at, NULL, NULL, NULL, :expires_at,
            :created_at, :updated_at
        )
        """,
    )


async def insert_voice_demand(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(voice_demand_insert_statement(), values)


def voice_demand_values(*, user_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": new_id(),
        "user_id": user_id,
        "agent_id": new_id(),
        "runtime_session_id": new_id(),
        "room_name": unique_value("room"),
        "source": "rls_test",
        "project_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "status": "pending",
        "requested_at": now,
        "expires_at": now + timedelta(minutes=5),
        "created_at": now,
        "updated_at": now,
    }


def voice_warm_request_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO voice_worker_warm_requests (
            user_id, project_key, requested_at, expires_at, created_at, updated_at
        ) VALUES (
            :user_id, :project_key, :requested_at, :expires_at, :created_at, :updated_at
        )
        """,
    )


def voice_warm_request_values(*, user_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "user_id": user_id,
        "project_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "requested_at": now,
        "expires_at": None,
        "created_at": now,
        "updated_at": now,
    }


def voice_lease_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO voice_project_worker_leases (
            project_key, owner_id, status, worker_id, lease_expires_at,
            created_at, updated_at
        ) VALUES (
            :project_key, :owner_id, :status, NULL, :lease_expires_at,
            :created_at, :updated_at
        )
        """,
    )


def voice_lease_values() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "project_key": uuid.uuid4().hex + uuid.uuid4().hex,
        "owner_id": unique_value("supervisor"),
        "status": "starting",
        "lease_expires_at": now + timedelta(minutes=5),
        "created_at": now,
        "updated_at": now,
    }


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()


def run_voice_demand_downgrade(
    connection: Connection,
    migration: VoiceDemandMigration,
) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.downgrade()


def run_voice_demand_upgrade(
    connection: Connection,
    migration: VoiceDemandMigration,
) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()

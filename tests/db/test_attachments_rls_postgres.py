"""Opt-in PostgreSQL RLS tests for chat image attachment metadata.

Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated disposable PostgreSQL test
database. The application role must be non-superuser and ``NOBYPASSRLS``.
Every test runs in a transaction and retains no attachment or chat rows.
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
ATTACHMENT_POLICY = "rls_message_attachments_tenant"
ATTACHMENT_WORKER_SELECT_POLICY = "rls_message_attachments_attachment_worker_select"
ATTACHMENT_WORKER_UPDATE_POLICY = "rls_message_attachments_attachment_worker_update"


@pytest_asyncio.fixture
async def postgres_attachment_connection() -> AsyncIterator[AsyncConnection]:
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
            await assert_attachment_schema_is_migrated(connection)
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
async def test_attachment_metadata_enforces_tenant_crud_isolation(
    postgres_attachment_connection: AsyncConnection,
) -> None:
    connection = postgres_attachment_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    chat_a = chat_values(user_id=tenant_a)
    attachment_a = attachment_values(
        user_id=tenant_a,
        chat_id=str(chat_a["id"]),
    )

    await set_tenant_context(connection, tenant_a)
    await insert_chat(connection, chat_a)
    await insert_attachment(connection, attachment_a)

    await set_tenant_context(connection, tenant_b)
    assert await visible_attachment_count(connection) == 0
    update_result = await connection.execute(
        text(
            """
            UPDATE message_attachments
            SET state = 'deleted'
            WHERE id = :attachment_id
            """,
        ),
        {"attachment_id": attachment_a["id"]},
    )
    assert update_result.rowcount == 0
    delete_result = await connection.execute(
        text("DELETE FROM message_attachments WHERE id = :attachment_id"),
        {"attachment_id": attachment_a["id"]},
    )
    assert delete_result.rowcount == 0
    await assert_attachment_insert_rejected(
        connection,
        attachment_a | {"id": new_id()},
    )

    chat_b = chat_values(user_id=tenant_b)
    attachment_b = attachment_values(
        user_id=tenant_b,
        chat_id=str(chat_b["id"]),
    )
    await insert_chat(connection, chat_b)
    await insert_attachment(connection, attachment_b)
    assert await visible_attachment_count(connection) == 1

    await set_tenant_context(connection, tenant_a)
    visible_ids = await connection.execute(
        text(
            """
            SELECT id
            FROM message_attachments
            WHERE id IN (:attachment_a, :attachment_b)
            ORDER BY id
            """,
        ),
        {
            "attachment_a": attachment_a["id"],
            "attachment_b": attachment_b["id"],
        },
    )
    assert tuple(visible_ids.scalars()) == (attachment_a["id"],)

    await connection.execute(
        text(
            """
            UPDATE message_attachments
            SET state = 'deleted', deleted_at = :now
            WHERE id = :attachment_id
            """,
        ),
        {
            "attachment_id": attachment_a["id"],
            "now": datetime.now(UTC),
        },
    )
    await set_tenant_context(connection, tenant_b)
    await connection.execute(
        text(
            """
            UPDATE message_attachments
            SET state = 'deleted', deleted_at = :now
            WHERE id = :attachment_id
            """,
        ),
        {
            "attachment_id": attachment_b["id"],
            "now": datetime.now(UTC),
        },
    )
    await set_attachment_worker_context(connection)
    assert await visible_attachment_count(connection) == 2
    hidden_chats = await connection.execute(
        text("SELECT count(*) FROM agent_sessions"),
    )
    assert int(hidden_chats.scalar_one()) == 0


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


async def assert_attachment_schema_is_migrated(
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
              AND rel.relname = 'message_attachments'
            """,
        ),
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
              AND tablename = 'message_attachments'
            """,
        ),
    )
    assert set(policy_result.scalars()) == {
        ATTACHMENT_POLICY,
        ATTACHMENT_WORKER_SELECT_POLICY,
        ATTACHMENT_WORKER_UPDATE_POLICY,
    }


async def set_tenant_context(connection: AsyncConnection, user_id: str) -> None:
    await connection.execute(
        text("SELECT set_config('app.service_scope', '', true)"),
    )
    await connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def set_attachment_worker_context(connection: AsyncConnection) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', '', true)"),
    )
    await connection.execute(
        text(
            "SELECT set_config('app.service_scope', 'attachment_worker', true)",
        ),
    )


async def visible_attachment_count(connection: AsyncConnection) -> int:
    result = await connection.execute(
        text("SELECT count(*) FROM message_attachments"),
    )
    return int(result.scalar_one())


async def assert_attachment_insert_rejected(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    savepoint = await connection.begin_nested()
    try:
        with pytest.raises(DBAPIError):
            await insert_attachment(connection, values)
    finally:
        if savepoint.is_active:
            await savepoint.rollback()


async def insert_chat(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO agent_sessions (
                id, user_id, agent_id, provider, model, channel, title,
                room_metadata, effective_tool_policy_json, status, owner_authenticated,
                started_at, created_at, updated_at
            ) VALUES (
                :id, :user_id, NULL, 'openai', 'gpt-5.6-sol', 'text', 'RLS test',
                '{}'::json, '{}'::json, 'started', true,
                :now, :now, :now
            )
            """,
        ),
        values,
    )


async def insert_attachment(
    connection: AsyncConnection,
    values: Mapping[str, object],
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO message_attachments (
                id, user_id, chat_id, transcript_id, client_draft_id,
                client_source, position, state, display_name, mime_type,
                byte_size, sha256, width, height, object_key, thumbnail_key,
                deletion_attempts, deletion_error, created_at, updated_at,
                deleted_at
            ) VALUES (
                :id, :user_id, :chat_id, NULL, :client_draft_id,
                'web', NULL, 'ready', 'image.jpg', 'image/jpeg',
                128, :sha256, 32, 24, :object_key, :thumbnail_key,
                0, NULL, :now, :now, NULL
            )
            """,
        ),
        values,
    )


def chat_values(*, user_id: str) -> dict[str, object]:
    return {
        "id": new_id(),
        "user_id": user_id,
        "now": datetime.now(UTC),
    }


def attachment_values(
    *,
    user_id: str,
    chat_id: str,
) -> dict[str, object]:
    attachment_id = new_id()
    return {
        "id": attachment_id,
        "user_id": user_id,
        "chat_id": chat_id,
        "client_draft_id": new_id(),
        "sha256": "a" * 64,
        "object_key": f"chat-images/test/{attachment_id}/image",
        "thumbnail_key": f"chat-images/test/{attachment_id}/thumbnail",
        "now": datetime.now(UTC),
    }


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

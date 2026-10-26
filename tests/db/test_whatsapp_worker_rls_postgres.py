"""PostgreSQL enforcement tests for WhatsApp linked-device worker RLS.

These tests are intentionally opt-in because SQLite cannot enforce PostgreSQL row
security and row-lock behavior. Set ``VOXOS_RLS_TEST_DATABASE_URL`` to a migrated,
disposable PostgreSQL database whose name contains ``test``. The configured role
must be the same non-superuser, non-``BYPASSRLS`` application role used by Voxos.
Most tests run in one transaction and roll it back. The concurrency test commits
unique fixture rows so separate connections can see them, then deletes those rows
in a ``finally`` block.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.sql.elements import TextClause
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.engine import create_session_factory
from voxos.db.session import SqlAlchemyAsyncSessionFactory
from voxos.db.settings import normalize_async_database_url
from voxos.db.tenant import tenant_scoped_session_factory, whatsapp_worker_session_factory
from voxos.integrations.models import (
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
)
from voxos.integrations.records import (
    IntegrationConversationRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
)
from voxos.integrations.reply_dispatch import (
    CommunicationReplyDispatcher,
    CommunicationReplyDispatchResult,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import (
    WhatsAppOutboxRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppSessionRecord,
)
from voxos.whatsapp.store import (
    WhatsAppLeaseFence,
    WhatsAppWorkerStore,
)

POSTGRES_RLS_TEST_URL_ENV = "VOXOS_RLS_TEST_DATABASE_URL"
WHATSAPP_CONTACT_IDENTITY_TABLE = "whatsapp_contact_identities"
WHATSAPP_MOBILE_REGISTRATION_TABLE = "whatsapp_mobile_registrations"
WHATSAPP_SESSION_TABLE = "whatsapp_sessions"
WHATSAPP_KEY_TABLE = "whatsapp_session_keys"
WHATSAPP_LEASE_TABLE = "whatsapp_session_leases"
WHATSAPP_OUTBOX_TABLE = "whatsapp_outbox"
WHATSAPP_TENANT_TABLES = (
    WHATSAPP_CONTACT_IDENTITY_TABLE,
    WHATSAPP_MOBILE_REGISTRATION_TABLE,
    WHATSAPP_SESSION_TABLE,
    WHATSAPP_KEY_TABLE,
    WHATSAPP_OUTBOX_TABLE,
)
WHATSAPP_TABLES = (*WHATSAPP_TENANT_TABLES, WHATSAPP_LEASE_TABLE)
EXPECTED_POLICIES = {
    WHATSAPP_CONTACT_IDENTITY_TABLE: {
        "rls_whatsapp_contact_identities_tenant",
    },
    WHATSAPP_MOBILE_REGISTRATION_TABLE: {
        "rls_whatsapp_mobile_registrations_tenant",
    },
    WHATSAPP_SESSION_TABLE: {
        "rls_whatsapp_sessions_tenant",
        "rls_whatsapp_sessions_whatsapp_worker_select",
        "rls_whatsapp_sessions_whatsapp_worker_update",
    },
    WHATSAPP_KEY_TABLE: {"rls_whatsapp_session_keys_tenant"},
    WHATSAPP_LEASE_TABLE: {
        "rls_whatsapp_session_leases_whatsapp_worker",
    },
    WHATSAPP_OUTBOX_TABLE: {
        "rls_whatsapp_outbox_tenant",
        "rls_whatsapp_outbox_whatsapp_worker_select",
        "rls_whatsapp_outbox_whatsapp_worker_update",
    },
}


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
            await assert_whatsapp_schema_is_migrated(connection)
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
async def test_whatsapp_schema_forces_rls_and_has_only_expected_policies(
    postgres_rls_connection: AsyncConnection,
) -> None:
    await assert_whatsapp_schema_is_migrated(postgres_rls_connection)


@pytest.mark.asyncio
async def test_whatsapp_tenant_tables_reject_wrong_user_reads_and_writes(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    rows_a = whatsapp_tenant_values(user_id=tenant_a)
    rows_b = whatsapp_tenant_values(user_id=tenant_b)

    await set_rls_context(connection, user_id=tenant_a)
    await insert_whatsapp_tenant_rows(connection, rows_a)

    await set_rls_context(connection, user_id=tenant_b)
    for table_name in WHATSAPP_TENANT_TABLES:
        assert await visible_row_count(connection, table_name) == 0

    session_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_sessions
            SET state = 'reconnecting'
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows_a[WHATSAPP_SESSION_TABLE]["id"]},
    )
    assert session_update.rowcount == 0
    key_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_session_keys
            SET revision = revision + 1
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows_a[WHATSAPP_KEY_TABLE]["id"]},
    )
    assert key_update.rowcount == 0
    outbox_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_outbox
            SET status = 'submitting'
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows_a[WHATSAPP_OUTBOX_TABLE]["id"]},
    )
    assert outbox_update.rowcount == 0
    registration_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_mobile_registrations
            SET state = 'code_received'
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows_a[WHATSAPP_MOBILE_REGISTRATION_TABLE]["id"]},
    )
    assert registration_update.rowcount == 0
    identity_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_contact_identities
            SET display_name = 'forbidden-update'
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows_a[WHATSAPP_CONTACT_IDENTITY_TABLE]["id"]},
    )
    assert identity_update.rowcount == 0

    for table_name in WHATSAPP_TENANT_TABLES:
        delete_result = await connection.execute(
            text(f'DELETE FROM "{table_name}" WHERE id = :row_id'),
            {"row_id": rows_a[table_name]["id"]},
        )
        assert delete_result.rowcount == 0

    rejected_rows = whatsapp_tenant_values(user_id=tenant_a)
    await assert_statement_rejected(
        connection,
        whatsapp_contact_identity_insert_statement(),
        rejected_rows[WHATSAPP_CONTACT_IDENTITY_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_mobile_registration_insert_statement(),
        rejected_rows[WHATSAPP_MOBILE_REGISTRATION_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_session_insert_statement(),
        rejected_rows[WHATSAPP_SESSION_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_key_insert_statement(),
        rejected_rows[WHATSAPP_KEY_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_outbox_insert_statement(),
        rejected_rows[WHATSAPP_OUTBOX_TABLE],
    )

    await insert_whatsapp_tenant_rows(connection, rows_b)
    for table_name in WHATSAPP_TENANT_TABLES:
        assert await visible_row_count(connection, table_name) == 1

    await set_rls_context(connection, user_id=tenant_a)
    for table_name in WHATSAPP_TENANT_TABLES:
        visible_ids = await visible_ids_for(
            connection,
            table_name=table_name,
            row_ids={
                str(rows_a[table_name]["id"]),
                str(rows_b[table_name]["id"]),
            },
        )
        assert visible_ids == {str(rows_a[table_name]["id"])}


@pytest.mark.asyncio
async def test_whatsapp_worker_can_select_and_update_only_sessions_and_outbox(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_a = unique_value("tenant-a")
    tenant_b = unique_value("tenant-b")
    rows_a = whatsapp_tenant_values(user_id=tenant_a)
    rows_b = whatsapp_tenant_values(user_id=tenant_b)

    await set_rls_context(connection, user_id=tenant_a)
    await insert_whatsapp_tenant_rows(connection, rows_a)
    await set_rls_context(connection, user_id=tenant_b)
    await insert_whatsapp_tenant_rows(connection, rows_b)

    await set_rls_context(connection, service_scope="whatsapp_worker")
    assert await visible_ids_for(
        connection,
        table_name=WHATSAPP_SESSION_TABLE,
        row_ids={
            str(rows_a[WHATSAPP_SESSION_TABLE]["id"]),
            str(rows_b[WHATSAPP_SESSION_TABLE]["id"]),
        },
    ) == {
        str(rows_a[WHATSAPP_SESSION_TABLE]["id"]),
        str(rows_b[WHATSAPP_SESSION_TABLE]["id"]),
    }
    assert (
        await visible_row_count(
            connection,
            WHATSAPP_MOBILE_REGISTRATION_TABLE,
        )
        == 0
    )
    assert await visible_row_count(connection, WHATSAPP_CONTACT_IDENTITY_TABLE) == 0
    assert await visible_ids_for(
        connection,
        table_name=WHATSAPP_OUTBOX_TABLE,
        row_ids={
            str(rows_a[WHATSAPP_OUTBOX_TABLE]["id"]),
            str(rows_b[WHATSAPP_OUTBOX_TABLE]["id"]),
        },
    ) == {
        str(rows_a[WHATSAPP_OUTBOX_TABLE]["id"]),
        str(rows_b[WHATSAPP_OUTBOX_TABLE]["id"]),
    }

    session_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_sessions
            SET state = 'reconnecting'
            WHERE id IN (:row_a, :row_b)
            """,
        ),
        {
            "row_a": rows_a[WHATSAPP_SESSION_TABLE]["id"],
            "row_b": rows_b[WHATSAPP_SESSION_TABLE]["id"],
        },
    )
    assert session_update.rowcount == 2
    outbox_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_outbox
            SET status = 'submitting'
            WHERE id IN (:row_a, :row_b)
            """,
        ),
        {
            "row_a": rows_a[WHATSAPP_OUTBOX_TABLE]["id"],
            "row_b": rows_b[WHATSAPP_OUTBOX_TABLE]["id"],
        },
    )
    assert outbox_update.rowcount == 2

    rejected_rows = whatsapp_tenant_values(user_id=tenant_a)
    await assert_statement_rejected(
        connection,
        whatsapp_contact_identity_insert_statement(),
        rejected_rows[WHATSAPP_CONTACT_IDENTITY_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_mobile_registration_insert_statement(),
        rejected_rows[WHATSAPP_MOBILE_REGISTRATION_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_session_insert_statement(),
        rejected_rows[WHATSAPP_SESSION_TABLE],
    )
    await assert_statement_rejected(
        connection,
        whatsapp_outbox_insert_statement(),
        rejected_rows[WHATSAPP_OUTBOX_TABLE],
    )

    for table_name, row in (
        (WHATSAPP_SESSION_TABLE, rows_a[WHATSAPP_SESSION_TABLE]),
        (WHATSAPP_OUTBOX_TABLE, rows_a[WHATSAPP_OUTBOX_TABLE]),
    ):
        delete_result = await connection.execute(
            text(f'DELETE FROM "{table_name}" WHERE id = :row_id'),
            {"row_id": row["id"]},
        )
        assert delete_result.rowcount == 0


@pytest.mark.asyncio
async def test_whatsapp_worker_cannot_read_session_keys_or_secret_vault(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    tenant_id = unique_value("tenant")
    rows = whatsapp_tenant_values(user_id=tenant_id)
    vault_row = whatsapp_vault_values(user_id=tenant_id)

    await set_rls_context(connection, user_id=tenant_id)
    await connection.execute(
        whatsapp_key_insert_statement(),
        rows[WHATSAPP_KEY_TABLE],
    )
    await connection.execute(whatsapp_vault_insert_statement(), vault_row)
    assert await visible_row_count(connection, WHATSAPP_KEY_TABLE) == 1
    assert await visible_row_count(connection, "user_secret_vault") == 1

    await set_rls_context(connection, service_scope="whatsapp_worker")
    assert await visible_row_count(connection, WHATSAPP_KEY_TABLE) == 0
    assert await visible_row_count(connection, "user_secret_vault") == 0

    key_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_session_keys
            SET revision = revision + 1
            WHERE id = :row_id
            """,
        ),
        {"row_id": rows[WHATSAPP_KEY_TABLE]["id"]},
    )
    assert key_update.rowcount == 0
    vault_update = await connection.execute(
        text(
            """
            UPDATE user_secret_vault
            SET ciphertext = :ciphertext
            WHERE id = :row_id
            """,
        ),
        {"row_id": vault_row["id"], "ciphertext": b"forbidden-update"},
    )
    assert vault_update.rowcount == 0

    worker_policies = await connection.execute(
        text(
            """
            SELECT tablename, policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
              AND tablename IN (
                  'whatsapp_session_keys',
                  'user_secret_vault'
              )
              AND (
                  qual LIKE '%whatsapp_worker%'
                  OR with_check LIKE '%whatsapp_worker%'
              )
            """,
        ),
    )
    assert worker_policies.all() == []


@pytest.mark.asyncio
async def test_whatsapp_worker_exclusively_manages_session_leases(
    postgres_rls_connection: AsyncConnection,
) -> None:
    connection = postgres_rls_connection
    lease = whatsapp_lease_values()

    await set_rls_context(connection, service_scope="whatsapp_worker")
    await connection.execute(whatsapp_lease_insert_statement(), lease)
    assert (
        await visible_lease_count(
            connection,
            integration_id=str(lease["integration_id"]),
        )
        == 1
    )

    await set_rls_context(connection, user_id=str(lease["user_id"]))
    assert (
        await visible_lease_count(
            connection,
            integration_id=str(lease["integration_id"]),
        )
        == 0
    )
    tenant_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_session_leases
            SET generation = generation + 1
            WHERE integration_id = :integration_id
            """,
        ),
        {"integration_id": lease["integration_id"]},
    )
    assert tenant_update.rowcount == 0
    tenant_delete = await connection.execute(
        text(
            """
            DELETE FROM whatsapp_session_leases
            WHERE integration_id = :integration_id
            """,
        ),
        {"integration_id": lease["integration_id"]},
    )
    assert tenant_delete.rowcount == 0
    await assert_statement_rejected(
        connection,
        whatsapp_lease_insert_statement(),
        whatsapp_lease_values(),
    )

    await set_rls_context(connection, service_scope="whatsapp_worker")
    worker_update = await connection.execute(
        text(
            """
            UPDATE whatsapp_session_leases
            SET generation = generation + 1, owner_id = :owner_id
            WHERE integration_id = :integration_id
            """,
        ),
        {
            "integration_id": lease["integration_id"],
            "owner_id": unique_value("worker"),
        },
    )
    assert worker_update.rowcount == 1
    worker_delete = await connection.execute(
        text(
            """
            DELETE FROM whatsapp_session_leases
            WHERE integration_id = :integration_id
            """,
        ),
        {"integration_id": lease["integration_id"]},
    )
    assert worker_delete.rowcount == 1


@pytest.mark.asyncio
async def test_whatsapp_dispatch_and_worker_completion_do_not_deadlock(
    postgres_rls_connection: AsyncConnection,
) -> None:
    """Guard the session -> outbox -> reply lock order on real PostgreSQL."""
    connection = postgres_rls_connection
    await connection.commit()
    engine = connection.engine
    base_factory = create_session_factory(engine)
    user_id = unique_value("tenant-lock-order")
    integration_id = new_id()
    resource_id = new_id()
    inbound_message_id = new_id()
    conversation_id = new_id()
    reply_id = new_id()
    outbox_id = new_id()
    session_id = new_id()
    credential_id = new_id()
    run_id = new_id()
    worker_id = unique_value("worker-lock-order")
    generation = 1
    now = datetime.now(UTC)
    lease_expires_at = now + timedelta(minutes=5)
    target_jid = "447700900123@s.whatsapp.net"
    reply_body = "PostgreSQL lock-order regression test"
    tenant_factory = tenant_scoped_session_factory(base_factory, user_id)
    worker_factory = whatsapp_worker_session_factory(base_factory)
    fence = WhatsAppLeaseFence(
        user_id=user_id,
        integration_id=integration_id,
        owner_id=worker_id,
        generation=generation,
        expires_at=lease_expires_at,
    )

    blocker_connection: AsyncConnection | None = None
    dispatch_connection: AsyncConnection | None = None
    worker_connection: AsyncConnection | None = None
    blocker_transaction = None
    dispatch_task: asyncio.Task[object] | None = None
    completion_task: asyncio.Task[object] | None = None
    try:
        async with tenant_factory() as session:
            session.add(
                IntegrationMessageRecord(
                    id=inbound_message_id,
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=resource_id,
                    provider=IntegrationProviderType.WHATSAPP.value,
                    direction=IntegrationDirection.INBOUND.value,
                    external_message_id=unique_value("inbound"),
                    from_number="+447700900123",
                    to_number="+447418606704",
                    body="hello",
                    status=IntegrationMessageStatus.RECEIVED.value,
                    payload_json={"remote_jid": target_jid},
                ),
            )
            session.add(
                IntegrationConversationRecord(
                    id=conversation_id,
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=resource_id,
                    provider=IntegrationProviderType.WHATSAPP.value,
                    medium="whatsapp",
                    remote_peer="+447700900123",
                    agent_id=new_id(),
                    runtime_session_id=new_id(),
                    expires_at=now + timedelta(hours=24),
                ),
            )
            session.add(
                IntegrationReplyRecord(
                    id=reply_id,
                    user_id=user_id,
                    inbound_message_id=inbound_message_id,
                    conversation_id=conversation_id,
                    agent_run_id=run_id,
                    status=IntegrationReplyStatus.GENERATED.value,
                    response_profile="whatsapp:v1",
                    generated_body=reply_body,
                ),
            )
            session.add(
                WhatsAppSessionRecord(
                    id=session_id,
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=resource_id,
                    source_resource_id=new_id(),
                    credential_id=credential_id,
                    desired_state=WhatsAppDesiredState.CONNECTED.value,
                    state=WhatsAppSessionState.CONNECTED.value,
                    phone_number="+447418606704",
                    validated_at=now,
                    connected_at=now,
                ),
            )
            session.add(
                WhatsAppOutboxRecord(
                    id=outbox_id,
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=resource_id,
                    reply_id=reply_id,
                    target_jid=target_jid,
                    body=reply_body,
                    status=WhatsAppOutboxStatus.SUBMITTING.value,
                    owner_id=worker_id,
                    lease_generation=generation,
                    submission_started_at=now,
                ),
            )
            await session.commit()
        async with worker_factory() as session:
            session.add(
                WhatsAppSessionLeaseRecord(
                    integration_id=integration_id,
                    user_id=user_id,
                    owner_id=worker_id,
                    generation=generation,
                    lease_expires_at=lease_expires_at,
                ),
            )
            await session.commit()

        blocker_connection = await engine.connect()
        dispatch_connection = await engine.connect()
        worker_connection = await engine.connect()
        dispatch_pid = await postgres_backend_pid(dispatch_connection)
        worker_pid = await postgres_backend_pid(worker_connection)
        blocker_transaction = await blocker_connection.begin()
        await set_rls_context(blocker_connection, user_id=user_id)
        locked_reply = await blocker_connection.execute(
            text(
                """
                SELECT id
                FROM integration_replies
                WHERE id = :reply_id
                FOR UPDATE
                """,
            ),
            {"reply_id": reply_id},
        )
        assert locked_reply.scalar_one() == reply_id

        dispatch_base_factory = SqlAlchemyAsyncSessionFactory(
            bind=dispatch_connection,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        dispatcher = CommunicationReplyDispatcher(
            session_factory=tenant_scoped_session_factory(
                dispatch_base_factory,
                user_id,
            ),
            secret_vault=cast(UserSecretVaultService, object()),
            public_base_url=None,
        )
        dispatch_task = asyncio.create_task(
            dispatcher.dispatch_whatsapp(
                user_id=user_id,
                run_id=run_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation_id,
                generated_body=reply_body,
            ),
        )
        await wait_for_backend_lock(
            connection=connection,
            backend_pid=dispatch_pid,
            task=dispatch_task,
        )

        worker_base_factory = SqlAlchemyAsyncSessionFactory(
            bind=worker_connection,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        worker_store = WhatsAppWorkerStore(
            whatsapp_worker_session_factory(worker_base_factory),
        )
        completion_task = asyncio.create_task(
            worker_store.complete_outbox(
                fence=fence,
                outbox_id=outbox_id,
                status=WhatsAppOutboxStatus.FAILED,
                failure_code="lock_order_test",
            ),
        )
        await wait_for_backend_lock(
            connection=connection,
            backend_pid=worker_pid,
            task=completion_task,
        )

        await blocker_transaction.rollback()
        dispatch_result, completion_result = await asyncio.wait_for(
            asyncio.gather(dispatch_task, completion_task),
            timeout=5,
        )
        typed_dispatch_result = cast(
            CommunicationReplyDispatchResult,
            dispatch_result,
        )
        assert typed_dispatch_result.whatsapp_outbox_id == outbox_id
        assert completion_result is True

        async with tenant_factory() as session:
            final_outbox = await session.get(WhatsAppOutboxRecord, outbox_id)
            final_reply = await session.get(IntegrationReplyRecord, reply_id)
        assert final_outbox is not None
        assert final_outbox.status == WhatsAppOutboxStatus.FAILED.value
        assert final_reply is not None
        assert final_reply.status == IntegrationReplyStatus.FAILED.value
    finally:
        if blocker_transaction is not None and blocker_transaction.is_active:
            await blocker_transaction.rollback()
        for task in (dispatch_task, completion_task):
            if task is not None and not task.done():
                task.cancel()
        pending_tasks = tuple(task for task in (dispatch_task, completion_task) if task is not None)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        for dedicated_connection in (
            blocker_connection,
            dispatch_connection,
            worker_connection,
        ):
            if dedicated_connection is not None:
                await dedicated_connection.close()
        await connection.rollback()
        async with tenant_factory() as session:
            for table_name, row_id in (
                (WHATSAPP_OUTBOX_TABLE, outbox_id),
                (WHATSAPP_SESSION_TABLE, session_id),
                ("integration_replies", reply_id),
                ("integration_conversations", conversation_id),
                ("integration_messages", inbound_message_id),
            ):
                await session.exec(
                    text(f'DELETE FROM "{table_name}" WHERE id = :row_id'),
                    params={"row_id": row_id},
                )
            await session.commit()
        async with worker_factory() as session:
            await session.exec(
                text(
                    """
                    DELETE FROM whatsapp_session_leases
                    WHERE integration_id = :integration_id
                    """,
                ),
                params={"integration_id": integration_id},
            )
            await session.commit()


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


async def assert_whatsapp_schema_is_migrated(
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
                      'whatsapp_mobile_registrations',
                      'whatsapp_contact_identities',
                      'whatsapp_sessions',
                      'whatsapp_session_keys',
                      'whatsapp_session_leases',
                      'whatsapp_outbox'
                  )
            """,
        ),
    )
    table_security = {
        str(row["relname"]): (row["rls_enabled"], row["rls_forced"]) for row in security_result.mappings()
    }
    assert table_security == dict.fromkeys(WHATSAPP_TABLES, (True, True)), (
        "apply the WhatsApp linked-device migration to the disposable test database"
    )

    policy_result = await connection.execute(
        text(
            """
            SELECT tablename, policyname
            FROM pg_policies
            WHERE schemaname = current_schema()
                  AND tablename IN (
                      'whatsapp_mobile_registrations',
                      'whatsapp_contact_identities',
                      'whatsapp_sessions',
                      'whatsapp_session_keys',
                      'whatsapp_session_leases',
                      'whatsapp_outbox'
                  )
            """,
        ),
    )
    policies_by_table: dict[str, set[str]] = {table_name: set() for table_name in WHATSAPP_TABLES}
    for row in policy_result.mappings():
        policies_by_table[str(row["tablename"])].add(str(row["policyname"]))
    assert policies_by_table == EXPECTED_POLICIES


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


async def postgres_backend_pid(connection: AsyncConnection) -> int:
    result = await connection.execute(text("SELECT pg_backend_pid()"))
    backend_pid = int(result.scalar_one())
    await connection.rollback()
    return backend_pid


async def wait_for_backend_lock(
    *,
    connection: AsyncConnection,
    backend_pid: int,
    task: asyncio.Task[object],
) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        if task.done():
            exception = task.exception()
            if exception is not None:
                raise exception
            pytest.fail("database operation completed before reaching the expected lock")
        result = await connection.execute(
            text(
                """
                SELECT wait_event_type
                FROM pg_stat_activity
                WHERE pid = :backend_pid
                """,
            ),
            {"backend_pid": backend_pid},
        )
        if result.scalar_one_or_none() == "Lock":
            return
        await asyncio.sleep(0.01)
    pytest.fail("database operation did not reach the expected row lock")


async def visible_row_count(
    connection: AsyncConnection,
    table_name: str,
) -> int:
    if table_name not in {*WHATSAPP_TABLES, "user_secret_vault"}:
        message = "unsupported test table"
        raise ValueError(message)
    result = await connection.execute(text(f'SELECT count(*) FROM "{table_name}"'))
    return int(result.scalar_one())


async def visible_ids_for(
    connection: AsyncConnection,
    *,
    table_name: str,
    row_ids: set[str],
) -> set[str]:
    if table_name not in WHATSAPP_TENANT_TABLES:
        message = "unsupported tenant table"
        raise ValueError(message)
    result = await connection.execute(
        text(
            f"""
            SELECT id
            FROM "{table_name}"
            WHERE id = ANY(CAST(:row_ids AS varchar[]))
            """,
        ),
        {"row_ids": sorted(row_ids)},
    )
    return {str(row_id) for row_id in result.scalars()}


async def visible_lease_count(
    connection: AsyncConnection,
    *,
    integration_id: str,
) -> int:
    result = await connection.execute(
        text(
            """
            SELECT count(*)
            FROM whatsapp_session_leases
            WHERE integration_id = :integration_id
            """,
        ),
        {"integration_id": integration_id},
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


def whatsapp_session_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_sessions (
            id, user_id, integration_id, resource_id, source_resource_id,
            credential_id, desired_state, state, link_method,
            phone_number, challenge_revision,
            challenge_request_revision, created_at, updated_at
        ) VALUES (
            :id, :user_id, :integration_id, :resource_id, :source_resource_id,
            :credential_id, :desired_state, :state, :link_method,
            :phone_number, :challenge_revision,
            :challenge_request_revision, :created_at, :updated_at
        )
        """,
    )


def whatsapp_contact_identity_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_contact_identities (
            id, user_id, integration_id, contact_id, phone_number, pn_jid,
            lid_jid, username, display_name, resolution_state, first_seen_at,
            last_seen_at, created_at, updated_at
        ) VALUES (
            :id, :user_id, :integration_id, NULL, :phone_number, :pn_jid,
            :lid_jid, NULL, :display_name, :resolution_state, :first_seen_at,
            :last_seen_at, :created_at, :updated_at
        )
        """,
    )


def whatsapp_mobile_registration_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_mobile_registrations (
            id, user_id, source_integration_id, source_resource_id, provider,
            phone_number, state, capture_expires_at, created_at,
            updated_at
        ) VALUES (
            :id, :user_id, :source_integration_id, :source_resource_id,
            :provider, :phone_number, :state, :capture_expires_at,
            :created_at, :updated_at
        )
        """,
    )


def whatsapp_key_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_session_keys (
            id, user_id, integration_id, key_category, logical_key_id,
            ciphertext, nonce, revision, created_at, updated_at
        ) VALUES (
            :id, :user_id, :integration_id, :key_category, :logical_key_id,
            :ciphertext, :nonce, :revision, :created_at, :updated_at
        )
        """,
    )


def whatsapp_outbox_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_outbox (
            id, user_id, integration_id, resource_id, reply_id, target_jid,
            body, status, queued_at, created_at, updated_at
        ) VALUES (
            :id, :user_id, :integration_id, :resource_id, :reply_id, :target_jid,
            :body, :status, :queued_at, :created_at, :updated_at
        )
        """,
    )


def whatsapp_lease_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO whatsapp_session_leases (
            integration_id, user_id, owner_id, generation, lease_expires_at,
            created_at, updated_at
        ) VALUES (
            :integration_id, :user_id, :owner_id, :generation, :lease_expires_at,
            :created_at, :updated_at
        )
        """,
    )


def whatsapp_vault_insert_statement() -> TextClause:
    return text(
        """
        INSERT INTO user_secret_vault (
            id, user_id, provider, credential_id, purpose, ciphertext,
            nonce, encrypted_data_key, encryption_backend, encryption_key_id,
            encryption_algorithm, encryption_context, created_at, updated_at
        ) VALUES (
            :id, :user_id, :provider, :credential_id, :purpose, :ciphertext,
            :nonce, NULL, :encryption_backend, NULL,
            :encryption_algorithm, CAST(:encryption_context AS jsonb),
            :created_at, :updated_at
        )
        """,
    )


async def insert_whatsapp_tenant_rows(
    connection: AsyncConnection,
    rows: Mapping[str, Mapping[str, object]],
) -> None:
    await connection.execute(
        whatsapp_contact_identity_insert_statement(),
        rows[WHATSAPP_CONTACT_IDENTITY_TABLE],
    )
    await connection.execute(
        whatsapp_mobile_registration_insert_statement(),
        rows[WHATSAPP_MOBILE_REGISTRATION_TABLE],
    )
    await connection.execute(
        whatsapp_session_insert_statement(),
        rows[WHATSAPP_SESSION_TABLE],
    )
    await connection.execute(
        whatsapp_key_insert_statement(),
        rows[WHATSAPP_KEY_TABLE],
    )
    await connection.execute(
        whatsapp_outbox_insert_statement(),
        rows[WHATSAPP_OUTBOX_TABLE],
    )


def whatsapp_tenant_values(
    *,
    user_id: str,
) -> dict[str, dict[str, object]]:
    now = datetime.now(UTC)
    integration_id = new_id()
    resource_id = new_id()
    return {
        WHATSAPP_CONTACT_IDENTITY_TABLE: {
            "id": new_id(),
            "user_id": user_id,
            "integration_id": integration_id,
            "phone_number": "+447700900123",
            "pn_jid": "447700900123@s.whatsapp.net",
            "lid_jid": f"{uuid.uuid4().int % 10**15 + 10**14}@lid",
            "display_name": "RLS test contact",
            "resolution_state": "unlinked",
            "first_seen_at": now,
            "last_seen_at": now,
            "created_at": now,
            "updated_at": now,
        },
        WHATSAPP_MOBILE_REGISTRATION_TABLE: {
            "id": new_id(),
            "user_id": user_id,
            "source_integration_id": new_id(),
            "source_resource_id": new_id(),
            "provider": "telnyx",
            "phone_number": "+447700901234",
            "state": "armed",
            "capture_expires_at": now + timedelta(minutes=10),
            "created_at": now,
            "updated_at": now,
        },
        WHATSAPP_SESSION_TABLE: {
            "id": new_id(),
            "user_id": user_id,
            "integration_id": integration_id,
            "resource_id": resource_id,
            "source_resource_id": new_id(),
            "credential_id": new_id(),
            "desired_state": "connected",
            "state": "awaiting_link",
            "link_method": "qr",
            "phone_number": "+447700901234",
            "challenge_revision": 0,
            "challenge_request_revision": 1,
            "created_at": now,
            "updated_at": now,
        },
        WHATSAPP_KEY_TABLE: {
            "id": new_id(),
            "user_id": user_id,
            "integration_id": integration_id,
            "key_category": "creds",
            "logical_key_id": "creds",
            "ciphertext": b"encrypted-session-key",
            "nonce": b"unique-key-nonce",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
        },
        WHATSAPP_OUTBOX_TABLE: {
            "id": new_id(),
            "user_id": user_id,
            "integration_id": integration_id,
            "resource_id": resource_id,
            "reply_id": new_id(),
            "target_jid": "447700900123@s.whatsapp.net",
            "body": "RLS test message",
            "status": "queued",
            "queued_at": now,
            "created_at": now,
            "updated_at": now,
        },
    }


def whatsapp_lease_values() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "integration_id": new_id(),
        "user_id": unique_value("tenant"),
        "owner_id": unique_value("worker"),
        "generation": 1,
        "lease_expires_at": now + timedelta(minutes=5),
        "created_at": now,
        "updated_at": now,
    }


def whatsapp_vault_values(*, user_id: str) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": new_id(),
        "user_id": user_id,
        "provider": "whatsapp",
        "credential_id": new_id(),
        "purpose": "whatsapp_session_data_key",
        "ciphertext": b"encrypted-data-key",
        "nonce": b"unique-vault-nonce",
        "encryption_backend": "local_aes_gcm",
        "encryption_algorithm": "AES-256-GCM",
        "encryption_context": "{}",
        "created_at": now,
        "updated_at": now,
    }


def unique_value(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def new_id() -> str:
    return str(uuid.uuid4())


async def dispose_engine(engine: AsyncEngine) -> None:
    await engine.dispose()

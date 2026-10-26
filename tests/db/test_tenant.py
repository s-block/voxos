import pytest
from sqlalchemy import text

from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import (
    TENANT_SERVICE_SCOPE_INFO_KEY,
    TENANT_SESSION_INFO_KEY,
    MissingTenantContextError,
    agent_webhook_session_factory,
    agent_worker_session_factory,
    attachment_worker_session_factory,
    context_tenant_session_factory,
    notification_worker_session_factory,
    set_tenant_user_id,
    tenant_scoped_session_factory,
    tenant_user_context,
    voice_worker_session_factory,
    whatsapp_worker_session_factory,
)


@pytest.mark.asyncio
async def test_set_tenant_user_id_rejects_empty_user_id(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        with pytest.raises(ValueError, match="must not be empty"):
            await set_tenant_user_id(session=session, user_id=" ")


@pytest.mark.asyncio
async def test_set_tenant_user_id_executes_safely_on_postgres_only_shape(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        if session.bind is not None and session.bind.dialect.name != "postgresql":
            return
        await set_tenant_user_id(session=session, user_id="user-123")
        result = await session.exec(text("SELECT current_setting('app.user_id', true)"))
        assert result.one() == "user-123"


@pytest.mark.asyncio
async def test_tenant_scoped_session_factory_sets_session_info(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = tenant_scoped_session_factory(
        session_factory=sqlite_session_factory,
        user_id=" user-123 ",
    )

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SESSION_INFO_KEY] == "user-123"


@pytest.mark.asyncio
async def test_context_tenant_session_factory_requires_context(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = context_tenant_session_factory(sqlite_session_factory)

    with pytest.raises(MissingTenantContextError):
        scoped_factory()


@pytest.mark.asyncio
async def test_context_tenant_session_factory_uses_task_local_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = context_tenant_session_factory(sqlite_session_factory)

    with tenant_user_context("user-123"):
        async with scoped_factory() as session:
            assert session.sync_session.info[TENANT_SESSION_INFO_KEY] == "user-123"


@pytest.mark.asyncio
async def test_agent_worker_session_factory_sets_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = agent_worker_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == "agent_worker"


@pytest.mark.asyncio
async def test_agent_webhook_session_factory_sets_narrow_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = agent_webhook_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == "agent_webhook"


@pytest.mark.asyncio
async def test_attachment_worker_session_factory_sets_narrow_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = attachment_worker_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == ("attachment_worker")


@pytest.mark.asyncio
async def test_voice_worker_session_factory_sets_narrow_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = voice_worker_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == "voice_worker"


@pytest.mark.asyncio
async def test_whatsapp_worker_session_factory_sets_narrow_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = whatsapp_worker_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == "whatsapp_worker"


@pytest.mark.asyncio
async def test_notification_worker_session_factory_sets_narrow_service_scope(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = notification_worker_session_factory(sqlite_session_factory)

    async with scoped_factory() as session:
        assert session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] == ("notification_worker")

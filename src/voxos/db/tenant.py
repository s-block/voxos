from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.db.session import AsyncSessionFactory

TENANT_USER_SETTING = "app.user_id"
TENANT_SERVICE_SCOPE_SETTING = "app.service_scope"
TENANT_SESSION_INFO_KEY = "voxos_tenant_user_id"
TENANT_SERVICE_SCOPE_INFO_KEY = "voxos_service_scope"
AGENT_WORKER_SERVICE_SCOPE = "agent_worker"
ATTACHMENT_WORKER_SERVICE_SCOPE = "attachment_worker"
INTEGRATION_WEBHOOK_SERVICE_SCOPE = "integration_webhook"
AGENT_WEBHOOK_SERVICE_SCOPE = "agent_webhook"
NOTIFICATION_WORKER_SERVICE_SCOPE = "notification_worker"
VOICE_WORKER_SERVICE_SCOPE = "voice_worker"
WHATSAPP_WORKER_SERVICE_SCOPE = "whatsapp_worker"
_current_tenant_user_id: ContextVar[str | None] = ContextVar(
    "voxos_current_tenant_user_id",
    default=None,
)


class MissingTenantContextError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("tenant user context is not set")


@dataclass(frozen=True, slots=True)
class TenantScope:
    user_id: str

    async def apply_to(self, session: AsyncSession) -> None:
        await set_tenant_user_id(session=session, user_id=self.user_id)


@dataclass(frozen=True, slots=True)
class TenantScopedSessionFactory:
    session_factory: AsyncSessionFactory
    user_id: str

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_scoped_session(
            session_factory=self.session_factory,
            user_id=self.user_id,
        )


@dataclass(frozen=True, slots=True)
class ContextTenantSessionFactory:
    session_factory: AsyncSessionFactory

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        return tenant_scoped_session(
            session_factory=self.session_factory,
            user_id=current_tenant_user_id(),
        )


@dataclass(frozen=True, slots=True)
class ServiceScopedSessionFactory:
    session_factory: AsyncSessionFactory
    service_scope: str

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        return service_scoped_session(
            session_factory=self.session_factory,
            service_scope=self.service_scope,
        )


def tenant_scoped_session_factory(
    session_factory: AsyncSessionFactory,
    user_id: str,
) -> AsyncSessionFactory:
    return TenantScopedSessionFactory(
        session_factory=session_factory,
        user_id=normalized_tenant_user_id(user_id),
    )


def context_tenant_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ContextTenantSessionFactory(session_factory=session_factory)


def agent_worker_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=AGENT_WORKER_SERVICE_SCOPE,
    )


def attachment_worker_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=ATTACHMENT_WORKER_SERVICE_SCOPE,
    )


def integration_webhook_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=INTEGRATION_WEBHOOK_SERVICE_SCOPE,
    )


def agent_webhook_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=AGENT_WEBHOOK_SERVICE_SCOPE,
    )


def notification_worker_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=NOTIFICATION_WORKER_SERVICE_SCOPE,
    )


def voice_worker_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=VOICE_WORKER_SERVICE_SCOPE,
    )


def whatsapp_worker_session_factory(
    session_factory: AsyncSessionFactory,
) -> AsyncSessionFactory:
    return ServiceScopedSessionFactory(
        session_factory=session_factory,
        service_scope=WHATSAPP_WORKER_SERVICE_SCOPE,
    )


@contextmanager
def tenant_user_context(user_id: str) -> Iterator[None]:
    token: Token[str | None] = _current_tenant_user_id.set(
        normalized_tenant_user_id(user_id),
    )
    try:
        yield
    finally:
        _current_tenant_user_id.reset(token)


def current_tenant_user_id() -> str:
    user_id = _current_tenant_user_id.get()
    if user_id is None:
        raise MissingTenantContextError
    return user_id


@asynccontextmanager
async def tenant_scoped_session(
    *,
    session_factory: AsyncSessionFactory,
    user_id: str,
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        await set_tenant_user_id(session=session, user_id=user_id)
        yield session


@asynccontextmanager
async def service_scoped_session(
    *,
    session_factory: AsyncSessionFactory,
    service_scope: str,
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        await set_service_scope(session=session, service_scope=service_scope)
        yield session


async def set_tenant_user_id(session: AsyncSession, user_id: str) -> None:
    stripped_user_id = normalized_tenant_user_id(user_id)
    session.sync_session.info[TENANT_SESSION_INFO_KEY] = stripped_user_id
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT set_config(:setting_name, :user_id, true)"),
        {
            "setting_name": TENANT_USER_SETTING,
            "user_id": stripped_user_id,
        },
    )


async def set_service_scope(session: AsyncSession, service_scope: str) -> None:
    stripped_scope = normalized_service_scope(service_scope)
    session.sync_session.info[TENANT_SERVICE_SCOPE_INFO_KEY] = stripped_scope
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text("SELECT set_config(:setting_name, :service_scope, true)"),
        {
            "setting_name": TENANT_SERVICE_SCOPE_SETTING,
            "service_scope": stripped_scope,
        },
    )


def normalized_tenant_user_id(user_id: str) -> str:
    stripped_user_id = user_id.strip()
    if not stripped_user_id:
        message = "tenant user id must not be empty"
        raise ValueError(message)
    return stripped_user_id


def normalized_service_scope(service_scope: str) -> str:
    stripped_scope = service_scope.strip()
    if stripped_scope not in {
        AGENT_WORKER_SERVICE_SCOPE,
        AGENT_WEBHOOK_SERVICE_SCOPE,
        ATTACHMENT_WORKER_SERVICE_SCOPE,
        INTEGRATION_WEBHOOK_SERVICE_SCOPE,
        NOTIFICATION_WORKER_SERVICE_SCOPE,
        VOICE_WORKER_SERVICE_SCOPE,
        WHATSAPP_WORKER_SERVICE_SCOPE,
    }:
        message = "unsupported service scope"
        raise ValueError(message)
    return stripped_scope


@event.listens_for(Session, "after_begin")
def set_rls_settings_on_transaction_begin(
    session: Session,
    _transaction: object,
    connection: Connection,
) -> None:
    if connection.dialect.name != "postgresql":
        return
    user_id = session.info.get(TENANT_SESSION_INFO_KEY)
    if isinstance(user_id, str):
        connection.execute(
            text("SELECT set_config(:setting_name, :user_id, true)"),
            {
                "setting_name": TENANT_USER_SETTING,
                "user_id": user_id,
            },
        )
    service_scope = session.info.get(TENANT_SERVICE_SCOPE_INFO_KEY)
    if isinstance(service_scope, str):
        connection.execute(
            text("SELECT set_config(:setting_name, :service_scope, true)"),
            {
                "setting_name": TENANT_SERVICE_SCOPE_SETTING,
                "service_scope": service_scope,
            },
        )

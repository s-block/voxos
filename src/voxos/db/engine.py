import ssl

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.db.session import AsyncSessionFactory, SqlAlchemyAsyncSessionFactory
from voxos.db.settings import DatabaseSettings, DatabaseTlsMode


def database_connect_args(settings: DatabaseSettings) -> dict[str, object]:
    if settings.database_tls_mode == DatabaseTlsMode.DRIVER_DEFAULT:
        return {}

    database_url = make_url(settings.async_database_url())
    if database_url.get_backend_name() != "postgresql":
        msg = "DATABASE_TLS_MODE=verify-full requires a PostgreSQL database"
        raise ValueError(msg)

    ca_file = settings.database_tls_ca_file
    ssl_context = ssl.create_default_context(
        cafile=str(ca_file) if ca_file is not None else None,
    )
    ssl_context.check_hostname = True
    ssl_context.verify_mode = ssl.CERT_REQUIRED
    return {"ssl": ssl_context}


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(
        settings.async_database_url(),
        connect_args=database_connect_args(settings),
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> AsyncSessionFactory:
    return SqlAlchemyAsyncSessionFactory(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def create_session_factory_from_settings(
    settings: DatabaseSettings,
) -> AsyncSessionFactory:
    return create_session_factory(create_database_engine(settings))

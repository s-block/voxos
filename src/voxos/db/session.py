from contextlib import AbstractAsyncContextManager
from typing import Protocol, TypeAlias

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

SqlAlchemyAsyncSessionFactory: TypeAlias = async_sessionmaker[AsyncSession]


class AsyncSessionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]:
        """Create an async SQLModel session context."""

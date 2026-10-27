import pytest
from sqlalchemy import text

from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_sessions_isolate_uncommitted_writes_and_rollbacks(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as setup:
        await setup.exec(text("CREATE TABLE transaction_fixture (id integer PRIMARY KEY)"))
        await setup.commit()

    async with sqlite_session_factory() as writer:
        await writer.exec(text("INSERT INTO transaction_fixture VALUES (1)"))
        async with sqlite_session_factory() as reader:
            result = await reader.exec(text("SELECT count(*) FROM transaction_fixture"))
            assert result.scalar_one() == 0
        await writer.commit()

    async with sqlite_session_factory() as reader:
        result = await reader.exec(text("SELECT count(*) FROM transaction_fixture"))
        assert result.scalar_one() == 1

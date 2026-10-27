from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from voxos.db.migrations import prepare_database_extensions


@pytest.mark.asyncio
async def test_prepare_database_extensions_enables_vector() -> None:
    connection = AsyncMock(spec=AsyncConnection)

    await prepare_database_extensions(connection)

    statement = connection.execute.await_args.args[0]
    assert str(statement) == "CREATE EXTENSION IF NOT EXISTS vector"
    connection.commit.assert_awaited_once_with()

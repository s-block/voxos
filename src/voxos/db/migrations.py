from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def prepare_database_extensions(connection: AsyncConnection) -> None:
    await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    await connection.commit()

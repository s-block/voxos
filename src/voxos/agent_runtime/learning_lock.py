from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.records import AgentLearningSettingsRecord


async def lock_learning_settings(db: AsyncSession, user_id: str) -> AgentLearningSettingsRecord:
    statement = (
        select(AgentLearningSettingsRecord).where(col(AgentLearningSettingsRecord.user_id) == user_id).with_for_update()
    )
    record = (await db.exec(statement)).one_or_none()
    if record is None:
        try:
            async with db.begin_nested():
                db.add(AgentLearningSettingsRecord(user_id=user_id))
                await db.flush()
        except IntegrityError:
            pass  # Another transaction inserted the account lock; read its committed row.
        record = (await db.exec(statement)).one()
    return record

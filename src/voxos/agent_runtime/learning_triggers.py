from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.effective_tool_policy import effective_tool_policy_from_json
from voxos.agent_runtime.records import AgentRunRecord, AgentSessionRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolReference
from voxos.db.models import utc_now


async def enqueue_error_reflection(
    db: AsyncSession, *, session: AgentSessionRecord, policy_json: dict[str, object]
) -> None:
    if session.agent_id is None or session.deleted_at is not None:
        return
    policy = effective_tool_policy_from_json(policy_json)
    if policy is None or not policy.allows(
        ToolReference(tool_name="record_agent_improvement", tool_source=ToolGrantScope.INTERNAL)
    ):
        return
    # One analysis per conversation per hour coalesces outages and duplicate callbacks.
    key = f"reflection:{session.id}:{utc_now().strftime('%Y%m%d%H')}"
    if (
        await db.exec(
            select(AgentRunRecord.id)
            .where(col(AgentRunRecord.user_id) == session.user_id, col(AgentRunRecord.idempotency_key) == key)
            .limit(1)
        )
    ).first():
        return
    record = AgentRunRecord(
        user_id=session.user_id,
        agent_id=session.agent_id,
        kind=AgentRunKind.REFLECTION.value,
        session_id=session.id,
        created_by_user_id=session.user_id,
        effective_tool_policy_json=policy_json,
        payload_json={"origin": session.id},
        idempotency_key=key,
        max_attempts=2,
    )
    record.root_run_id = record.id
    try:
        async with db.begin_nested():
            db.add(record)
            await db.flush()
    except IntegrityError:
        pass  # A concurrent terminal callback has already enqueued this trigger.

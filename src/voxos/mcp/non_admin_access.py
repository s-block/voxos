from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.mcp.models import McpServerStatus, McpTrustState
from voxos.mcp.records import UserMcpServerRecord


@dataclass(frozen=True, slots=True)
class NonAdminMcpToolAccess:
    server_id: str
    tool_names: tuple[str, ...]


async def non_admin_mcp_tool_access(
    *,
    session: AsyncSession,
    user_id: str,
) -> tuple[NonAdminMcpToolAccess, ...]:
    statement = select(UserMcpServerRecord).where(
        col(UserMcpServerRecord.user_id) == user_id,
        col(UserMcpServerRecord.non_admin_tools_enabled).is_(True),
        col(UserMcpServerRecord.enabled).is_(True),
        col(UserMcpServerRecord.trust_state) == McpTrustState.TRUSTED.value,
        col(UserMcpServerRecord.status) == McpServerStatus.CONNECTED.value,
    )
    result = await session.exec(statement)
    return tuple(
        NonAdminMcpToolAccess(
            server_id=record.id,
            tool_names=tuple(record.allowed_tools),
        )
        for record in result.all()
        if record.allowed_tools
    )

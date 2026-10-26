from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select

from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.history import ACCOUNT_LEARNING_TOOLS
from voxos.agent_runtime.tool_policy import ToolGrantScope
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import IntegrationProviderType
from voxos.integrations.records import IntegrationCallSessionRecord
from voxos.mcp.non_admin_access import (
    NonAdminMcpToolAccess,
    non_admin_mcp_tool_access,
)
from voxos.voice.source_policy import VoiceSessionSource

CALLER_ACCESS_PROVIDER_SOURCES = frozenset(
    {
        IntegrationProviderType.TELNYX.value,
        IntegrationProviderType.TWILIO.value,
    },
)


class VoiceCallerAccessLevel(StrEnum):
    OWNER = "owner"
    ADMIN_CONTACT = "admin_contact"
    LIMITED_CONTACT = "limited_contact"


class VoiceCallerAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: VoiceCallerAccessLevel
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    limited_mcp_tool_access: tuple[NonAdminMcpToolAccess, ...] = Field(default_factory=tuple)

    @property
    def allows_private_context(self) -> bool:
        return self.level in {
            VoiceCallerAccessLevel.OWNER,
            VoiceCallerAccessLevel.ADMIN_CONTACT,
        }


@dataclass(frozen=True, slots=True)
class VoiceCallerAccessResolver:
    session_factory: AsyncSessionFactory

    async def resolve(
        self,
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        source: str,
    ) -> VoiceCallerAccess:
        if source == VoiceSessionSource.LIVEKIT_API.value:
            return VoiceCallerAccess(level=VoiceCallerAccessLevel.OWNER)

        contact_display_name: str | None = None
        async with self.session_factory() as session:
            if source in CALLER_ACCESS_PROVIDER_SOURCES:
                statement = select(IntegrationCallSessionRecord).where(
                    col(IntegrationCallSessionRecord.user_id) == user_id,
                    col(IntegrationCallSessionRecord.runtime_session_id) == runtime_session_id,
                    col(IntegrationCallSessionRecord.provider) == source,
                )
                result = await session.exec(statement)
                records = tuple(result.all())
                if len(records) == 1 and records[0].agent_id == agent_id:
                    record = records[0]
                    if record.contact_id is not None and record.caller_is_admin:
                        return VoiceCallerAccess(
                            level=VoiceCallerAccessLevel.ADMIN_CONTACT,
                            contact_display_name=record.contact_display_name,
                        )
                    if record.contact_id is not None:
                        contact_display_name = record.contact_display_name
            limited_mcp_tool_access = await non_admin_mcp_tool_access(
                session=session,
                user_id=user_id,
            )
        return VoiceCallerAccess(
            level=VoiceCallerAccessLevel.LIMITED_CONTACT,
            contact_display_name=contact_display_name,
            limited_mcp_tool_access=limited_mcp_tool_access,
        )


def policy_for_caller_access(
    *,
    base_policy: EffectiveToolPolicySnapshot,
    caller_access: VoiceCallerAccess,
) -> EffectiveToolPolicySnapshot:
    if caller_access.level == VoiceCallerAccessLevel.OWNER:
        return base_policy
    if caller_access.level == VoiceCallerAccessLevel.ADMIN_CONTACT:
        if not any(entry.tool_name in ACCOUNT_LEARNING_TOOLS for entry in base_policy.entries):
            return base_policy
        return base_policy.model_copy(
            update={
                "entries": tuple(
                    entry for entry in base_policy.entries if entry.tool_name not in ACCOUNT_LEARNING_TOOLS
                )
            }
        )
    allowed_tools_by_server_id = {
        access.server_id: frozenset(access.tool_names) for access in caller_access.limited_mcp_tool_access
    }
    entries = tuple(
        entry
        for entry in base_policy.entries
        if (
            entry.tool_source == ToolGrantScope.MCP_SERVER
            and entry.mcp_server_id is not None
            and entry.tool_name in allowed_tools_by_server_id.get(entry.mcp_server_id, ())
        )
    )
    return EffectiveToolPolicySnapshot(
        user_id=base_policy.user_id,
        agent_id=base_policy.agent_id,
        parent_policy_id=base_policy.id,
        entries=entries,
    )

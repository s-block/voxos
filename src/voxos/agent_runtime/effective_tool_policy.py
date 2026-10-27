from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.tool_policy import (
    ToolGrant,
    ToolGrantScope,
    ToolGrantStatus,
    ToolReference,
    ToolRisk,
)
from voxos.agent_runtime.tool_routing import ResolvedToolRoute, ToolRoutingService
from voxos.db.models import new_record_id, utc_now

NON_INHERITED_SUBAGENT_TOOL_NAMES = frozenset(
    {
        "create_agent_schedule",
        "delegate_agent_task",
        "generate_image",
        "search_agent_history",
        "read_agent_history",
        "record_agent_improvement",
        "create_agent_memory",
        "propose_agent_memory",
        "create_user_skill",
        "update_user_skill",
        "confirm_agent_memory",
        "confirm_skill_proposal",
    },
)


class EffectiveToolPolicyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    tool_source: ToolGrantScope
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    risk: ToolRisk = ToolRisk.MEDIUM

    def matches(self, reference: ToolReference) -> bool:
        return (
            self.tool_name == reference.tool_name
            and self.tool_source == reference.tool_source
            and self.mcp_server_id == reference.mcp_server_id
            and self.integration_id == reference.integration_id
        )


class EffectiveToolPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=new_record_id, min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    parent_policy_id: str | None = Field(default=None, min_length=1, max_length=36)
    entries: tuple[EffectiveToolPolicyEntry, ...] = Field(default_factory=tuple)
    resolved_routes: tuple[ResolvedToolRoute, ...] = Field(default_factory=tuple)
    created_at: datetime = Field(default_factory=utc_now)

    def allows(self, reference: ToolReference) -> bool:
        return any(entry.matches(reference) for entry in self.entries)

    def allowed_tool_names(
        self,
        *,
        tool_source: ToolGrantScope,
        tool_names: tuple[str, ...],
        mcp_server_id: str | None,
        integration_id: str | None = None,
    ) -> tuple[str, ...]:
        allowed = {
            entry.tool_name
            for entry in self.entries
            if (
                entry.tool_source == tool_source
                and entry.mcp_server_id == mcp_server_id
                and entry.integration_id == integration_id
            )
        }
        return tuple(tool_name for tool_name in tool_names if tool_name in allowed)

    def to_json(self) -> JsonObject:
        return self.model_dump(mode="json")


class EffectiveToolPolicyAuthorizationServiceProtocol(Protocol):
    async def list_effective_grants(self, scope: AgentScope) -> tuple[ToolGrant, ...]:
        """Return persisted grants for the agent scope."""


class EffectiveToolPolicyService:
    def __init__(
        self,
        authorization_service: EffectiveToolPolicyAuthorizationServiceProtocol,
        routing_service: ToolRoutingService | None = None,
    ) -> None:
        self._authorization_service = authorization_service
        self._routing_service = routing_service

    async def snapshot_for_agent(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str | None = None,
        run_id: str | None = None,
        channel: str = "text",
        source: str = "agent_runtime.effective_tool_policy",
    ) -> EffectiveToolPolicySnapshot:
        scope = AgentScope(
            owner_user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            run_id=run_id,
            channel=channel,
            source=source,
        )
        grants = await self._authorization_service.list_effective_grants(scope)
        policy = policy_snapshot_from_grants(
            user_id=user_id,
            agent_id=agent_id,
            grants=grants,
        )
        if self._routing_service is None:
            return policy
        routes = await self._routing_service.resolve_routes(
            scope=scope,
            grants=grants,
        )
        return policy.model_copy(update={"resolved_routes": routes})

    async def snapshot_for_subagent(
        self,
        *,
        parent_policy: EffectiveToolPolicySnapshot,
        user_id: str,
        target_agent_id: str,
        session_id: str | None = None,
    ) -> EffectiveToolPolicySnapshot:
        target_policy = await self.snapshot_for_agent(
            user_id=user_id,
            agent_id=target_agent_id,
            session_id=session_id,
            source="agent_runtime.effective_tool_policy.subagent",
        )
        child_policy = intersect_policy_snapshots(
            parent_policy=parent_policy,
            target_policy=target_policy,
            user_id=user_id,
            agent_id=target_agent_id,
        )
        child_policy = child_policy.model_copy(
            update={
                "entries": tuple(
                    entry
                    for entry in child_policy.entries
                    if not (
                        entry.tool_source == ToolGrantScope.INTERNAL
                        and entry.tool_name in NON_INHERITED_SUBAGENT_TOOL_NAMES
                    )
                ),
            },
        )
        if self._routing_service is None:
            return child_policy
        routes = await self._routing_service.resolve_routes(
            scope=AgentScope(
                owner_user_id=user_id,
                agent_id=target_agent_id,
                session_id=session_id,
                channel="text",
                source="agent_runtime.effective_tool_policy.subagent",
            ),
            grants=tool_grants_from_policy_entries(
                user_id=user_id,
                agent_id=target_agent_id,
                entries=child_policy.entries,
            ),
        )
        return child_policy.model_copy(update={"resolved_routes": routes})


def policy_snapshot_from_grants(
    *,
    user_id: str,
    agent_id: str,
    grants: tuple[ToolGrant, ...],
) -> EffectiveToolPolicySnapshot:
    entries = tuple(
        EffectiveToolPolicyEntry(
            tool_name=grant.tool_name,
            tool_source=grant.tool_source,
            mcp_server_id=grant.mcp_server_id,
            integration_id=grant.integration_id,
            risk=grant.risk,
        )
        for grant in grants
        if grant.status == ToolGrantStatus.ALLOWED
    )
    return EffectiveToolPolicySnapshot(
        user_id=user_id,
        agent_id=agent_id,
        entries=entries,
    )


def intersect_policy_snapshots(
    *,
    parent_policy: EffectiveToolPolicySnapshot,
    target_policy: EffectiveToolPolicySnapshot,
    user_id: str,
    agent_id: str,
) -> EffectiveToolPolicySnapshot:
    parent_entries = {policy_entry_key(entry): entry for entry in parent_policy.entries}
    entries = tuple(entry for entry in target_policy.entries if policy_entry_key(entry) in parent_entries)
    return EffectiveToolPolicySnapshot(
        user_id=user_id,
        agent_id=agent_id,
        parent_policy_id=parent_policy.id,
        entries=entries,
    )


def effective_tool_policy_from_json(
    policy_json: JsonObject | None,
) -> EffectiveToolPolicySnapshot | None:
    if not policy_json:
        return None
    try:
        return EffectiveToolPolicySnapshot.model_validate(policy_json)
    except ValidationError:
        return None


def effective_tool_policy_id(policy_json: JsonObject | None) -> str | None:
    policy = effective_tool_policy_from_json(policy_json)
    if policy is None:
        return None
    return policy.id


def policy_entry_key(
    entry: EffectiveToolPolicyEntry,
) -> tuple[ToolGrantScope, str | None, str | None, str]:
    return (entry.tool_source, entry.mcp_server_id, entry.integration_id, entry.tool_name)


def tool_grants_from_policy_entries(
    *,
    user_id: str,
    agent_id: str,
    entries: tuple[EffectiveToolPolicyEntry, ...],
) -> tuple[ToolGrant, ...]:
    return tuple(
        ToolGrant(
            id=new_record_id(),
            user_id=user_id,
            agent_id=agent_id,
            tool_name=entry.tool_name,
            tool_source=entry.tool_source,
            mcp_server_id=entry.mcp_server_id,
            integration_id=entry.integration_id,
            status=ToolGrantStatus.ALLOWED,
            risk=entry.risk,
        )
        for entry in entries
    )

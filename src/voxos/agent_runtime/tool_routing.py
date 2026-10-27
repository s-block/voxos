from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import col, select

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.records import AgentToolRoutePreferenceRecord
from voxos.agent_runtime.tool_policy import (
    ToolGrant,
    ToolGrantScope,
    ToolGrantStatus,
    ToolReference,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.mcp.models import McpServerStatus, McpTrustState
from voxos.mcp.presets import mcp_preset_tool_route
from voxos.mcp.records import UserMcpServerRecord
from voxos.mcp.services import runtime_preset_id_for_record, safe_metadata_from_record

WEB_SEARCH_ACTION = "web.search"
PINNED_TOOL_REQUIRED_ERROR = "pinned tool routes require a tool selection"
AUTOMATIC_PINNED_TOOL_ERROR = "automatic tool routes must not include a pinned tool"
INVALID_PINNED_TOOL_ERROR = "pinned tool must be an allowed implementation of the selected action"
INCOMPLETE_PINNED_TOOL_ERROR = "persisted pinned tool route is incomplete"


class ToolRouteMode(StrEnum):
    AUTOMATIC = "automatic"
    PINNED = "pinned"


class ToolRouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    tool_source: ToolGrantScope
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)


class ToolRouteDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)


class ToolRoutePreference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str | None = Field(default=None, min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    action: str = Field(min_length=1, max_length=128)
    mode: ToolRouteMode = ToolRouteMode.AUTOMATIC
    pinned_tool: ToolRouteSelection | None = None

    @model_validator(mode="after")
    def validate_pinned_tool(self) -> Self:
        if self.mode == ToolRouteMode.PINNED and self.pinned_tool is None:
            raise ValueError(PINNED_TOOL_REQUIRED_ERROR)
        if self.mode == ToolRouteMode.AUTOMATIC and self.pinned_tool is not None:
            raise ValueError(AUTOMATIC_PINNED_TOOL_ERROR)
        return self


class ToolRoutePreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=128)
    mode: ToolRouteMode = ToolRouteMode.AUTOMATIC
    pinned_tool: ToolRouteSelection | None = None

    @model_validator(mode="after")
    def validate_pinned_tool(self) -> Self:
        if self.mode == ToolRouteMode.PINNED and self.pinned_tool is None:
            raise ValueError(PINNED_TOOL_REQUIRED_ERROR)
        if self.mode == ToolRouteMode.AUTOMATIC and self.pinned_tool is not None:
            raise ValueError(AUTOMATIC_PINNED_TOOL_ERROR)
        return self


class ResolvedToolRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=128)
    mode: ToolRouteMode
    selected_tool: ToolReference | None = None
    automatic_priority: int | None = Field(default=None, ge=0, le=10_000)


class ToolRouteCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = Field(min_length=1, max_length=128)
    provider_label: str = Field(min_length=1, max_length=256)
    reference: ToolReference
    automatic_priority: int = Field(ge=0, le=10_000)


TOOL_ROUTE_DEFINITIONS = (
    ToolRouteDefinition(
        action=WEB_SEARCH_ACTION,
        label="Web search",
        description=(
            "Provider used for general web searches. Browser automation and page "
            "interaction tools are routed separately."
        ),
    ),
)
TOOL_ROUTE_DEFINITION_BY_ACTION = {definition.action: definition for definition in TOOL_ROUTE_DEFINITIONS}


class ToolRoutingService(Protocol):
    async def resolve_routes(
        self,
        *,
        scope: AgentScope,
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ResolvedToolRoute, ...]:
        """Resolve one authorized implementation for each configured action."""


class ToolRoutePreferenceValidationError(ValueError):
    """Raised when a requested route cannot be resolved within current grants."""


@dataclass(frozen=True, slots=True)
class SqlModelToolRoutingService:
    session_factory: AsyncSessionFactory

    async def list_preferences(
        self,
        *,
        user_id: str,
        agent_id: str,
    ) -> tuple[ToolRoutePreference, ...]:
        statement = (
            select(AgentToolRoutePreferenceRecord)
            .where(
                col(AgentToolRoutePreferenceRecord.user_id) == user_id,
                col(AgentToolRoutePreferenceRecord.agent_id) == agent_id,
            )
            .order_by(col(AgentToolRoutePreferenceRecord.action).asc())
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            persisted = {record.action: tool_route_preference_from_record(record) for record in result.all()}
        return tuple(
            persisted.get(
                definition.action,
                ToolRoutePreference(
                    user_id=user_id,
                    agent_id=agent_id,
                    action=definition.action,
                ),
            )
            for definition in TOOL_ROUTE_DEFINITIONS
        )

    async def list_candidates(
        self,
        *,
        user_id: str,
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ToolRouteCandidate, ...]:
        return await self._candidates(user_id=user_id, grants=grants)

    async def set_preferences(
        self,
        *,
        user_id: str,
        agent_id: str,
        preferences: tuple[ToolRoutePreferenceUpdate, ...],
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ToolRoutePreference, ...]:
        duplicate_actions = duplicate_values(
            tuple(preference.action for preference in preferences),
        )
        if duplicate_actions:
            message = f"duplicate tool route action: {duplicate_actions[0]}"
            raise ToolRoutePreferenceValidationError(message)
        candidates = await self._candidates(user_id=user_id, grants=grants)
        candidates_by_action = candidates_grouped_by_action(candidates)
        for preference in preferences:
            self._validate_preference(
                preference,
                candidates=candidates_by_action.get(preference.action, ()),
            )

        async with self.session_factory() as session:
            for preference in preferences:
                statement = select(AgentToolRoutePreferenceRecord).where(
                    col(AgentToolRoutePreferenceRecord.user_id) == user_id,
                    col(AgentToolRoutePreferenceRecord.agent_id) == agent_id,
                    col(AgentToolRoutePreferenceRecord.action) == preference.action,
                )
                result = await session.exec(statement)
                record = result.one_or_none()
                if record is None:
                    record = AgentToolRoutePreferenceRecord(
                        user_id=user_id,
                        agent_id=agent_id,
                        action=preference.action,
                        mode=preference.mode.value,
                    )
                record.mode = preference.mode.value
                record.updated_at = utc_now()
                set_pinned_tool(record, preference.pinned_tool)
                session.add(record)
            await session.commit()
        return await self.list_preferences(user_id=user_id, agent_id=agent_id)

    async def resolve_routes(
        self,
        *,
        scope: AgentScope,
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ResolvedToolRoute, ...]:
        preferences = await self.list_preferences(
            user_id=scope.owner_user_id,
            agent_id=scope.agent_id,
        )
        candidates = await self._candidates(
            user_id=scope.owner_user_id,
            grants=grants,
        )
        candidates_by_action = candidates_grouped_by_action(candidates)
        return tuple(
            resolve_tool_route(
                preference,
                candidates_by_action.get(preference.action, ()),
            )
            for preference in preferences
        )

    async def _candidates(
        self,
        *,
        user_id: str,
        grants: tuple[ToolGrant, ...],
    ) -> tuple[ToolRouteCandidate, ...]:
        allowed_mcp_grants = tuple(
            grant
            for grant in grants
            if grant.status == ToolGrantStatus.ALLOWED
            and grant.tool_source == ToolGrantScope.MCP_SERVER
            and grant.mcp_server_id is not None
        )
        server_ids = tuple(sorted({grant.mcp_server_id for grant in allowed_mcp_grants if grant.mcp_server_id}))
        if not server_ids:
            return ()
        statement = select(UserMcpServerRecord).where(
            col(UserMcpServerRecord.user_id) == user_id,
            col(UserMcpServerRecord.id).in_(server_ids),
            col(UserMcpServerRecord.enabled).is_(True),
            col(UserMcpServerRecord.trust_state) == McpTrustState.TRUSTED.value,
            col(UserMcpServerRecord.status) == McpServerStatus.CONNECTED.value,
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            server_by_id = {record.id: record for record in result.all()}
        candidates: list[ToolRouteCandidate] = []
        for grant in allowed_mcp_grants:
            server_id = grant.mcp_server_id
            if server_id is None:
                continue
            server = server_by_id.get(server_id)
            if server is None:
                continue
            if grant.tool_name not in server.allowed_tools:
                continue
            preset_id = runtime_preset_id_for_record(
                server,
                safe_metadata_from_record(server.safe_metadata),
            )
            route = mcp_preset_tool_route(preset_id, grant.tool_name)
            if route is None:
                continue
            action, priority = route
            candidates.append(
                ToolRouteCandidate(
                    action=action,
                    provider_label=server.label,
                    automatic_priority=priority,
                    reference=ToolReference(
                        tool_name=grant.tool_name,
                        tool_source=grant.tool_source,
                        mcp_server_id=server_id,
                        integration_id=grant.integration_id,
                        risk=grant.risk,
                    ),
                ),
            )
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    candidate.action,
                    candidate.automatic_priority,
                    candidate.provider_label,
                    candidate.reference.tool_name,
                ),
            ),
        )

    def _validate_preference(
        self,
        preference: ToolRoutePreferenceUpdate,
        *,
        candidates: tuple[ToolRouteCandidate, ...],
    ) -> None:
        if preference.action not in TOOL_ROUTE_DEFINITION_BY_ACTION:
            message = f"unknown tool route action: {preference.action}"
            raise ToolRoutePreferenceValidationError(message)
        if preference.mode != ToolRouteMode.PINNED:
            return
        pinned_tool = preference.pinned_tool
        if pinned_tool is None or not any(
            selection_matches_reference(pinned_tool, candidate.reference) for candidate in candidates
        ):
            raise ToolRoutePreferenceValidationError(INVALID_PINNED_TOOL_ERROR)


def resolve_tool_route(
    preference: ToolRoutePreference,
    candidates: tuple[ToolRouteCandidate, ...],
) -> ResolvedToolRoute:
    selected: ToolRouteCandidate | None = None
    if preference.mode == ToolRouteMode.PINNED and preference.pinned_tool is not None:
        selected = next(
            (
                candidate
                for candidate in candidates
                if selection_matches_reference(
                    preference.pinned_tool,
                    candidate.reference,
                )
            ),
            None,
        )
    elif candidates:
        selected = min(candidates, key=tool_route_candidate_sort_key)
    return ResolvedToolRoute(
        action=preference.action,
        mode=preference.mode,
        selected_tool=None if selected is None else selected.reference,
        automatic_priority=(None if selected is None else selected.automatic_priority),
    )


def tool_route_candidate_sort_key(
    candidate: ToolRouteCandidate,
) -> tuple[int, str, str, str, str]:
    reference = candidate.reference
    return (
        candidate.automatic_priority,
        reference.tool_source.value,
        reference.mcp_server_id or "",
        reference.integration_id or "",
        reference.tool_name,
    )


def candidates_grouped_by_action(
    candidates: tuple[ToolRouteCandidate, ...],
) -> dict[str, tuple[ToolRouteCandidate, ...]]:
    grouped: dict[str, list[ToolRouteCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(candidate.action, []).append(candidate)
    return {action: tuple(values) for action, values in grouped.items()}


def selection_matches_reference(
    selection: ToolRouteSelection,
    reference: ToolReference,
) -> bool:
    return (
        selection.tool_name == reference.tool_name
        and selection.tool_source == reference.tool_source
        and selection.mcp_server_id == reference.mcp_server_id
        and selection.integration_id == reference.integration_id
    )


def tool_route_preference_from_record(
    record: AgentToolRoutePreferenceRecord,
) -> ToolRoutePreference:
    mode = ToolRouteMode(record.mode)
    pinned_tool = None
    if mode == ToolRouteMode.PINNED:
        if record.pinned_tool_name is None or record.pinned_tool_source is None:
            raise ValueError(INCOMPLETE_PINNED_TOOL_ERROR)
        pinned_tool = ToolRouteSelection(
            tool_name=record.pinned_tool_name,
            tool_source=ToolGrantScope(record.pinned_tool_source),
            mcp_server_id=record.pinned_mcp_server_id,
            integration_id=record.pinned_integration_id,
        )
    return ToolRoutePreference(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        action=record.action,
        mode=mode,
        pinned_tool=pinned_tool,
    )


def set_pinned_tool(
    record: AgentToolRoutePreferenceRecord,
    selection: ToolRouteSelection | None,
) -> None:
    record.pinned_tool_name = None if selection is None else selection.tool_name
    record.pinned_tool_source = None if selection is None else selection.tool_source.value
    record.pinned_mcp_server_id = None if selection is None else selection.mcp_server_id
    record.pinned_integration_id = None if selection is None else selection.integration_id


def duplicate_values(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return tuple(duplicates)

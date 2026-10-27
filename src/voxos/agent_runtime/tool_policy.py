from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_identity.models import AgentScope


class ToolCapability(StrEnum):
    READ = "read"
    WRITE = "write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    SENSITIVE_DATA = "sensitive_data"


class ToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolGrantStatus(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"


class ToolGrantScope(StrEnum):
    INTERNAL = "internal"
    MCP_SERVER = "mcp_server"
    INTEGRATION = "integration"


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    capabilities: tuple[ToolCapability, ...] = Field(default_factory=tuple)
    risk: ToolRisk = ToolRisk.LOW
    confirmation_required: bool = False


class ToolExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)
    policy: ToolPolicy


class ToolReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    tool_source: ToolGrantScope
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    risk: ToolRisk = ToolRisk.MEDIUM


class ToolGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    tool_name: str = Field(min_length=1, max_length=256)
    tool_source: ToolGrantScope
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    status: ToolGrantStatus
    risk: ToolRisk


class ToolGrantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_name: str = Field(min_length=1, max_length=256)
    tool_source: ToolGrantScope
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    status: ToolGrantStatus
    risk: ToolRisk = ToolRisk.MEDIUM


class ToolAuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reason: str = Field(min_length=1, max_length=512)
    reference: ToolReference


class ToolAuthorizationService(Protocol):
    async def get_explicit_grant(
        self,
        *,
        user_id: str,
        agent_id: str,
        reference: ToolReference,
    ) -> ToolGrant | None:
        """Return the persisted decision for one tool, excluding defaults."""

    async def list_effective_grants(self, scope: AgentScope) -> tuple[ToolGrant, ...]:
        """Return persisted and platform-default grants for the agent scope."""

    async def set_grants(
        self,
        *,
        user_id: str,
        agent_id: str,
        grants: tuple[ToolGrantUpdate, ...],
        audit_reason: str = "grant_updated",
    ) -> tuple[ToolGrant, ...]:
        """Persist grant decisions for a user-owned agent."""

    async def authorize_tool(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
    ) -> ToolAuthorizationDecision:
        """Return whether the scoped agent may call a tool."""

    async def record_tool_audit_event(
        self,
        *,
        scope: AgentScope,
        reference: ToolReference,
        allowed: bool,
        reason: str,
        metadata_json: dict[str, object] | None = None,
    ) -> None:
        """Persist a safe audit event for a tool decision."""

    async def allowed_tool_names(
        self,
        *,
        scope: AgentScope,
        tool_source: ToolGrantScope,
        tool_names: tuple[str, ...],
        mcp_server_id: str | None = None,
        integration_id: str | None = None,
    ) -> tuple[str, ...]:
        """Return the subset of tool names allowed for the scope."""

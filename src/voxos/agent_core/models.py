from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicySnapshot
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolRisk
from voxos.location.models import ClientLocation
from voxos.logos.models import ActivityIcon, BrandLogo
from voxos.place_cards.models import PlaceCardArtifact


class AgentEventKind(StrEnum):
    RUN_STARTED = "run_started"
    MESSAGE_DELTA = "message_delta"
    MESSAGE_COMPLETED = "message_completed"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    USAGE = "usage"
    RUN_COMPLETED = "run_completed"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"
    CHAT_DELETED = "chat_deleted"


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64)
    kind: AgentEventKind
    session_id: str = Field(min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    payload: JsonObject = Field(default_factory=dict)
    created_at: datetime


class AgentRunContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_kind: AgentRunKind | None = None
    root_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    instructions: str = Field(min_length=1)
    communication_medium: CommunicationMedium = CommunicationMedium.WEB_CHAT
    effective_tool_policy_id: str | None = Field(default=None, min_length=1, max_length=36)
    effective_tool_policy: EffectiveToolPolicySnapshot | None = None
    session_started_at: datetime | None = None
    current_location: ClientLocation | None = None


class AgentToolActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=128)
    detail: str | None = Field(default=None, min_length=1, max_length=256)
    icon: ActivityIcon | None = None
    logo: BrandLogo | None = None


class AgentTool(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=1024)
    parameters_schema: JsonObject = Field(default_factory=dict)
    source: ToolGrantScope
    risk: ToolRisk = ToolRisk.MEDIUM
    required: bool = False
    parallel_safe: bool = False
    always_visible: bool = False
    mcp_server_id: str | None = Field(default=None, min_length=1, max_length=36)
    integration_id: str | None = Field(default=None, min_length=1, max_length=36)
    source_tool_name: str | None = Field(default=None, min_length=1, max_length=256)
    catalog_label: str | None = Field(default=None, min_length=1, max_length=256)
    capability_key: str | None = Field(default=None, min_length=1, max_length=128)
    route_action: str | None = Field(default=None, min_length=1, max_length=128)
    automatic_route_priority: int | None = Field(default=None, ge=0, le=10_000)
    activity: AgentToolActivity | None = None
    authority_argument_allowed_paths: frozenset[tuple[str, ...]] = Field(
        default_factory=frozenset,
    )


class AgentToolAttachmentArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    mime_type: Literal["image/jpeg", "image/png"]
    byte_size: int = Field(ge=1)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    position: int | None = Field(default=None, ge=0)


class AgentToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    output_text: str = Field(default="")
    persisted_output_text: str | None = None
    is_error: bool = False
    raw_output: JsonObject = Field(default_factory=dict)
    executed_arguments_json: JsonObject | None = None
    attachment_artifacts: tuple[AgentToolAttachmentArtifact, ...] = Field(
        default_factory=tuple,
    )
    place_card_artifacts: tuple[PlaceCardArtifact, ...] = Field(
        default_factory=tuple,
    )


class AgentToolProvider(Protocol):
    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        """Return tools available to the run context."""

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        """Execute a scoped tool call."""


class AgentRunService(Protocol):
    async def stream(self, context: AgentRunContext) -> AsyncIterator[AgentEvent]:
        """Stream normalized agent events."""

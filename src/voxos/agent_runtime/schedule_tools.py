from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voxos.agent_core.models import AgentRunContext, AgentTool, AgentToolResult
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.schedule_models import (
    AgentSchedule,
    AgentScheduleCreateRequest,
    ScheduleCatchUpPolicy,
    ScheduleKind,
)
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.tool_execution import (
    ToolExecutionContext,
    ToolExecutionSecurityError,
    inline_local_schema_references,
    validate_llm_tool_arguments,
    validate_tool_arguments_schema,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)

CREATE_AGENT_SCHEDULE_TOOL_NAME = "create_agent_schedule"
CREATE_AGENT_SCHEDULE_TOOL_DESCRIPTION = (
    "Schedule a one-shot or recurring future agent turn and post its response in the "
    "current chat. Use this for reminders, future checks, monitoring, and requested "
    "follow-ups only when the current user explicitly asks for or confirms the "
    "schedule. The instruction must say exactly what the future agent should do."
)
INTERACTIVE_CHAT_REQUIRED_ERROR = "Schedule creation is available only during an interactive text chat turn"


class AgentScheduleToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        min_length=1,
        max_length=256,
        description="Short user-facing name for the scheduled task.",
    )
    instruction: str = Field(
        min_length=1,
        max_length=24000,
        description="Self-contained instruction for the future agent turn.",
    )
    kind: Literal[ScheduleKind.ONE_SHOT, ScheduleKind.INTERVAL] = Field(
        default=ScheduleKind.ONE_SHOT,
        description="Run once or repeat at a fixed interval.",
    )
    run_at: datetime | None = Field(
        default=None,
        description=(
            "ISO 8601 date and time with a UTC offset. Required for one-shot tasks and "
            "optional as the first run time for recurring tasks."
        ),
    )
    interval_seconds: int | None = Field(
        default=None,
        ge=60,
        description="Seconds between recurring runs. Required for interval tasks.",
    )
    timezone: str = Field(
        default="UTC",
        min_length=1,
        max_length=128,
        description="IANA timezone name used to describe the schedule.",
    )

    @field_validator("name", "instruction", "timezone", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_schedule_shape(self) -> "AgentScheduleToolRequest":
        if self.run_at is not None and self.run_at.utcoffset() is None:
            message = "run_at must include a UTC offset"
            raise ValueError(message)
        if self.kind == ScheduleKind.ONE_SHOT:
            if self.run_at is None:
                message = "run_at is required for a one-shot scheduled task"
                raise ValueError(message)
            if self.interval_seconds is not None:
                message = "interval_seconds is only valid for an interval scheduled task"
                raise ValueError(message)
        elif self.interval_seconds is None:
            message = "interval_seconds is required for an interval scheduled task"
            raise ValueError(message)
        return self


class AgentScheduleToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedule: AgentSchedule


AGENT_SCHEDULE_TOOL_SCHEMA = inline_local_schema_references(
    cast(JsonObject, AgentScheduleToolRequest.model_json_schema()),
)


@dataclass(frozen=True, slots=True)
class AgentScheduleToolProvider:
    schedule_store: AgentScheduleStore
    authorization_service: ToolAuthorizationService | None = None

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        if context.run_kind != AgentRunKind.TEXT_CHAT_TURN:
            return ()
        reference = ToolReference(
            tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        )
        if context.effective_tool_policy is not None:
            if not context.effective_tool_policy.allows(reference):
                return ()
        elif self.authorization_service is not None:
            allowed = await self.authorization_service.allowed_tool_names(
                scope=self._execution_context(
                    context=context,
                    source="agent_runtime.schedule_tools.list",
                    require_run=False,
                ).to_agent_scope(),
                tool_source=ToolGrantScope.INTERNAL,
                tool_names=(CREATE_AGENT_SCHEDULE_TOOL_NAME,),
            )
            if CREATE_AGENT_SCHEDULE_TOOL_NAME not in allowed:
                return ()
        return (agent_schedule_tool(),)

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        if tool_name != CREATE_AGENT_SCHEDULE_TOOL_NAME:
            message = f"Unknown schedule tool: {tool_name}"
            raise ValueError(message)
        execution_context = self._execution_context(
            context=context,
            source="agent_runtime.schedule_tools.execute",
            require_run=True,
        )
        try:
            await self._require_interactive_chat_run(execution_context)
        except ToolExecutionSecurityError as exc:
            await self._audit_scope_denial(
                execution_context=execution_context,
                reason=str(exc),
            )
            raise
        try:
            validate_llm_tool_arguments(arguments_json)
        except ToolExecutionSecurityError as exc:
            await self._audit_argument_denial(
                execution_context=execution_context,
                reason=str(exc),
            )
            raise
        validate_tool_arguments_schema(arguments_json, AGENT_SCHEDULE_TOOL_SCHEMA)
        request = AgentScheduleToolRequest.model_validate(arguments_json)
        await self._authorize(execution_context)
        if execution_context.session_id is None:
            message = "Schedule creation requires a current chat"
            raise ToolExecutionSecurityError(message)
        schedule = await self.schedule_store.create_schedule(
            user_id=execution_context.user_id,
            request=AgentScheduleCreateRequest(
                agent_id=execution_context.agent_id,
                chat_id=execution_context.session_id,
                name=request.name,
                kind=ScheduleKind(request.kind),
                timezone=request.timezone,
                payload_json={"text": request.instruction},
                run_at=request.run_at,
                interval_seconds=request.interval_seconds,
                catch_up_policy=ScheduleCatchUpPolicy.SKIP_MISSED,
            ),
        )
        result = AgentScheduleToolResult(schedule=schedule)
        output = cast(JsonObject, result.model_dump(mode="json"))
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=orjson.dumps(output).decode(),
            raw_output=output,
        )

    async def _require_interactive_chat_run(
        self,
        execution_context: ToolExecutionContext,
    ) -> None:
        run_id = execution_context.run_id
        if run_id is None:
            raise ToolExecutionSecurityError(INTERACTIVE_CHAT_REQUIRED_ERROR)
        run = await self.schedule_store.run_store.get_run(
            user_id=execution_context.user_id,
            run_id=run_id,
        )
        if (
            run is None
            or run.agent_id != execution_context.agent_id
            or run.kind != AgentRunKind.TEXT_CHAT_TURN.value
            or run.session_id != execution_context.session_id
            or run.chat_id != execution_context.session_id
        ):
            raise ToolExecutionSecurityError(INTERACTIVE_CHAT_REQUIRED_ERROR)

    async def _authorize(self, execution_context: ToolExecutionContext) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    async def _audit_argument_denial(
        self,
        *,
        execution_context: ToolExecutionContext,
        reason: str,
    ) -> None:
        if self.authorization_service is None:
            return
        await self.authorization_service.record_tool_audit_event(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_arguments"},
        )

    async def _audit_scope_denial(
        self,
        *,
        execution_context: ToolExecutionContext,
        reason: str,
    ) -> None:
        if self.authorization_service is None:
            return
        await self.authorization_service.record_tool_audit_event(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_execution_scope"},
        )

    @staticmethod
    def _execution_context(
        *,
        context: AgentRunContext,
        source: str,
        require_run: bool,
    ) -> ToolExecutionContext:
        return ToolExecutionContext.from_run_context(
            context,
            source=source,
            require_run=require_run,
        )


def agent_schedule_tool() -> AgentTool:
    return AgentTool(
        name=CREATE_AGENT_SCHEDULE_TOOL_NAME,
        description=CREATE_AGENT_SCHEDULE_TOOL_DESCRIPTION,
        parameters_schema=AGENT_SCHEDULE_TOOL_SCHEMA,
        source=ToolGrantScope.INTERNAL,
        risk=ToolRisk.MEDIUM,
        always_visible=True,
    )

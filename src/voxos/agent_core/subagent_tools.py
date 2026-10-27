from dataclasses import dataclass
from typing import cast

import orjson
from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_core.models import AgentRunContext, AgentTool, AgentToolResult
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.subagents import (
    SubAgentContextMode,
    SubAgentService,
    SubAgentSpawnRequest,
)
from voxos.agent_runtime.tool_execution import (
    ToolExecutionContext,
    ToolExecutionSecurityError,
    validate_llm_tool_arguments,
)
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolReference,
    ToolRisk,
)
from voxos.text.model_policy import TextSubAgentModelPolicy
from voxos.text.models import TextProvider

DELEGATE_AGENT_TASK_TOOL_NAME = "delegate_agent_task"


class DelegateAgentTaskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_prompt: str = Field(min_length=1, max_length=24000)
    parent_summary: str | None = Field(default=None, min_length=1, max_length=12000)
    model: str | None = Field(default=None, min_length=1, max_length=256)


class DelegateAgentTaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    child_run_id: str = Field(min_length=1, max_length=36)
    status: str = Field(min_length=1, max_length=32)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True, slots=True)
class SubAgentToolProvider:
    subagent_service: SubAgentService
    model_policy: TextSubAgentModelPolicy
    authorization_service: ToolAuthorizationService | None = None

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        if context.run_id is None:
            return ()
        execution_context = self._execution_context(
            context=context,
            source="agent_core.subagent_tools.list",
        )
        if self.authorization_service is not None:
            allowed_names = await self.authorization_service.allowed_tool_names(
                scope=execution_context.to_agent_scope(),
                tool_source=ToolGrantScope.INTERNAL,
                tool_names=(DELEGATE_AGENT_TASK_TOOL_NAME,),
            )
            if DELEGATE_AGENT_TASK_TOOL_NAME not in allowed_names:
                return ()
        settings = await self.model_policy.settings(
            user_id=context.user_id,
            provider=TextProvider(context.provider),
            parent_model=context.model,
        )
        return (
            AgentTool(
                name=DELEGATE_AGENT_TASK_TOOL_NAME,
                description=(
                    "Queue a durable subagent only for genuinely independent, multi-step "
                    "work. Do not delegate solely to use a tool: use direct tools or "
                    "tool_search in this run. This turn does not wait for the child; its "
                    "status, tools, and result are surfaced separately. Omit parent_summary "
                    "for isolated context; supplying one uses summary context. Choose a model "
                    "only when the work warrants a different cost/quality tier; otherwise "
                    f"the configured default {settings.default_model} is used."
                ),
                parameters_schema=delegate_agent_task_schema(
                    allowed_models=settings.allowed_models,
                ),
                source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
        )

    async def execute_tool(
        self,
        *,
        context: AgentRunContext,
        tool_name: str,
        call_id: str,
        arguments_json: JsonObject,
    ) -> AgentToolResult:
        if tool_name != DELEGATE_AGENT_TASK_TOOL_NAME:
            message = f"Unknown subagent tool: {tool_name}"
            raise ValueError(message)
        execution_context = self._execution_context(
            context=context,
            source="agent_core.subagent_tools.execute",
        )
        try:
            validate_llm_tool_arguments(arguments_json)
        except ToolExecutionSecurityError as exc:
            await self._audit_denial(
                execution_context=execution_context,
                reason=str(exc),
            )
            raise
        await self._authorize(execution_context)
        arguments = DelegateAgentTaskArguments.model_validate(arguments_json)
        if execution_context.run_id is None:
            message = "Subagent delegation requires a persisted parent run"
            raise ToolExecutionSecurityError(message)
        child = await self.subagent_service.spawn_subagent(
            user_id=execution_context.user_id,
            request=SubAgentSpawnRequest(
                parent_run_id=execution_context.run_id,
                target_agent_id=execution_context.agent_id,
                task_prompt=arguments.task_prompt,
                context_mode=(
                    SubAgentContextMode.SUMMARY
                    if arguments.parent_summary is not None
                    else SubAgentContextMode.ISOLATED
                ),
                parent_summary=arguments.parent_summary,
                model=arguments.model,
                current_location=context.current_location,
            ),
            parent_provider=context.provider,
            parent_model=context.model,
        )
        if child is None:
            message = "Parent run was not found"
            raise LookupError(message)
        provider = child.payload_json.get("provider")
        model = child.payload_json.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            message = "Subagent model policy did not produce a provider and model"
            raise RuntimeError(message)
        result = DelegateAgentTaskResult(
            child_run_id=child.id,
            status=child.status,
            provider=provider,
            model=model,
        )
        raw_output = cast(JsonObject, result.model_dump(mode="json"))
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=orjson.dumps(raw_output).decode(),
            raw_output=raw_output,
        )

    async def _authorize(self, execution_context: ToolExecutionContext) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=ToolReference(
                tool_name=DELEGATE_AGENT_TASK_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
        )
        if not decision.allowed:
            raise PermissionError(decision.reason)

    async def _audit_denial(
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
                tool_name=DELEGATE_AGENT_TASK_TOOL_NAME,
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
            ),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_arguments"},
        )

    @staticmethod
    def _execution_context(
        *,
        context: AgentRunContext,
        source: str,
    ) -> ToolExecutionContext:
        return ToolExecutionContext.from_run_context(
            context,
            source=source,
            require_run=True,
        )


def delegate_agent_task_schema(*, allowed_models: tuple[str, ...]) -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "task_prompt": {
                "type": "string",
                "minLength": 1,
                "description": "A complete, well-scoped task for the delegated run.",
            },
            "parent_summary": {
                "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}],
                "description": (
                    "Optional relevant parent context. Supplying it uses summary mode; "
                    "omitting it keeps the child isolated."
                ),
            },
            "model": {
                "anyOf": [
                    {"type": "string", "enum": list(allowed_models)},
                    {"type": "null"},
                ],
                "description": (
                    "Optional user-allowed model override. Omit it to use the configured subagent default."
                ),
            },
        },
        "required": ["task_prompt"],
        "additionalProperties": False,
    }

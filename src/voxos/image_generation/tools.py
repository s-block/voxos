from dataclasses import dataclass

import orjson
from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_core.models import (
    AgentRunContext,
    AgentTool,
    AgentToolAttachmentArtifact,
    AgentToolResult,
)
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.models import JsonObject
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
from voxos.attachments.service import AttachmentError, attachment_summary
from voxos.image_generation.providers import ImageGenerationProviderError
from voxos.image_generation.service import (
    ImageGenerationAction,
    ImageGenerationService,
    ImageGenerationUnavailableError,
)
from voxos.text.errors import MissingTextCredentialError

GENERATE_IMAGE_TOOL_NAME = "generate_image"


class ImageGenerationToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=10000)
    action: ImageGenerationAction


@dataclass(frozen=True, slots=True)
class ImageGenerationAgentToolProvider:
    service: ImageGenerationService
    authorization_service: ToolAuthorizationService | None = None

    async def list_tools(self, context: AgentRunContext) -> tuple[AgentTool, ...]:
        if not self._supports_context(context):
            return ()
        execution_context = self._execution_context(
            context=context,
            source="image_generation.tools.list",
        )
        if self.authorization_service is not None:
            allowed_names = await self.authorization_service.allowed_tool_names(
                scope=execution_context.to_agent_scope(),
                tool_source=ToolGrantScope.INTERNAL,
                tool_names=(GENERATE_IMAGE_TOOL_NAME,),
            )
            if GENERATE_IMAGE_TOOL_NAME not in allowed_names:
                return ()
        capabilities = await self.service.capabilities(user_id=context.user_id)
        if not capabilities.generation_available:
            return ()
        return (
            AgentTool(
                name=GENERATE_IMAGE_TOOL_NAME,
                description=(
                    "Generate or edit an image only when the user explicitly asks to create "
                    "or change one. Use the full conversation to write a self-contained visual "
                    "prompt. Choose edit for a current uploaded image or the most recent image "
                    "generated in this chat; choose generate for a new independent image. Do not "
                    "call this tool merely to discuss, identify, or analyze an image. After a "
                    "failed call, follow its recovery guidance in the current response. Never "
                    "claim that a retry is happening unless you call this tool again."
                ),
                parameters_schema=image_generation_tool_schema(),
                source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.MEDIUM,
                parallel_safe=False,
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
        if tool_name != GENERATE_IMAGE_TOOL_NAME:
            message = f"Unknown image-generation tool: {tool_name}"
            raise ValueError(message)
        if not self._supports_context(context):
            message = "Image generation is available only in a root web chat run"
            raise ToolExecutionSecurityError(message)
        execution_context = self._execution_context(
            context=context,
            source="image_generation.tools.execute",
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
        arguments = ImageGenerationToolArguments.model_validate(arguments_json)
        if execution_context.run_id is None:
            message = "Image generation requires a persisted run"
            raise ToolExecutionSecurityError(message)
        try:
            generated = await self.service.generate_for_tool(
                user_id=execution_context.user_id,
                chat_id=context.session_id,
                agent_id=execution_context.agent_id,
                run_id=execution_context.run_id,
                call_id=call_id,
                prompt=arguments.prompt,
                action=arguments.action,
            )
        except (
            AttachmentError,
            ImageGenerationProviderError,
            ImageGenerationUnavailableError,
            MissingTextCredentialError,
        ) as exc:
            return AgentToolResult(
                call_id=call_id,
                name=tool_name,
                output_text=str(exc),
                is_error=True,
                executed_arguments_json=arguments.model_dump(mode="json"),
            )

        summary = attachment_summary(generated.attachment)
        artifact = AgentToolAttachmentArtifact.model_validate(
            summary.model_dump(mode="json"),
        )
        safe_output: JsonObject = {
            "action": arguments.action.value,
            "provider": generated.provider.value,
            "model": generated.model,
            "status": "The generated image will be attached to the final assistant response.",
        }
        return AgentToolResult(
            call_id=call_id,
            name=tool_name,
            output_text=orjson.dumps(safe_output).decode(),
            raw_output=safe_output,
            executed_arguments_json=arguments.model_dump(mode="json"),
            attachment_artifacts=(artifact,),
        )

    async def _authorize(self, execution_context: ToolExecutionContext) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=execution_context.to_agent_scope(),
            reference=self._tool_reference(),
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
            reference=self._tool_reference(),
            allowed=False,
            reason=reason,
            metadata_json={"category": "invalid_arguments"},
        )

    @staticmethod
    def _supports_context(context: AgentRunContext) -> bool:
        return (
            context.run_id is not None
            and context.parent_run_id is None
            and context.communication_medium == CommunicationMedium.WEB_CHAT
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

    @staticmethod
    def _tool_reference() -> ToolReference:
        return ToolReference(
            tool_name=GENERATE_IMAGE_TOOL_NAME,
            tool_source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        )


def image_generation_tool_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "maxLength": 10000,
                "description": (
                    "A self-contained visual prompt incorporating every relevant detail from the conversation."
                ),
            },
            "action": {
                "type": "string",
                "enum": [action.value for action in ImageGenerationAction],
                "description": "Generate a new image or edit the trusted chat image reference.",
            },
        },
        "required": ["prompt", "action"],
        "additionalProperties": False,
    }

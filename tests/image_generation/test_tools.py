from dataclasses import dataclass
from typing import cast

import pytest
from pydantic import ValidationError

from voxos.agent_core.models import AgentRunContext
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.tool_execution import ToolAuthorityArgumentError
from voxos.image_generation.providers import ImageGenerationProviderError
from voxos.image_generation.service import (
    ImageGenerationAction,
    ImageGenerationCapabilities,
    ImageGenerationService,
    StagedImageGeneration,
)
from voxos.image_generation.tools import (
    GENERATE_IMAGE_TOOL_NAME,
    ImageGenerationAgentToolProvider,
    image_generation_tool_schema,
)


@dataclass(frozen=True, slots=True)
class CapabilityOnlyImageService:
    available: bool = True

    async def capabilities(self, *, user_id: str) -> ImageGenerationCapabilities:
        del user_id
        return ImageGenerationCapabilities(
            generation_available=self.available,
            editing_available=self.available,
        )


@dataclass(frozen=True, slots=True)
class FailingImageService(CapabilityOnlyImageService):
    error_message: str = "Image generation failed"

    async def generate_for_tool(
        self,
        *,
        user_id: str,
        chat_id: str,
        agent_id: str,
        run_id: str,
        call_id: str,
        prompt: str,
        action: ImageGenerationAction,
    ) -> StagedImageGeneration:
        del user_id, chat_id, agent_id, run_id, call_id, prompt, action
        raise ImageGenerationProviderError(self.error_message)


@pytest.mark.asyncio
async def test_image_tool_is_exposed_only_for_configured_root_web_chat() -> None:
    provider = ImageGenerationAgentToolProvider(
        service=cast(ImageGenerationService, CapabilityOnlyImageService()),
    )

    root_tools = await provider.list_tools(run_context())
    child_tools = await provider.list_tools(
        run_context(parent_run_id="parent-run"),
    )
    voice_tools = await provider.list_tools(
        run_context(communication_medium=CommunicationMedium.VOICE),
    )

    assert [tool.name for tool in root_tools] == [GENERATE_IMAGE_TOOL_NAME]
    assert root_tools[0].parallel_safe is False
    assert "Never claim that a retry is happening" in root_tools[0].description
    assert child_tools == ()
    assert voice_tools == ()


@pytest.mark.asyncio
async def test_image_tool_is_hidden_when_generation_is_unavailable() -> None:
    provider = ImageGenerationAgentToolProvider(
        service=cast(
            ImageGenerationService,
            CapabilityOnlyImageService(available=False),
        ),
    )

    assert await provider.list_tools(run_context()) == ()


@pytest.mark.asyncio
async def test_image_tool_rejects_authority_bearing_arguments() -> None:
    provider = ImageGenerationAgentToolProvider(
        service=cast(ImageGenerationService, CapabilityOnlyImageService()),
    )

    with pytest.raises(ToolAuthorityArgumentError):
        await provider.execute_tool(
            context=run_context(),
            tool_name=GENERATE_IMAGE_TOOL_NAME,
            call_id="call-1",
            arguments_json={
                "action": "edit",
                "prompt": "Make it brighter",
                "user_id": "untrusted-user",
            },
        )


@pytest.mark.asyncio
async def test_image_tool_rejects_model_selected_attachment_ids() -> None:
    provider = ImageGenerationAgentToolProvider(
        service=cast(ImageGenerationService, CapabilityOnlyImageService()),
    )

    with pytest.raises(ValidationError):
        await provider.execute_tool(
            context=run_context(),
            tool_name=GENERATE_IMAGE_TOOL_NAME,
            call_id="call-1",
            arguments_json={
                "action": "edit",
                "prompt": "Make it brighter",
                "attachment_id": "untrusted-attachment",
            },
        )


@pytest.mark.asyncio
async def test_image_tool_returns_provider_failure_to_the_chat_model() -> None:
    provider = ImageGenerationAgentToolProvider(
        service=cast(
            ImageGenerationService,
            FailingImageService(
                error_message=("NVIDIA's content filter rejected the image prompt. Do not retry it unchanged."),
            ),
        ),
    )

    result = await provider.execute_tool(
        context=run_context(),
        tool_name=GENERATE_IMAGE_TOOL_NAME,
        call_id="call-1",
        arguments_json={
            "action": "generate",
            "prompt": "A fictional beach wedding",
        },
    )

    assert result.is_error is True
    assert result.output_text == ("NVIDIA's content filter rejected the image prompt. Do not retry it unchanged.")
    assert result.attachment_artifacts == ()


def test_image_tool_schema_has_no_authority_inputs() -> None:
    schema = image_generation_tool_schema()

    assert schema["required"] == ["prompt", "action"]
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert set(properties) == {"prompt", "action"}


def run_context(
    *,
    parent_run_id: str | None = None,
    communication_medium: CommunicationMedium = CommunicationMedium.WEB_CHAT,
) -> AgentRunContext:
    return AgentRunContext(
        session_id="chat-1",
        run_id="run-1",
        parent_run_id=parent_run_id,
        user_id="user-1",
        agent_id="agent-1",
        provider="openai",
        model="gpt-5.6-sol",
        instructions="Be helpful.",
        communication_medium=communication_medium,
    )

from datetime import UTC, datetime

import pytest

from voxos.agent_identity.models import AgentScope, AgentUpdateRequest
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.models import MemoryCreateRequest, MemoryKind
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_planning.models import TaskPlanCreateRequest
from voxos.agent_planning.services import AgentPlanningService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicyEntry,
    EffectiveToolPolicySnapshot,
)
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolRisk
from voxos.agent_skills.models import SkillCreateRequest
from voxos.agent_skills.services import AgentSkillService
from voxos.db.session import AsyncSessionFactory
from voxos.persona.models import AgentPersona
from voxos.persona.rendering import ToolPolicyContext
from voxos.persona.services import AgentPersonaService
from voxos.providers.models import OpenAIRealtimeConfig
from voxos.voice.caller_access import VoiceCallerAccess, VoiceCallerAccessLevel
from voxos.voice.instructions import PersonaVoiceInstructionProvider
from voxos.voice.models import UserVoicePreferences


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC)


@pytest.mark.asyncio
async def test_persona_voice_instruction_provider_renders_voice_context() -> None:
    instruction_provider = PersonaVoiceInstructionProvider(
        persona_service=AgentPersonaService(
            default_persona=AgentPersona(name="Ada", role="technical voice assistant"),
        ),
        tool_policy_context=ToolPolicyContext(
            available_tools=("email",),
            confirmation_required_actions=("send_email",),
        ),
        clock=FakeClock(),
    )
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="Base voice instructions.",
            preferred_language="English",
            modalities=("audio",),
            speed=1.25,
        ),
    )

    instructions = await instruction_provider.render_instructions(
        user_id="user-123",
        preferences=preferences,
    )

    assert "# Base Agent Instructions" in instructions
    assert "Base voice instructions." in instructions
    assert "- Name: Ada" in instructions
    assert "- Model: gpt-realtime" in instructions
    assert "- Voice: marin" in instructions
    assert "- Preferred language: English" in instructions
    assert "- Modalities: audio" in instructions
    assert "- Speed: 1.25" in instructions
    assert "- Confirmation required actions: send_email" in instructions
    assert "- Current datetime (UTC): 2026-07-09T12:34:56+00:00" in instructions
    assert "- Voice session started at (UTC): 2026-07-09T12:34:56+00:00" in instructions
    assert "get_current_datetime" in instructions
    assert "# Factual Accuracy And Web Grounding" in instructions
    assert "When in doubt, search" in instructions
    assert "# Communication Medium Contract" in instructions
    assert "30 to 50 spoken words" in instructions
    assert "# Tool Discovery" in instructions
    assert "always call tool_search before answering" in instructions
    assert "Every tool_search result is authorized and callable" in instructions
    assert "generic browser is a fallback" in instructions
    assert "call tool_search before using a generic browser" in instructions


@pytest.mark.asyncio
async def test_persona_voice_instruction_provider_renders_agent_context_sections(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    memory_service = AgentMemoryService(sqlite_session_factory)
    skill_service = AgentSkillService(sqlite_session_factory)
    planning_service = AgentPlanningService(sqlite_session_factory)
    await memory_service.create_memory(
        user_id="user-123",
        request=MemoryCreateRequest(
            kind=MemoryKind.PREFERENCE,
            text="Prefers deployment updates before test summaries.",
        ),
    )
    await skill_service.create_user_skill(
        user_id="user-123",
        request=SkillCreateRequest(
            name="Deployment Notes",
            slug="deployment-notes",
            description="Use deployment review steps.",
            body_markdown="Check migrations before deployment.",
        ),
    )
    await planning_service.create_plan(
        user_id="user-123",
        request=TaskPlanCreateRequest(
            objective="Prepare deployment",
            steps=("Review migration",),
        ),
    )
    instruction_provider = PersonaVoiceInstructionProvider(
        persona_service=AgentPersonaService(),
        memory_service=memory_service,
        skill_service=skill_service,
        planning_service=planning_service,
    )
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="Base deployment instructions.",
        ),
    )

    instructions = await instruction_provider.render_instructions(
        user_id="user-123",
        preferences=preferences,
    )

    assert "# Long-term User Memory" in instructions
    assert "Prefers deployment updates" in instructions
    assert "# Task-Relevant Skills" in instructions
    assert "deployment-notes" in instructions
    assert "# Active Task Plan" in instructions
    assert "Prepare deployment" in instructions


@pytest.mark.asyncio
async def test_limited_caller_instructions_exclude_all_owner_private_context(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    memory_service = AgentMemoryService(sqlite_session_factory)
    skill_service = AgentSkillService(sqlite_session_factory)
    planning_service = AgentPlanningService(sqlite_session_factory)
    await memory_service.create_memory(
        user_id="user-123",
        request=MemoryCreateRequest(
            kind=MemoryKind.PREFERENCE,
            text="PRIVATE MEMORY SENTINEL",
        ),
    )
    await skill_service.create_user_skill(
        user_id="user-123",
        request=SkillCreateRequest(
            name="Private Skill",
            slug="private-skill",
            description="PRIVATE SKILL SENTINEL",
            body_markdown="Private instructions.",
        ),
    )
    await planning_service.create_plan(
        user_id="user-123",
        request=TaskPlanCreateRequest(
            objective="PRIVATE PLAN SENTINEL",
            steps=("Keep private",),
        ),
    )
    provider = PersonaVoiceInstructionProvider(
        persona_service=AgentPersonaService(
            default_persona=AgentPersona(
                name="PRIVATE PERSONA SENTINEL",
                role="private role",
            ),
        ),
        memory_service=memory_service,
        skill_service=skill_service,
        planning_service=planning_service,
        clock=FakeClock(),
    )
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="PRIVATE CUSTOM INSTRUCTIONS SENTINEL",
        ),
    )
    policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        entries=(
            EffectiveToolPolicyEntry(
                tool_name="web_search_exa",
                tool_source=ToolGrantScope.MCP_SERVER,
                mcp_server_id="server-123",
                risk=ToolRisk.LOW,
            ),
        ),
    )

    instructions = await provider.render_instructions_for_session(
        scope=AgentScope(
            owner_user_id="user-123",
            agent_id="agent-123",
            session_id="session-123",
            channel="livekit",
            source="voice.worker",
        ),
        preferences=preferences,
        caller_access=VoiceCallerAccess(
            level=VoiceCallerAccessLevel.LIMITED_CONTACT,
            contact_display_name="Ada Caller",
        ),
        effective_tool_policy=policy,
    )

    assert "Ada Caller" in instructions
    assert "web_search_exa" in instructions
    assert "PRIVATE CUSTOM INSTRUCTIONS SENTINEL" not in instructions
    assert "PRIVATE PERSONA SENTINEL" not in instructions
    assert "PRIVATE MEMORY SENTINEL" not in instructions
    assert "PRIVATE SKILL SENTINEL" not in instructions
    assert "PRIVATE PLAN SENTINEL" not in instructions
    assert "# Factual Accuracy And Web Grounding" in instructions
    assert "offer to provide their links in a follow-up message" in instructions
    assert "30 to 50 spoken words" in instructions


@pytest.mark.asyncio
async def test_limited_caller_instructions_use_saved_agent_identity(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    updated_agent = await identity_service.update_agent(
        owner_user_id="user-123",
        agent_id=agent.id,
        request=AgentUpdateRequest(
            display_name="Lars",
            description="Personal voice assistant",
        ),
    )
    assert updated_agent is not None
    provider = PersonaVoiceInstructionProvider(
        persona_service=AgentPersonaService(),
        identity_service=identity_service,
        clock=FakeClock(),
    )
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="Private owner instructions.",
        ),
    )

    instructions = await provider.render_instructions_for_session(
        scope=AgentScope(
            owner_user_id="user-123",
            agent_id=agent.id,
            session_id="session-123",
            channel="livekit",
            source="voice.worker",
        ),
        preferences=preferences,
        caller_access=VoiceCallerAccess(level=VoiceCallerAccessLevel.LIMITED_CONTACT),
        effective_tool_policy=EffectiveToolPolicySnapshot(
            user_id="user-123",
            agent_id=agent.id,
        ),
    )

    assert "# Agent Identity" in instructions
    assert "- Name: Lars" in instructions
    assert "- Description: Personal voice assistant" in instructions
    assert "for the account owner" in instructions
    assert "Voxos user" not in instructions


@pytest.mark.asyncio
async def test_admin_caller_instructions_keep_approved_private_context() -> None:
    provider = PersonaVoiceInstructionProvider(
        persona_service=AgentPersonaService(
            default_persona=AgentPersona(
                name="Ada Owner Persona",
                role="private assistant",
            ),
        ),
        tool_policy_context=ToolPolicyContext(
            available_tools=("get_current_datetime", "create_agent_memory"),
        ),
        clock=FakeClock(),
    )
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="Owner-approved voice instructions.",
        ),
    )
    policy = EffectiveToolPolicySnapshot(
        user_id="user-123",
        agent_id="agent-123",
        entries=(
            EffectiveToolPolicyEntry(
                tool_name="get_current_datetime",
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.LOW,
            ),
        ),
    )

    instructions = await provider.render_instructions_for_session(
        scope=AgentScope(
            owner_user_id="user-123",
            agent_id="agent-123",
            session_id="session-123",
            channel="livekit",
            source="voice.worker",
        ),
        preferences=preferences,
        caller_access=VoiceCallerAccess(
            level=VoiceCallerAccessLevel.ADMIN_CONTACT,
            contact_display_name="Admin Caller",
        ),
        effective_tool_policy=policy,
    )

    assert "Owner-approved voice instructions." in instructions
    assert "Ada Owner Persona" in instructions
    assert "Available tools: get_current_datetime" in instructions
    assert "Restricted actions: create_agent_memory" in instructions

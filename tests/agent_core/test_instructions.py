from datetime import UTC, datetime

import pytest

from voxos.agent_core.instructions import (
    AgentInstructionSessionSettings,
    PersonaAgentInstructionProvider,
)
from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_identity.models import AgentScope, AgentUpdateRequest
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_skills.models import SkillCapabilityPreference, SkillCreateRequest
from voxos.agent_skills.services import AgentSkillService
from voxos.db.session import AsyncSessionFactory
from voxos.location.models import ClientLocation
from voxos.persona.models import AgentPersona
from voxos.persona.services import AgentPersonaService


@pytest.mark.asyncio
async def test_scoped_instructions_use_saved_agent_identity(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent("user-123")
    updated_agent = await identity_service.update_agent(
        owner_user_id="user-123",
        agent_id=agent.id,
        request=AgentUpdateRequest(
            display_name="Lars",
            description="Personal research assistant",
        ),
    )
    assert updated_agent is not None
    provider = PersonaAgentInstructionProvider(
        persona_service=AgentPersonaService(
            default_persona=AgentPersona(name="Voxos", role="voice assistant"),
        ),
        identity_service=identity_service,
    )

    instructions = await provider.render_instructions(
        scope=AgentScope(
            owner_user_id="user-123",
            agent_id=agent.id,
            session_id="session-123",
            channel="text",
            source="text.web_chat",
        ),
        user_id="user-123",
        settings=AgentInstructionSessionSettings(
            provider="openai",
            model="gpt-4.1-mini",
            base_agent_instructions="Help the user.",
            session_mode="text_chat",
            communication_medium=CommunicationMedium.WEB_CHAT,
        ),
    )

    assert "# Agent Identity" in instructions
    assert "- Name: Lars" in instructions
    assert "- Description: Personal research assistant" in instructions
    assert "- Name: Voxos" not in instructions
    assert "- Role: voice assistant" not in instructions
    assert "do not substitute a product or platform name" in instructions
    assert "# Factual Accuracy And Web Grounding" in instructions
    assert "A request to search, browse, look up, verify, fact-check, or double-check" in instructions
    assert "Never claim to have searched unless a search tool completed successfully" in instructions


@pytest.mark.asyncio
async def test_instructions_prioritize_skills_matched_from_current_task_query(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    skill_service = AgentSkillService(sqlite_session_factory)
    await skill_service.create_user_skill(
        user_id="user-123",
        request=SkillCreateRequest(
            name="Grounded Local Day Plan",
            slug="grounded-local-day-plan",
            category="tool_use",
            description="Find verified local places with Google Maps before recommending them.",
            body_markdown="Use search_places before making restaurant recommendations.",
            preferred_capabilities=(
                SkillCapabilityPreference(
                    capability="google-maps-grounding-lite",
                    tool_names=("search_places",),
                ),
            ),
        ),
    )
    provider = PersonaAgentInstructionProvider(
        persona_service=AgentPersonaService(),
        skill_service=skill_service,
    )

    instructions = await provider.render_instructions(
        scope=None,
        user_id="user-123",
        settings=AgentInstructionSessionSettings(
            provider="openai",
            model="gpt-4.1-mini",
            base_agent_instructions="Help the user.",
            task_query="Find restaurants in Nova Place using Google Maps reviews.",
            session_mode="text_chat",
        ),
    )

    assert "# Task-Relevant Skills" in instructions
    assert "Use get_agent_skill to read its full body" in instructions
    assert "grounded-local-day-plan" in instructions
    assert "Preferred: google-maps-grounding-lite: search_places." in instructions


@pytest.mark.asyncio
async def test_instructions_include_user_shared_current_location() -> None:
    provider = PersonaAgentInstructionProvider(
        persona_service=AgentPersonaService(),
    )

    instructions = await provider.render_instructions(
        scope=None,
        user_id="user-123",
        settings=AgentInstructionSessionSettings(
            provider="openai",
            model="gpt-5.4-nano",
            base_agent_instructions="Help the user.",
            session_mode="text_chat",
            current_location=ClientLocation(
                latitude=42.4304,
                longitude=19.2594,
                horizontal_accuracy_m=30,
                captured_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
            ),
        ),
    )

    assert "# Current Device Location" in instructions
    assert "Latitude: 42.4304000" in instructions
    assert "Do not invent a street address" in instructions

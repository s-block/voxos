from dataclasses import dataclass, field

import pytest
from mcp.server.auth.provider import AccessToken

from voxos.mcp.auth import McpTransportAuthConfig
from voxos.persona.mcp import (
    PERSONA_MCP_TOOL_NAMES,
    AgentPersonaMcpTools,
    PersonaMcpUpdateRequest,
    VoicePreferencesMcpUpdateRequest,
    create_persona_mcp_server,
)
from voxos.persona.models import AgentSoul, VerbosityLevel
from voxos.persona.rules import (
    PersonaRule,
    PersonaRuleCategory,
    PersonaRuleEffect,
)
from voxos.persona.services import AgentPersonaService
from voxos.persona.updates import (
    PersonaField,
    PersonaFieldChange,
    PersonaRuleChange,
    PersonaRuleUpdateAction,
    PersonaUpdateStatus,
)
from voxos.providers.models import OpenAIRealtimeConfig
from voxos.voice.errors import VoicePreferencesNotFoundError
from voxos.voice.models import UserVoicePreferences


@dataclass(slots=True)
class InMemoryPersonaStore:
    souls: dict[str, AgentSoul] = field(default_factory=dict)
    saved_souls: list[AgentSoul] = field(default_factory=list)

    async def get_soul(self, user_id: str) -> AgentSoul | None:
        return self.souls.get(user_id)

    async def save_soul(self, soul: AgentSoul) -> None:
        self.souls[soul.user_id] = soul
        self.saved_souls.append(soul)


@dataclass(slots=True)
class InMemoryVoicePreferenceManager:
    preferences_by_user: dict[str, UserVoicePreferences] = field(default_factory=dict)
    saved_preferences: list[UserVoicePreferences] = field(default_factory=list)

    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        preferences = self.preferences_by_user.get(user_id)
        if preferences is None:
            raise VoicePreferencesNotFoundError(user_id)
        return preferences

    async def set_preferences(self, preferences: UserVoicePreferences) -> object:
        self.preferences_by_user[preferences.user_id] = preferences
        self.saved_preferences.append(preferences)
        return preferences


@dataclass(frozen=True, slots=True)
class StaticMcpUserResolver:
    user_id: str

    def require_user_id(self) -> str:
        return self.user_id


class NullTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        return None


def mcp_auth_config() -> McpTransportAuthConfig:
    return McpTransportAuthConfig(
        token_verifier=NullTokenVerifier(),
        issuer_url="https://issuer.example.com",
        resource_server_url="https://resource.example.com",
    )


@pytest.mark.asyncio
async def test_persona_mcp_tools_scope_updates_to_authenticated_user() -> None:
    store = InMemoryPersonaStore(
        souls={
            "user-a": AgentSoul(user_id="user-a"),
            "user-b": AgentSoul(user_id="user-b"),
        },
    )
    service = AgentPersonaService(store=store)
    tools = AgentPersonaMcpTools(
        persona_service=service,
        user_resolver=StaticMcpUserResolver("user-b"),
    )

    result = await tools.update_agent_persona(
        PersonaMcpUpdateRequest(
            field_changes=(
                PersonaFieldChange(
                    field=PersonaField.VERBOSITY,
                    value=VerbosityLevel.DETAILED.value,
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.APPLIED
    assert result.snapshot.persona.verbosity == VerbosityLevel.DETAILED
    assert store.souls["user-a"].persona.verbosity == VerbosityLevel.CONCISE
    assert store.souls["user-b"].persona.verbosity == VerbosityLevel.DETAILED


@pytest.mark.asyncio
async def test_persona_mcp_update_requires_confirmation_for_protected_rule() -> None:
    rule = PersonaRule(
        id="never-update-calendar-without-confirmation",
        category=PersonaRuleCategory.CALENDAR,
        effect=PersonaRuleEffect.REQUIRE_CONFIRMATION,
        subject="update_calendar_event",
        description="Never update calendar events without confirmation.",
        priority=95,
        requires_confirmation_to_change=True,
    )
    store = InMemoryPersonaStore(
        souls={"user-123": AgentSoul(user_id="user-123", rules=(rule,))},
    )
    tools = AgentPersonaMcpTools(
        persona_service=AgentPersonaService(store=store),
        user_resolver=StaticMcpUserResolver("user-123"),
    )

    result = await tools.update_agent_persona(
        PersonaMcpUpdateRequest(
            rule_changes=(
                PersonaRuleChange(
                    action=PersonaRuleUpdateAction.DISABLE,
                    rule_id=rule.id,
                ),
            ),
        ),
    )

    assert result.status == PersonaUpdateStatus.NEEDS_CONFIRMATION
    assert result.pending_changes == ("rule:disable:never-update-calendar-without-confirmation",)
    assert store.saved_souls == []


@pytest.mark.asyncio
async def test_persona_mcp_tool_updates_voice_language_preference() -> None:
    voice_preferences = InMemoryVoicePreferenceManager(
        preferences_by_user={
            "user-123": UserVoicePreferences(
                user_id="user-123",
                model="gpt-realtime",
                voice="marin",
                credential_ref="openai-local",
                provider_config=OpenAIRealtimeConfig(
                    agent_instructions="Be helpful.",
                    preferred_language="Italian",
                ),
            ),
        },
    )
    tools = AgentPersonaMcpTools(
        persona_service=AgentPersonaService(),
        user_resolver=StaticMcpUserResolver("user-123"),
        voice_preference_manager=voice_preferences,
    )

    result = await tools.update_voice_preferences(
        VoicePreferencesMcpUpdateRequest(preferred_language="English"),
    )

    assert result.status == "applied"
    assert result.preferences is not None
    assert result.preferences.preferred_language == "English"
    assert result.preferences.model == "gpt-realtime"
    assert result.preferences.voice == "marin"
    assert len(voice_preferences.saved_preferences) == 1
    saved_preferences = voice_preferences.saved_preferences[0]
    assert saved_preferences.provider_config.preferred_language == "English"
    assert saved_preferences.credential_ref == "openai-local"


@pytest.mark.asyncio
async def test_create_persona_mcp_server_registers_persona_tools() -> None:
    server = create_persona_mcp_server(
        persona_service=AgentPersonaService(),
        auth_config=mcp_auth_config(),
    )

    tools = await server.list_tools()

    assert tuple(tool.name for tool in tools) == PERSONA_MCP_TOOL_NAMES


def test_mcp_server_auth_config_requires_transport_auth() -> None:
    with pytest.raises(ValueError, match="token_verifier"):
        McpTransportAuthConfig(
            token_verifier=None,  # type: ignore[arg-type]
            issuer_url="https://issuer.example.com",
            resource_server_url="https://resource.example.com",
        )


def test_mcp_server_auth_config_requires_auth_metadata() -> None:
    with pytest.raises(ValueError, match="issuer_url and resource_server_url"):
        McpTransportAuthConfig(
            token_verifier=NullTokenVerifier(),
            issuer_url=" ",
            resource_server_url="https://resource.example.com",
        )

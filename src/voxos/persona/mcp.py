from dataclasses import dataclass
from typing import Literal, Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from voxos.db.tenant import tenant_user_context
from voxos.mcp.auth import (
    AuthenticatedMcpUserResolver,
    McpTransportAuthConfig,
    McpUserResolver,
)
from voxos.persona.models import AgentPersona, AgentSoul
from voxos.persona.rules import PersonaRule
from voxos.persona.services import AgentPersonaService
from voxos.persona.updates import (
    PersonaFieldChange,
    PersonaRuleChange,
    PersonaUpdateRequest,
    PersonaUpdateResult,
    PersonaUpdateSource,
    PersonaUpdateStatus,
)
from voxos.voice.errors import VoicePreferencesNotFoundError
from voxos.voice.models import UserVoicePreferences

PERSONA_MCP_TOOL_NAMES = (
    "get_agent_persona",
    "update_agent_persona",
    "update_voice_preferences",
)
VoicePreferencesMcpUpdateStatus = Literal["applied", "unavailable"]


class VoicePreferenceManager(Protocol):
    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        """Load non-secret voice preferences for a user."""

    async def set_preferences(
        self,
        preferences: UserVoicePreferences,
    ) -> object:
        """Persist non-secret voice preferences for a user."""


class PersonaMcpSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    persona: AgentPersona
    rules: tuple[PersonaRule, ...] = Field(default_factory=tuple)
    version: int = Field(ge=1)

    @classmethod
    def from_soul(cls, soul: AgentSoul) -> "PersonaMcpSnapshot":
        return cls(
            persona=soul.persona,
            rules=soul.rules,
            version=soul.version,
        )


class PersonaMcpUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_changes: tuple[PersonaFieldChange, ...] = Field(default_factory=tuple)
    rule_changes: tuple[PersonaRuleChange, ...] = Field(default_factory=tuple)
    confirmed: bool = False
    confirmation_token: str | None = Field(default=None, min_length=1, max_length=256)
    reason: str | None = Field(default=None, min_length=1, max_length=1024)


class PersonaMcpUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PersonaUpdateStatus
    snapshot: PersonaMcpSnapshot
    applied_changes: tuple[str, ...] = Field(default_factory=tuple)
    pending_changes: tuple[str, ...] = Field(default_factory=tuple)
    blocked_changes: tuple[str, ...] = Field(default_factory=tuple)

    @classmethod
    def from_update_result(
        cls,
        result: PersonaUpdateResult,
    ) -> "PersonaMcpUpdateResult":
        return cls(
            status=result.status,
            snapshot=PersonaMcpSnapshot.from_soul(result.soul),
            applied_changes=result.applied_changes,
            pending_changes=result.pending_changes,
            blocked_changes=result.blocked_changes,
        )


class VoicePreferencesMcpSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    voice: str
    preferred_language: str
    agent_instructions: str
    initial_reply_instructions: str | None

    @classmethod
    def from_preferences(
        cls,
        preferences: UserVoicePreferences,
    ) -> "VoicePreferencesMcpSnapshot":
        return cls(
            provider=preferences.provider.value,
            model=preferences.model,
            voice=preferences.voice,
            preferred_language=preferences.provider_config.preferred_language,
            agent_instructions=preferences.provider_config.agent_instructions,
            initial_reply_instructions=(preferences.provider_config.initial_reply_instructions),
        )


class VoicePreferencesMcpUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = Field(default=None, min_length=1, max_length=256)
    voice: str | None = Field(default=None, min_length=1, max_length=256)
    preferred_language: str | None = Field(default=None, min_length=1, max_length=128)
    agent_instructions: str | None = Field(default=None, min_length=1)
    initial_reply_instructions: str | None = Field(default=None, min_length=1)

    @field_validator(
        "model",
        "voice",
        "preferred_language",
        "agent_instructions",
        "initial_reply_instructions",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @model_validator(mode="after")
    def validate_has_changes(self) -> "VoicePreferencesMcpUpdateRequest":
        if (
            self.model is None
            and self.voice is None
            and self.preferred_language is None
            and self.agent_instructions is None
            and self.initial_reply_instructions is None
        ):
            message = "voice preference update request must include at least one change"
            raise ValueError(message)
        return self


class VoicePreferencesMcpUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VoicePreferencesMcpUpdateStatus
    preferences: VoicePreferencesMcpSnapshot | None = None
    message: str | None = Field(default=None, min_length=1)


@dataclass(frozen=True, slots=True)
class AgentPersonaMcpTools:
    persona_service: AgentPersonaService
    user_resolver: McpUserResolver
    voice_preference_manager: VoicePreferenceManager | None = None

    async def get_agent_persona(self) -> PersonaMcpSnapshot:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            soul = await self.persona_service.load_soul(user_id)
        return PersonaMcpSnapshot.from_soul(soul)

    async def update_agent_persona(
        self,
        request: PersonaMcpUpdateRequest,
    ) -> PersonaMcpUpdateResult:
        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            result = await self.persona_service.update_soul(
                PersonaUpdateRequest(
                    user_id=user_id,
                    source=PersonaUpdateSource.TOOL,
                    field_changes=request.field_changes,
                    rule_changes=request.rule_changes,
                    confirmed=request.confirmed,
                    confirmation_token=request.confirmation_token,
                    reason=request.reason,
                ),
            )
        return PersonaMcpUpdateResult.from_update_result(result)

    async def update_voice_preferences(
        self,
        request: VoicePreferencesMcpUpdateRequest,
    ) -> VoicePreferencesMcpUpdateResult:
        if self.voice_preference_manager is None:
            return VoicePreferencesMcpUpdateResult(
                status="unavailable",
                message="Voice preference storage is not configured for this MCP server.",
            )

        user_id = self.user_resolver.require_user_id()
        with tenant_user_context(user_id):
            try:
                preferences = await self.voice_preference_manager.get_preferences(user_id)
            except VoicePreferencesNotFoundError:
                return VoicePreferencesMcpUpdateResult(
                    status="unavailable",
                    message="Save an OpenAI key before updating voice preferences.",
                )

            provider_config = preferences.provider_config.model_copy(
                update={
                    key: value
                    for key, value in {
                        "preferred_language": request.preferred_language,
                        "agent_instructions": request.agent_instructions,
                        "initial_reply_instructions": request.initial_reply_instructions,
                    }.items()
                    if value is not None
                },
            )
            updated_preferences = preferences.model_copy(
                update={
                    key: value
                    for key, value in {
                        "model": request.model,
                        "voice": request.voice,
                        "provider_config": provider_config,
                    }.items()
                    if value is not None
                },
            )
            await self.voice_preference_manager.set_preferences(updated_preferences)
        return VoicePreferencesMcpUpdateResult(
            status="applied",
            preferences=VoicePreferencesMcpSnapshot.from_preferences(updated_preferences),
        )


def create_persona_mcp_server(
    persona_service: AgentPersonaService,
    auth_config: McpTransportAuthConfig,
    voice_preference_manager: VoicePreferenceManager | None = None,
) -> FastMCP:
    tools = AgentPersonaMcpTools(
        persona_service=persona_service,
        user_resolver=AuthenticatedMcpUserResolver(),
        voice_preference_manager=voice_preference_manager,
    )
    mcp = FastMCP(
        name="Voxos Persona",
        instructions=(
            "Manage the authenticated user's Voxos agent persona, durable "
            "behaviour rules, and non-secret voice preferences. The authenticated "
            "user is derived from the bearer token; tools must not accept or "
            "override user IDs."
        ),
        json_response=True,
        token_verifier=auth_config.token_verifier,
        auth=auth_config.auth_settings(),
    )

    @mcp.tool(
        name="get_agent_persona",
        description="Read the authenticated user's current agent persona and rules.",
    )
    async def get_agent_persona() -> PersonaMcpSnapshot:
        return await tools.get_agent_persona()

    @mcp.tool(
        name="update_agent_persona",
        description=(
            "Apply or propose structured persona/rule changes for the "
            "authenticated user. Protected changes return needs_confirmation "
            "unless confirmed is true."
        ),
    )
    async def update_agent_persona(
        request: PersonaMcpUpdateRequest,
    ) -> PersonaMcpUpdateResult:
        return await tools.update_agent_persona(request)

    @mcp.tool(
        name="update_voice_preferences",
        description=(
            "Update non-secret voice preferences for the authenticated user, "
            "including preferred_language, model, voice, and agent instructions. "
            "This tool never reads or returns provider credentials."
        ),
    )
    async def update_voice_preferences(
        request: VoicePreferencesMcpUpdateRequest,
    ) -> VoicePreferencesMcpUpdateResult:
        return await tools.update_voice_preferences(request)

    return mcp

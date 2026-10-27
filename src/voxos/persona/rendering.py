from pydantic import BaseModel, ConfigDict, Field

from voxos.agent_core.time_context import CurrentDatetimeContext
from voxos.agent_identity.models import AgentIdentity
from voxos.agent_identity.rendering import render_agent_identity_section
from voxos.persona.models import AgentPersona, AgentSoul
from voxos.persona.rules import PersonaRule, active_persona_rules


class CurrentVoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    voice: str = Field(min_length=1, max_length=256)
    preferred_language: str = Field(min_length=1, max_length=128)
    modalities: tuple[str, ...] = Field(default_factory=tuple)
    speed: float | None = Field(default=None, gt=0)
    session_mode: str | None = Field(default=None, min_length=1, max_length=128)


class ToolPolicyContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available_tools: tuple[str, ...] = Field(default_factory=tuple)
    restricted_actions: tuple[str, ...] = Field(default_factory=tuple)
    confirmation_required_actions: tuple[str, ...] = Field(default_factory=tuple)


class AgentInstructionRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    base_agent_instructions: str = Field(min_length=1)
    soul: AgentSoul
    agent_identity: AgentIdentity | None = None
    long_term_memory_section: str | None = Field(default=None, min_length=1)
    available_skills_section: str | None = Field(default=None, min_length=1)
    active_task_plan_section: str | None = Field(default=None, min_length=1)
    current_voice_settings: CurrentVoiceSettings | None = None
    tool_policy_context: ToolPolicyContext | None = None
    current_datetime_context: CurrentDatetimeContext | None = None


class AgentInstructionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_instructions: str = Field(min_length=1)
    included_rule_ids: tuple[str, ...] = Field(default_factory=tuple)
    persona_version: int = Field(ge=1)


class AgentInstructionRenderer:
    def render(
        self,
        request: AgentInstructionRenderRequest,
    ) -> AgentInstructionBundle:
        rules = active_persona_rules(
            (*request.soul.persona.persistent_rules, *request.soul.rules),
        )
        sections = [self._base_section(request.base_agent_instructions)]
        if request.agent_identity is not None:
            sections.append(render_agent_identity_section(request.agent_identity))
        sections.append(
            self._persona_section(
                request.soul.persona,
                include_identity=request.agent_identity is None,
            ),
        )
        rules_section = self._rules_section(rules)
        if rules_section:
            sections.append(rules_section)
        self._append_optional_section(sections, request.long_term_memory_section)
        self._append_optional_section(sections, request.available_skills_section)
        self._append_optional_section(sections, request.active_task_plan_section)
        if request.current_voice_settings is not None:
            sections.append(self._voice_section(request.current_voice_settings))
        if request.tool_policy_context is not None:
            sections.append(self._tool_policy_section(request.tool_policy_context))
        if request.current_datetime_context is not None:
            sections.append(self._datetime_section(request.current_datetime_context))

        return AgentInstructionBundle(
            system_instructions="\n\n".join(sections),
            included_rule_ids=tuple(rule.id for rule in rules),
            persona_version=request.soul.version,
        )

    def _base_section(self, base_agent_instructions: str) -> str:
        return "\n".join(
            (
                "# Base Agent Instructions",
                base_agent_instructions.strip(),
            ),
        )

    def _persona_section(
        self,
        persona: AgentPersona,
        *,
        include_identity: bool,
    ) -> str:
        lines = ["# Agent Persona"]
        if include_identity:
            lines.extend(
                (
                    f"- Name: {persona.name}",
                    f"- Role: {persona.role}",
                ),
            )
        lines.extend(
            (
                f"- Tone: {persona.tone.value}",
                f"- Humour style: {persona.humour_style.value}",
                f"- Formality: {persona.formality.value}",
                f"- Warmth: {persona.warmth.value}",
                f"- Verbosity: {persona.verbosity.value}",
                f"- Speaking style: {self._join_values(persona.speaking_style)}",
            ),
        )
        if persona.behavioural_boundaries:
            lines.append(
                f"- Behavioural boundaries: {self._join_values(persona.behavioural_boundaries)}",
            )
        if persona.user_preferences:
            lines.append(
                f"- User preferences: {self._join_values(persona.user_preferences)}",
            )
        if persona.backstory:
            lines.append(f"- Backstory: {persona.backstory}")
        return "\n".join(lines)

    def _rules_section(self, rules: tuple[PersonaRule, ...]) -> str | None:
        if not rules:
            return None

        lines = ["# Persistent Rules"]
        for rule in rules:
            rule_text = (
                f"- [{rule.id}] priority={rule.priority} "
                f"category={rule.category.value} effect={rule.effect.value} "
                f"subject={rule.subject}"
            )
            if rule.condition is not None:
                rule_text = f"{rule_text} condition={rule.condition}"
            if rule.description is not None:
                rule_text = f"{rule_text}: {rule.description}"
            lines.append(rule_text)
        return "\n".join(lines)

    def _voice_section(self, voice_settings: CurrentVoiceSettings) -> str:
        lines = [
            "# Current Voice Settings",
            f"- Provider: {voice_settings.provider}",
            f"- Model: {voice_settings.model}",
            f"- Voice: {voice_settings.voice}",
            f"- Preferred language: {voice_settings.preferred_language}",
        ]
        if voice_settings.modalities:
            lines.append(f"- Modalities: {self._join_values(voice_settings.modalities)}")
        if voice_settings.speed is not None:
            lines.append(f"- Speed: {voice_settings.speed}")
        if voice_settings.session_mode is not None:
            lines.append(f"- Session mode: {voice_settings.session_mode}")
        return "\n".join(lines)

    def _tool_policy_section(self, tool_policy: ToolPolicyContext) -> str:
        lines = ["# Tool Policy Context"]
        if tool_policy.available_tools:
            lines.append(
                f"- Available tools: {self._join_values(tool_policy.available_tools)}",
            )
        if tool_policy.restricted_actions:
            lines.append(
                f"- Restricted actions: {self._join_values(tool_policy.restricted_actions)}",
            )
        if tool_policy.confirmation_required_actions:
            lines.append(
                f"- Confirmation required actions: {self._join_values(tool_policy.confirmation_required_actions)}",
            )
        if len(lines) == 1:
            lines.append("- No additional tool policy context.")
        return "\n".join(lines)

    def _datetime_section(self, datetime_context: CurrentDatetimeContext) -> str:
        lines = [
            "# Current Datetime Context",
            f"- Current datetime (UTC): {datetime_context.current_datetime_utc}",
        ]
        if datetime_context.chat_started_at_utc is not None:
            lines.append(f"- Chat started at (UTC): {datetime_context.chat_started_at_utc}")
        if datetime_context.voice_session_started_at_utc is not None:
            lines.append(
                f"- Voice session started at (UTC): {datetime_context.voice_session_started_at_utc}",
            )
        lines.append(
            "- For exact current time, call get_current_datetime before answering time-sensitive questions.",
        )
        return "\n".join(lines)

    def _join_values(self, values: tuple[str, ...]) -> str:
        return ", ".join(values)

    def _append_optional_section(
        self,
        sections: list[str],
        optional_section: str | None,
    ) -> None:
        if optional_section is None:
            return
        stripped_section = optional_section.strip()
        if stripped_section:
            sections.append(stripped_section)

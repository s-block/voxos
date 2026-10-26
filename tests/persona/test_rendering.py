from datetime import UTC, datetime

from voxos.agent_core.time_context import CurrentDatetimeContext
from voxos.persona.models import AgentPersona, AgentSoul, PersonaTone, VerbosityLevel
from voxos.persona.rendering import (
    AgentInstructionRenderer,
    AgentInstructionRenderRequest,
    CurrentVoiceSettings,
    ToolPolicyContext,
)
from voxos.persona.rules import (
    PersonaRule,
    PersonaRuleCategory,
    PersonaRuleEffect,
)


def test_renderer_combines_sections_in_deterministic_order() -> None:
    low_rule = PersonaRule(
        id="reply-concise",
        category=PersonaRuleCategory.COMMUNICATION,
        effect=PersonaRuleEffect.PREFER,
        subject="reply_length",
        description="Keep replies concise unless the user asks for detail.",
        priority=50,
    )
    high_rule = PersonaRule(
        id="email-confirmation",
        category=PersonaRuleCategory.EMAIL,
        effect=PersonaRuleEffect.REQUIRE_CONFIRMATION,
        subject="send_email",
        description="Always ask before sending emails.",
        priority=90,
    )
    disabled_rule = PersonaRule(
        id="disabled-phone-rule",
        category=PersonaRuleCategory.PHONE,
        effect=PersonaRuleEffect.FORBID,
        subject="phone_calls",
        description="Do not render this rule.",
        priority=100,
        enabled=False,
    )
    soul = AgentSoul(
        user_id="user-123",
        persona=AgentPersona(
            name="Ada",
            role="technical assistant",
            tone=PersonaTone.DIRECT,
            verbosity=VerbosityLevel.CONCISE,
            speaking_style=("plain", "specific"),
        ),
        rules=(low_rule, high_rule, disabled_rule),
    )

    bundle = AgentInstructionRenderer().render(
        AgentInstructionRenderRequest(
            base_agent_instructions="Base instructions.",
            soul=soul,
            long_term_memory_section="# Long-term User Memory\n- (preference) Use concise updates.",
            available_skills_section="# Available Skills\n- planning (planning, developer): Break work into steps.",
            active_task_plan_section="# Active Task Plan\n- Objective: Ship memory [active]",
            current_voice_settings=CurrentVoiceSettings(
                provider="openai_realtime",
                model="gpt-realtime",
                voice="marin",
                preferred_language="English",
                modalities=("text", "audio"),
                speed=1.1,
                session_mode="realtime_voice",
            ),
            tool_policy_context=ToolPolicyContext(
                available_tools=("email", "calendar"),
                restricted_actions=("phone_calls_after_8pm",),
                confirmation_required_actions=("send_email", "update_calendar"),
            ),
            current_datetime_context=CurrentDatetimeContext.from_datetimes(
                current_datetime=datetime(2026, 7, 9, 12, 34, 56, tzinfo=UTC),
                chat_started_at=datetime(2026, 7, 9, 12, 0, 0, tzinfo=UTC),
            ),
        ),
    )

    instructions = bundle.system_instructions
    assert instructions.index("# Base Agent Instructions") < instructions.index(
        "# Agent Persona",
    )
    assert instructions.index("email-confirmation") < instructions.index(
        "reply-concise",
    )
    assert instructions.index("# Persistent Rules") < instructions.index(
        "# Long-term User Memory",
    )
    assert instructions.index("# Long-term User Memory") < instructions.index(
        "# Available Skills",
    )
    assert instructions.index("# Available Skills") < instructions.index(
        "# Active Task Plan",
    )
    assert instructions.index("# Active Task Plan") < instructions.index(
        "# Current Voice Settings",
    )
    assert instructions.index("# Current Voice Settings") < instructions.index(
        "# Tool Policy Context",
    )
    assert instructions.index("# Tool Policy Context") < instructions.index(
        "# Current Datetime Context",
    )
    assert "- Current datetime (UTC): 2026-07-09T12:34:56+00:00" in instructions
    assert "- Chat started at (UTC): 2026-07-09T12:00:00+00:00" in instructions
    assert "disabled-phone-rule" not in instructions
    assert bundle.included_rule_ids == ("email-confirmation", "reply-concise")

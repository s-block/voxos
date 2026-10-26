import pytest
from pydantic import ValidationError

from voxos.persona.models import AgentPersona, AgentSoul
from voxos.persona.rules import (
    PersonaRule,
    PersonaRuleCategory,
    PersonaRuleEffect,
    PersonaRuleSource,
)


def test_agent_persona_rejects_empty_identity_and_style() -> None:
    with pytest.raises(ValidationError):
        AgentPersona(name=" ")

    with pytest.raises(ValidationError):
        AgentPersona(speaking_style=("clear", " "))


def test_agent_soul_is_user_scoped_and_contains_typed_rules() -> None:
    rule = PersonaRule(
        id="email-confirmation",
        category=PersonaRuleCategory.EMAIL,
        effect=PersonaRuleEffect.REQUIRE_CONFIRMATION,
        subject="send_email",
        description="Always ask before sending emails.",
        priority=90,
        source=PersonaRuleSource.USER,
        requires_confirmation_to_change=True,
    )

    soul = AgentSoul(user_id=" user-123 ", rules=(rule,))

    assert soul.user_id == "user-123"
    assert soul.rules[0].category == PersonaRuleCategory.EMAIL
    assert soul.rules[0].is_protected is True


def test_locale_rule_can_represent_british_english_preference() -> None:
    rule = PersonaRule(
        id="locale-british-english",
        category=PersonaRuleCategory.LOCALE,
        effect=PersonaRuleEffect.REQUIRE,
        subject="english_variant",
        description="Use British English.",
        priority=70,
    )

    assert rule.subject == "english_variant"
    assert rule.description == "Use British English."

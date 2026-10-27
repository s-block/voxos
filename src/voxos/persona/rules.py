from collections.abc import Iterable
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

HIGH_PRIORITY_RULE_THRESHOLD = 80


class PersonaRuleCategory(StrEnum):
    COMMUNICATION = "communication"
    LOCALE = "locale"
    EMAIL = "email"
    PHONE = "phone"
    CALENDAR = "calendar"
    TOOLS = "tools"
    PRIVACY = "privacy"
    SAFETY = "safety"
    USER_PREFERENCE = "user_preference"


class PersonaRuleEffect(StrEnum):
    REQUIRE = "require"
    PREFER = "prefer"
    FORBID = "forbid"
    REQUIRE_CONFIRMATION = "require_confirmation"


class PersonaRuleSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"
    ADMIN = "admin"


class PersonaRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    category: PersonaRuleCategory
    effect: PersonaRuleEffect
    subject: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=1024)
    condition: str | None = Field(default=None, min_length=1, max_length=512)
    priority: int = Field(default=50, ge=0, le=100)
    source: PersonaRuleSource = PersonaRuleSource.USER
    enabled: bool = True
    requires_confirmation_to_change: bool = False

    @field_validator("id", "subject", "description", "condition", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def is_high_priority(self) -> bool:
        return self.priority >= HIGH_PRIORITY_RULE_THRESHOLD

    @property
    def is_protected(self) -> bool:
        return (
            self.requires_confirmation_to_change
            or self.is_high_priority
            or self.category in {PersonaRuleCategory.PRIVACY, PersonaRuleCategory.SAFETY}
        )


def enabled_persona_rules(
    rules: Iterable[PersonaRule],
) -> tuple[PersonaRule, ...]:
    return tuple(rule for rule in rules if rule.enabled)


def sort_persona_rules_by_priority(
    rules: Iterable[PersonaRule],
) -> tuple[PersonaRule, ...]:
    return tuple(sorted(rules, key=lambda rule: (-rule.priority, rule.id)))


def active_persona_rules(
    rules: Iterable[PersonaRule],
) -> tuple[PersonaRule, ...]:
    return sort_persona_rules_by_priority(enabled_persona_rules(rules))

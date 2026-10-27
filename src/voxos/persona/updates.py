from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voxos.persona.models import AgentSoul
from voxos.persona.rules import PersonaRule

PersonaFieldValue = str | tuple[str, ...] | None


class PersonaUpdateSource(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    SYSTEM = "system"
    ADMIN = "admin"


class PersonaField(StrEnum):
    NAME = "name"
    ROLE = "role"
    TONE = "tone"
    HUMOUR_STYLE = "humour_style"
    FORMALITY = "formality"
    WARMTH = "warmth"
    VERBOSITY = "verbosity"
    SPEAKING_STYLE = "speaking_style"
    BEHAVIOURAL_BOUNDARIES = "behavioural_boundaries"
    USER_PREFERENCES = "user_preferences"
    BACKSTORY = "backstory"


class PersonaRuleUpdateAction(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    ENABLE = "enable"
    DISABLE = "disable"
    REMOVE = "remove"


class PersonaUpdateStatus(StrEnum):
    APPLIED = "applied"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"


class PersonaFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field: PersonaField
    value: PersonaFieldValue


class PersonaRuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: PersonaRuleUpdateAction
    rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    rule: PersonaRule | None = None

    @model_validator(mode="after")
    def validate_rule_change(self) -> "PersonaRuleChange":
        if self.action in {
            PersonaRuleUpdateAction.ADD,
            PersonaRuleUpdateAction.REPLACE,
        }:
            if self.rule is None:
                message = f"{self.action.value} rule changes require a rule"
                raise ValueError(message)
            return self

        if self.rule_id is None:
            message = f"{self.action.value} rule changes require rule_id"
            raise ValueError(message)
        if self.rule is not None:
            message = f"{self.action.value} rule changes must not include rule"
            raise ValueError(message)
        return self


class PersonaUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    source: PersonaUpdateSource
    field_changes: tuple[PersonaFieldChange, ...] = Field(default_factory=tuple)
    rule_changes: tuple[PersonaRuleChange, ...] = Field(default_factory=tuple)
    confirmed: bool = False
    confirmation_token: str | None = Field(default=None, min_length=1, max_length=256)
    reason: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def validate_has_changes(self) -> "PersonaUpdateRequest":
        if not self.field_changes and not self.rule_changes:
            message = "persona update request must include at least one change"
            raise ValueError(message)
        return self


class PersonaUpdateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: PersonaUpdateStatus
    soul: AgentSoul
    applied_changes: tuple[str, ...] = Field(default_factory=tuple)
    pending_changes: tuple[str, ...] = Field(default_factory=tuple)
    blocked_changes: tuple[str, ...] = Field(default_factory=tuple)

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from voxos.agent_identity.models import DEFAULT_AGENT_DISPLAY_NAME
from voxos.persona.rules import PersonaRule


class PersonaTone(StrEnum):
    HELPFUL = "helpful"
    DIRECT = "direct"
    CALM = "calm"
    ENCOURAGING = "encouraging"
    TECHNICAL = "technical"


class HumourStyle(StrEnum):
    NONE = "none"
    DRY = "dry"
    LIGHT = "light"
    WITTY = "witty"


class FormalityLevel(StrEnum):
    CASUAL = "casual"
    NEUTRAL = "neutral"
    FORMAL = "formal"


class WarmthLevel(StrEnum):
    RESERVED = "reserved"
    BALANCED = "balanced"
    WARM = "warm"


class VerbosityLevel(StrEnum):
    CONCISE = "concise"
    BALANCED = "balanced"
    DETAILED = "detailed"


def utc_now() -> datetime:
    return datetime.now(UTC)


class AgentPersona(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        default=DEFAULT_AGENT_DISPLAY_NAME,
        min_length=1,
        max_length=128,
    )
    role: str = Field(default="voice assistant", min_length=1, max_length=256)
    tone: PersonaTone = PersonaTone.HELPFUL
    humour_style: HumourStyle = HumourStyle.NONE
    formality: FormalityLevel = FormalityLevel.NEUTRAL
    warmth: WarmthLevel = WarmthLevel.BALANCED
    verbosity: VerbosityLevel = VerbosityLevel.CONCISE
    speaking_style: tuple[str, ...] = Field(
        default=("clear", "plain-spoken"),
        min_length=1,
    )
    behavioural_boundaries: tuple[str, ...] = Field(default_factory=tuple)
    user_preferences: tuple[str, ...] = Field(default_factory=tuple)
    backstory: str | None = Field(default=None, min_length=1, max_length=2048)
    persistent_rules: tuple[PersonaRule, ...] = Field(default_factory=tuple)

    @field_validator("name", "role", "backstory", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "speaking_style",
        "behavioural_boundaries",
        "user_preferences",
        mode="before",
    )
    @classmethod
    def strip_text_tuple(cls, value: object) -> object:
        if isinstance(value, str):
            return (value.strip(),)
        if isinstance(value, tuple | list):
            return tuple(item.strip() if isinstance(item, str) else item for item in value)
        return value

    @field_validator(
        "speaking_style",
        "behavioural_boundaries",
        "user_preferences",
    )
    @classmethod
    def validate_text_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value):
            message = "text tuple values must not be empty"
            raise ValueError(message)
        return value


class AgentSoul(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    persona: AgentPersona = Field(default_factory=AgentPersona)
    rules: tuple[PersonaRule, ...] = Field(default_factory=tuple)
    version: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("user_id", mode="before")
    @classmethod
    def strip_user_id(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

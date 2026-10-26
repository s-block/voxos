from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillScope(StrEnum):
    DEVELOPER = "developer"
    USER = "user"


class SkillStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    PROPOSED = "proposed"
    REJECTED = "rejected"


class SkillCategory(StrEnum):
    GENERAL = "general"
    VOICE = "voice"
    MEMORY = "memory"
    PLANNING = "planning"
    TOOL_USE = "tool_use"


class SkillCapabilityPreference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability: str = Field(min_length=1, max_length=128)
    tool_names: tuple[str, ...] = Field(default_factory=tuple, max_length=16)

    @field_validator("capability", mode="before")
    @classmethod
    def strip_capability(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("tool_names", mode="before")
    @classmethod
    def normalize_tool_names(cls, value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        if not all(isinstance(tool_name, str) for tool_name in value):
            return value
        normalized = tuple(dict.fromkeys(tool_name.strip() for tool_name in value))
        if any(not tool_name for tool_name in normalized):
            message = "preferred capability tool names must not be empty"
            raise ValueError(message)
        return normalized


class SkillSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    scope: SkillScope
    owner_user_id: str | None = Field(default=None, min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=128)
    category: SkillCategory = SkillCategory.GENERAL
    status: SkillStatus
    description: str = Field(min_length=1, max_length=512)
    preferred_capabilities: tuple[SkillCapabilityPreference, ...] = Field(
        default_factory=tuple,
        max_length=8,
    )


class SkillDocument(SkillSummary):
    body_markdown: str = Field(min_length=1, max_length=12000)
    created_by_reflection_id: str | None = Field(default=None, min_length=1, max_length=36)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    slug: str | None = Field(default=None, min_length=1, max_length=128)
    category: SkillCategory = SkillCategory.GENERAL
    description: str = Field(min_length=1, max_length=512)
    body_markdown: str = Field(min_length=1, max_length=12000)
    preferred_capabilities: tuple[SkillCapabilityPreference, ...] = Field(
        default_factory=tuple,
        max_length=8,
    )
    created_by_reflection_id: str | None = Field(default=None, min_length=1, max_length=36)
    confirmed: bool = True

    @field_validator("name", "slug", "description", "body_markdown", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class SkillUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=128)
    category: SkillCategory | None = None
    description: str | None = Field(default=None, min_length=1, max_length=512)
    body_markdown: str | None = Field(default=None, min_length=1, max_length=12000)
    preferred_capabilities: tuple[SkillCapabilityPreference, ...] | None = Field(
        default=None,
        max_length=8,
    )
    confirmed: bool = True

    @field_validator("name", "description", "body_markdown", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class SkillRenderContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str | None = Field(default=None, min_length=1, max_length=512)
    max_summaries: int = Field(default=8, ge=1, le=20)
    include_full_body_slugs: tuple[str, ...] = Field(default_factory=tuple)

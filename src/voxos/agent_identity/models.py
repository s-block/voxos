from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

DEFAULT_AGENT_SLUG = "default"
DEFAULT_AGENT_DISPLAY_NAME = "Voxos"


class AgentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    owner_user_id: str = Field(min_length=1, max_length=256)
    slug: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=2048)
    status: AgentStatus
    is_default: bool
    created_at: datetime
    updated_at: datetime


class AgentScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    session_id: str | None = Field(default=None, min_length=1, max_length=36)
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    channel: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("slug", "display_name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class AgentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("display_name", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value


class AgentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agents: tuple[AgentIdentity, ...] = Field(default_factory=tuple)

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AgentWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    user_id: str = Field(min_length=1, max_length=256)
    agent_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=8000)
    enabled: bool
    created_at: datetime
    updated_at: datetime


class AgentWebhookCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=8000)

    @field_validator("agent_id", "name", "instruction")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            message = "Webhook fields must not be blank"
            raise ValueError(message)
        return stripped


class AgentWebhookUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = Field(default=None, min_length=1, max_length=256)
    instruction: str | None = Field(default=None, min_length=1, max_length=8000)
    enabled: bool | None = None

    @field_validator("name", "instruction")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            message = "Webhook fields must not be blank"
            raise ValueError(message)
        return stripped

    @model_validator(mode="after")
    def require_update(self) -> "AgentWebhookUpdateRequest":
        if not self.model_fields_set:
            message = "At least one webhook field must be supplied"
            raise ValueError(message)
        return self


class AgentWebhookView(AgentWebhook):
    trigger_path: str = Field(min_length=1, max_length=512)
    trigger_url: str | None = Field(default=None, min_length=1, max_length=2560)


class AgentWebhookCredential(AgentWebhookView):
    token: str = Field(min_length=1, max_length=512)


class AgentWebhookDeliveryAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    webhook_id: str = Field(min_length=1, max_length=36)
    run_id: str = Field(min_length=1, max_length=36)
    status: Literal["accepted"] = "accepted"

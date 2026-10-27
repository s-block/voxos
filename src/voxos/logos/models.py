import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?!-)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
)


class ActivityIcon(StrEnum):
    MCP = "mcp"
    WEB = "web"


class BrandLogo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str = Field(min_length=1, max_length=253)
    label: str = Field(min_length=1, max_length=128)
    source: Literal["domain"] = "domain"

    @field_validator("domain", mode="before")
    @classmethod
    def normalize_domain(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower().removeprefix("www.")
        return value

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        if HOSTNAME_PATTERN.fullmatch(value) is None:
            message = "domain must be a valid hostname"
            raise ValueError(message)
        return value

    @field_validator("label", mode="before")
    @classmethod
    def strip_label(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

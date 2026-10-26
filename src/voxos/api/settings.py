from typing import ClassVar

import orjson
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    default_cors_origins: ClassVar[tuple[str, ...]] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )

    api_cors_allow_origins: tuple[str, ...] = Field(
        default=default_cors_origins,
        alias="API_CORS_ALLOW_ORIGINS",
    )
    api_public_base_url: str | None = Field(
        default=None,
        alias="API_PUBLIC_BASE_URL",
        min_length=1,
        max_length=2048,
    )
    deployment_revision: str | None = Field(
        default=None,
        alias="DEPLOYMENT_REVISION",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    api_docs_enabled: bool = Field(default=True, alias="API_DOCS_ENABLED")
    api_rate_limit_per_minute: int = Field(default=60, ge=1, le=6000, alias="API_RATE_LIMIT_PER_MINUTE")
    api_rate_limit_burst: int = Field(default=20, ge=1, le=1000, alias="API_RATE_LIMIT_BURST")
    api_max_in_flight: int = Field(default=64, ge=1, le=1000, alias="API_MAX_IN_FLIGHT")
    api_max_user_in_flight: int = Field(default=4, ge=1, le=100, alias="API_MAX_USER_IN_FLIGHT")

    @field_validator("api_cors_allow_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if stripped == "":
            return ()

        if stripped.startswith("["):
            parsed = orjson.loads(stripped)
            if isinstance(parsed, list) and all(isinstance(origin, str) for origin in parsed):
                return tuple(parsed)
            return parsed

        return tuple(origin.strip() for origin in stripped.split(",") if origin.strip() != "")

    @field_validator("api_public_base_url", mode="before")
    @classmethod
    def strip_public_base_url(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip().rstrip("/")
            return stripped or None
        return value

    @field_validator("api_public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            message = "API_PUBLIC_BASE_URL must start with https:// for public callbacks"
            raise ValueError(message)
        return value

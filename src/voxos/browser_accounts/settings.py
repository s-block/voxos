from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BrowserAccountSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(
        default=True,
        validation_alias="BROWSER_ACCOUNTS_ENABLED",
    )
    setup_ttl_seconds: int = Field(
        default=900,
        ge=300,
        le=3600,
        validation_alias="BROWSER_ACCOUNTS_SETUP_TTL_SECONDS",
    )
    authorization_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=1800,
        validation_alias="BROWSER_ACCOUNTS_AUTHORIZATION_TTL_SECONDS",
    )
    task_timeout_seconds: int = Field(
        default=600,
        ge=30,
        le=900,
        validation_alias="BROWSER_ACCOUNTS_TASK_TIMEOUT_SECONDS",
    )
    cleanup_timeout_seconds: int = Field(
        default=15,
        ge=5,
        le=60,
        validation_alias="BROWSER_ACCOUNTS_CLEANUP_TIMEOUT_SECONDS",
    )
    task_lease_seconds: int = Field(
        default=720,
        ge=60,
        le=1200,
        validation_alias="BROWSER_ACCOUNTS_TASK_LEASE_SECONDS",
    )
    max_task_steps: int = Field(
        default=40,
        ge=1,
        le=100,
        validation_alias="BROWSER_ACCOUNTS_MAX_TASK_STEPS",
    )
    max_output_characters: int = Field(
        default=12_000,
        ge=1000,
        le=50_000,
        validation_alias="BROWSER_ACCOUNTS_MAX_OUTPUT_CHARACTERS",
    )
    poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.25,
        le=5.0,
        validation_alias="BROWSER_ACCOUNTS_POLL_INTERVAL_SECONDS",
    )
    browserless_base_url: str = Field(
        default="https://production-lon.browserless.io",
        validation_alias="BROWSER_ACCOUNTS_BROWSERLESS_BASE_URL",
    )
    stagehand_base_url: str = Field(
        default="http://127.0.0.1:3100",
        validation_alias="BROWSER_ACCOUNTS_STAGEHAND_BASE_URL",
    )
    stagehand_service_token: SecretStr | None = Field(
        default=None,
        min_length=32,
        max_length=512,
        validation_alias="BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN",
    )

    @field_validator("stagehand_service_token")
    @classmethod
    def validate_stagehand_service_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(not 33 <= ord(character) <= 126 for character in value.get_secret_value()):
            message = "BROWSER_ACCOUNTS_STAGEHAND_SERVICE_TOKEN must contain printable non-space characters"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def validate_task_lease_window(self) -> "BrowserAccountSettings":
        minimum_lease_seconds = (
            self.task_timeout_seconds + self.cleanup_timeout_seconds + (2 * self.poll_interval_seconds)
        )
        if self.task_lease_seconds <= minimum_lease_seconds:
            message = "task lease must exceed the task timeout, cleanup timeout, and two polling intervals"
            raise ValueError(message)
        return self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PushNotificationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    enabled: bool = Field(default=False, alias="PUSH_NOTIFICATIONS_ENABLED")
    claim_batch_size: int = Field(
        default=20,
        ge=1,
        le=100,
        alias="PUSH_NOTIFICATION_CLAIM_BATCH_SIZE",
    )
    lease_seconds: int = Field(
        default=60,
        ge=10,
        le=600,
        alias="PUSH_NOTIFICATION_LEASE_SECONDS",
    )
    max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        alias="PUSH_NOTIFICATION_MAX_ATTEMPTS",
    )
    retry_base_seconds: int = Field(
        default=30,
        ge=1,
        le=3600,
        alias="PUSH_NOTIFICATION_RETRY_BASE_SECONDS",
    )

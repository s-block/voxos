from enum import StrEnum
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AttachmentStorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class AttachmentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    backend: AttachmentStorageBackend = Field(
        default=AttachmentStorageBackend.LOCAL,
        alias="ATTACHMENT_STORAGE_BACKEND",
    )
    image_input_enabled: bool = Field(
        default=True,
        alias="CHAT_IMAGE_INPUT_ENABLED",
    )
    local_path: Path = Field(
        default=Path(".voxos/attachments"),
        alias="ATTACHMENT_LOCAL_PATH",
    )
    s3_bucket: str | None = Field(
        default=None,
        alias="ATTACHMENT_S3_BUCKET",
        min_length=3,
        max_length=63,
    )
    s3_prefix: str = Field(
        default="chat-images",
        alias="ATTACHMENT_S3_PREFIX",
        min_length=1,
        max_length=256,
    )
    s3_region: str | None = Field(
        default=None,
        alias="ATTACHMENT_S3_REGION",
        min_length=1,
        max_length=64,
    )
    max_source_bytes: int = Field(
        default=25 * 1024 * 1024,
        alias="ATTACHMENT_MAX_SOURCE_BYTES",
        ge=1024,
        le=100 * 1024 * 1024,
    )
    max_image_bytes: int = Field(
        default=5 * 1024 * 1024,
        alias="ATTACHMENT_MAX_IMAGE_BYTES",
        ge=1024,
        le=20 * 1024 * 1024,
    )
    max_turn_bytes: int = Field(
        default=20 * 1024 * 1024,
        alias="ATTACHMENT_MAX_TURN_BYTES",
        ge=1024,
        le=80 * 1024 * 1024,
    )
    max_images_per_turn: int = Field(
        default=4,
        alias="ATTACHMENT_MAX_IMAGES_PER_TURN",
        ge=1,
        le=16,
    )
    max_pixels: int = Field(
        default=20_000_000,
        alias="ATTACHMENT_MAX_PIXELS",
        ge=1_000_000,
        le=100_000_000,
    )
    max_dimension: int = Field(
        default=4096,
        alias="ATTACHMENT_MAX_DIMENSION",
        ge=512,
        le=8192,
    )
    thumbnail_dimension: int = Field(
        default=512,
        alias="ATTACHMENT_THUMBNAIL_DIMENSION",
        ge=64,
        le=2048,
    )
    staging_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        alias="ATTACHMENT_STAGING_TTL_SECONDS",
        ge=300,
        le=7 * 24 * 60 * 60,
    )
    max_deletion_attempts: int = Field(
        default=5,
        alias="ATTACHMENT_MAX_DELETION_ATTEMPTS",
        ge=1,
        le=20,
    )
    deletion_retry_interval_seconds: float = Field(
        default=60.0,
        alias="ATTACHMENT_DELETION_RETRY_INTERVAL_SECONDS",
        ge=1.0,
        le=3600.0,
    )
    max_history_images: int = Field(
        default=12,
        alias="ATTACHMENT_MAX_HISTORY_IMAGES",
        ge=1,
        le=64,
    )
    max_history_bytes: int = Field(
        default=40 * 1024 * 1024,
        alias="ATTACHMENT_MAX_HISTORY_BYTES",
        ge=1024,
        le=256 * 1024 * 1024,
    )

    @field_validator("s3_bucket", "s3_region", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("s3_prefix", mode="before")
    @classmethod
    def normalize_prefix(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().strip("/")
        return value

    @model_validator(mode="after")
    def validate_backend(self) -> "AttachmentSettings":
        if self.backend == AttachmentStorageBackend.S3 and self.s3_bucket is None:
            message = "ATTACHMENT_S3_BUCKET is required for the s3 attachment backend"
            raise ValueError(message)
        if self.max_turn_bytes < self.max_image_bytes:
            message = "ATTACHMENT_MAX_TURN_BYTES must be at least ATTACHMENT_MAX_IMAGE_BYTES"
            raise ValueError(message)
        if self.max_history_images < self.max_images_per_turn:
            message = "ATTACHMENT_MAX_HISTORY_IMAGES must be at least ATTACHMENT_MAX_IMAGES_PER_TURN"
            raise ValueError(message)
        if self.max_history_bytes < self.max_turn_bytes:
            message = "ATTACHMENT_MAX_HISTORY_BYTES must be at least ATTACHMENT_MAX_TURN_BYTES"
            raise ValueError(message)
        return self

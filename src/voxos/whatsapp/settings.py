from typing import Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class WhatsAppWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    whatsapp_enabled: bool = Field(default=True, alias="WHATSAPP_ENABLED")
    whatsapp_bridge_executable: str = Field(
        default="node",
        min_length=1,
        alias="WHATSAPP_BRIDGE_EXECUTABLE",
    )
    whatsapp_bridge_script: str = Field(
        default="/app/whatsapp-bridge/dist/index.js",
        min_length=1,
        alias="WHATSAPP_BRIDGE_SCRIPT",
    )
    whatsapp_worker_poll_interval_seconds: float = Field(
        default=0.5,
        gt=0,
        alias="WHATSAPP_WORKER_POLL_INTERVAL_SECONDS",
    )
    whatsapp_worker_lease_ttl_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="WHATSAPP_WORKER_LEASE_TTL_SECONDS",
    )
    whatsapp_worker_shutdown_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="WHATSAPP_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
    )
    whatsapp_bridge_start_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="WHATSAPP_BRIDGE_START_TIMEOUT_SECONDS",
    )
    whatsapp_bridge_request_timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        le=60.0,
        alias="WHATSAPP_BRIDGE_REQUEST_TIMEOUT_SECONDS",
    )
    whatsapp_bridge_restart_grace_seconds: float = Field(
        default=5.0,
        ge=0,
        alias="WHATSAPP_BRIDGE_RESTART_GRACE_SECONDS",
    )
    whatsapp_max_sessions_per_worker: int = Field(
        default=25,
        gt=0,
        alias="WHATSAPP_MAX_SESSIONS_PER_WORKER",
    )
    whatsapp_challenge_ttl_seconds: int = Field(
        default=120,
        gt=0,
        alias="WHATSAPP_CHALLENGE_TTL_SECONDS",
    )
    whatsapp_api_challenge_wait_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="WHATSAPP_API_CHALLENGE_WAIT_SECONDS",
    )
    whatsapp_ipc_max_frame_bytes: int = Field(
        default=1_048_576,
        ge=16_384,
        alias="WHATSAPP_IPC_MAX_FRAME_BYTES",
    )
    whatsapp_health_path: str = Field(
        default="/var/run/voxos/whatsapp-worker-health.json",
        min_length=1,
        alias="WHATSAPP_HEALTH_PATH",
    )
    whatsapp_health_freshness_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="WHATSAPP_HEALTH_FRESHNESS_SECONDS",
    )
    whatsapp_worker_restart_after_seconds: float = Field(
        default=60.0,
        gt=0,
        alias="WHATSAPP_WORKER_RESTART_AFTER_SECONDS",
    )

    @model_validator(mode="after")
    def validate_worker_lifecycle(self) -> Self:
        if self.whatsapp_worker_lease_ttl_seconds <= (
            max(
                self.whatsapp_worker_shutdown_timeout_seconds,
                self.whatsapp_bridge_request_timeout_seconds,
            )
            + self.whatsapp_worker_poll_interval_seconds * 2
        ):
            message = (
                "WHATSAPP_WORKER_LEASE_TTL_SECONDS must exceed the bridge "
                "request and shutdown timeouts plus two poll intervals"
            )
            raise ValueError(message)
        if self.whatsapp_bridge_restart_grace_seconds >= self.whatsapp_worker_lease_ttl_seconds:
            message = "WHATSAPP_BRIDGE_RESTART_GRACE_SECONDS must be shorter than WHATSAPP_WORKER_LEASE_TTL_SECONDS"
            raise ValueError(message)
        if self.whatsapp_health_freshness_seconds <= self.whatsapp_worker_poll_interval_seconds:
            message = "WHATSAPP_HEALTH_FRESHNESS_SECONDS must exceed WHATSAPP_WORKER_POLL_INTERVAL_SECONDS"
            raise ValueError(message)
        if self.whatsapp_worker_restart_after_seconds <= max(
            self.whatsapp_health_freshness_seconds,
            self.whatsapp_bridge_request_timeout_seconds,
        ):
            message = (
                "WHATSAPP_WORKER_RESTART_AFTER_SECONDS must exceed the health freshness and bridge request timeouts"
            )
            raise ValueError(message)
        return self

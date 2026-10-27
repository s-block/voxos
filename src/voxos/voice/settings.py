from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from voxos.voice.endpoint_policy import LiveKitEndpointPolicy


class VoiceAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    voice_agent_name: str = Field(default="voxos-voice-agent", alias="VOICE_AGENT_NAME")
    voice_user_metadata_key: str = Field(
        default="user_id",
        alias="VOICE_USER_METADATA_KEY",
    )
    voice_token_ttl_seconds: int = Field(
        default=900,
        gt=0,
        alias="VOICE_TOKEN_TTL_SECONDS",
    )
    voice_route_signing_secret: SecretStr | None = Field(
        default=None,
        alias="VOICE_ROUTE_SIGNING_SECRET",
    )
    voice_route_metadata_key: str = Field(
        default="voxos_route",
        min_length=1,
        max_length=128,
        alias="VOICE_ROUTE_METADATA_KEY",
    )
    voice_route_clock_skew_seconds: int = Field(
        default=60,
        ge=0,
        le=300,
        alias="VOICE_ROUTE_CLOCK_SKEW_SECONDS",
    )
    voice_inactivity_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        alias="VOICE_INACTIVITY_TIMEOUT_SECONDS",
    )
    voice_delete_room_on_close: bool = Field(
        default=True,
        alias="VOICE_DELETE_ROOM_ON_CLOSE",
    )
    voice_worker_poll_interval_seconds: float = Field(
        default=0.25,
        gt=0,
        alias="VOICE_WORKER_POLL_INTERVAL_SECONDS",
    )
    voice_worker_start_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="VOICE_WORKER_START_TIMEOUT_SECONDS",
    )
    voice_agent_ready_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        alias="VOICE_AGENT_READY_TIMEOUT_SECONDS",
    )
    voice_outbound_answer_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        alias="VOICE_OUTBOUND_ANSWER_TIMEOUT_SECONDS",
    )
    voice_outbound_opening_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        alias="VOICE_OUTBOUND_OPENING_TIMEOUT_SECONDS",
    )
    voice_outbound_opening_retry_attempts: int = Field(
        default=2,
        ge=1,
        le=5,
        alias="VOICE_OUTBOUND_OPENING_RETRY_ATTEMPTS",
    )
    voice_worker_settings_resolution_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS",
    )
    voice_worker_start_concurrency: int = Field(
        default=8,
        gt=0,
        alias="VOICE_WORKER_START_CONCURRENCY",
    )
    voice_worker_lease_renewal_concurrency: int = Field(
        default=16,
        gt=0,
        alias="VOICE_WORKER_LEASE_RENEWAL_CONCURRENCY",
    )
    voice_worker_demand_ttl_seconds: int = Field(
        default=60,
        gt=0,
        alias="VOICE_WORKER_DEMAND_TTL_SECONDS",
    )
    voice_worker_idle_ttl_seconds: float = Field(
        default=60.0,
        ge=0,
        alias="VOICE_WORKER_IDLE_TTL_SECONDS",
    )
    voice_worker_idle_processes: int = Field(
        default=0,
        ge=0,
        le=4,
        alias="VOICE_WORKER_IDLE_PROCESSES",
    )
    voice_warm_user_workers: bool = Field(
        default=True,
        alias="VOICE_WARM_USER_WORKERS",
    )
    voice_worker_lease_ttl_seconds: float = Field(
        default=15.0,
        gt=0,
        alias="VOICE_WORKER_LEASE_TTL_SECONDS",
    )
    voice_worker_shutdown_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        alias="VOICE_WORKER_SHUTDOWN_TIMEOUT_SECONDS",
    )
    voice_worker_max_active_projects: int = Field(
        default=100,
        gt=0,
        alias="VOICE_WORKER_MAX_ACTIVE_PROJECTS",
    )
    voice_worker_max_active_projects_per_supervisor: int = Field(
        default=16,
        gt=0,
        alias="VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_SUPERVISOR",
    )
    voice_worker_max_active_projects_per_tenant: int = Field(
        default=2,
        gt=0,
        alias="VOICE_WORKER_MAX_ACTIVE_PROJECTS_PER_TENANT",
    )
    voice_worker_max_calls_per_project: int = Field(
        default=32,
        gt=0,
        alias="VOICE_WORKER_MAX_CALLS_PER_PROJECT",
    )
    voice_worker_demand_retention_seconds: int = Field(
        default=86_400,
        gt=0,
        alias="VOICE_WORKER_DEMAND_RETENTION_SECONDS",
    )
    voice_worker_maintenance_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        alias="VOICE_WORKER_MAINTENANCE_INTERVAL_SECONDS",
    )
    voice_livekit_endpoint_policy: LiveKitEndpointPolicy = Field(
        default=LiveKitEndpointPolicy.PUBLIC_WSS,
        alias="VOICE_LIVEKIT_ENDPOINT_POLICY",
    )

    @field_validator("voice_route_signing_secret")
    @classmethod
    def validate_voice_route_signing_secret(
        cls,
        value: SecretStr | None,
    ) -> SecretStr | None:
        if value is None:
            return None
        stripped = value.get_secret_value().strip()
        if len(stripped) < 32:
            message = "VOICE_ROUTE_SIGNING_SECRET must contain at least 32 characters"
            raise ValueError(message)
        return SecretStr(stripped)

    @model_validator(mode="after")
    def validate_worker_lifecycle(self) -> Self:
        validate_voice_worker_lifecycle_settings(self)
        return self


def validate_voice_worker_lifecycle_settings(settings: VoiceAgentSettings) -> None:
    if settings.voice_worker_lease_ttl_seconds <= (settings.voice_worker_poll_interval_seconds * 2):
        message = "VOICE_WORKER_LEASE_TTL_SECONDS must exceed two poll intervals"
        raise ValueError(message)
    if settings.voice_worker_lease_ttl_seconds <= (
        settings.voice_worker_shutdown_timeout_seconds + settings.voice_worker_poll_interval_seconds * 2
    ):
        message = "VOICE_WORKER_LEASE_TTL_SECONDS must exceed shutdown timeout plus two poll intervals"
        raise ValueError(message)
    if (
        settings.voice_worker_settings_resolution_timeout_seconds + settings.voice_worker_poll_interval_seconds
        >= settings.voice_worker_start_timeout_seconds
    ):
        message = (
            "VOICE_WORKER_SETTINGS_RESOLUTION_TIMEOUT_SECONDS plus one poll "
            "interval must be shorter than VOICE_WORKER_START_TIMEOUT_SECONDS"
        )
        raise ValueError(message)
    if settings.voice_worker_demand_ttl_seconds <= (
        settings.voice_worker_start_timeout_seconds + settings.voice_worker_poll_interval_seconds
    ):
        message = "VOICE_WORKER_DEMAND_TTL_SECONDS must exceed worker start timeout plus one poll interval"
        raise ValueError(message)
    if settings.voice_worker_demand_retention_seconds < settings.voice_worker_idle_ttl_seconds:
        message = "VOICE_WORKER_DEMAND_RETENTION_SECONDS must be at least VOICE_WORKER_IDLE_TTL_SECONDS"
        raise ValueError(message)

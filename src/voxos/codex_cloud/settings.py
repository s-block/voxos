from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CodexCloudSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    enabled: bool = Field(default=True, alias="CODEX_CLOUD_ENABLED")
    docker_executable: str = Field(default="docker", min_length=1, alias="CODEX_CLOUD_DOCKER_EXECUTABLE")
    runner_image: str = Field(
        default="voxos-codex-runner:0.148.0",
        min_length=1,
        alias="CODEX_CLOUD_RUNNER_IMAGE",
    )
    worker_id: str = Field(default="local-codex-cloud-worker", alias="CODEX_CLOUD_WORKER_ID")
    max_concurrency: int = Field(default=2, ge=1, le=8, alias="CODEX_CLOUD_MAX_CONCURRENCY")
    claim_batch_size: int = Field(default=2, ge=1, le=8, alias="CODEX_CLOUD_CLAIM_BATCH_SIZE")
    poll_interval_seconds: float = Field(default=2.0, ge=0.25, le=60, alias="CODEX_CLOUD_POLL_INTERVAL_SECONDS")
    remote_poll_interval_seconds: float = Field(
        default=10.0,
        ge=2.0,
        le=300,
        alias="CODEX_CLOUD_REMOTE_POLL_INTERVAL_SECONDS",
    )
    login_timeout_seconds: int = Field(default=900, ge=300, le=1800, alias="CODEX_CLOUD_LOGIN_TIMEOUT_SECONDS")
    command_timeout_seconds: int = Field(
        default=120,
        ge=30,
        le=600,
        alias="CODEX_CLOUD_COMMAND_TIMEOUT_SECONDS",
    )
    task_tracking_timeout_seconds: int = Field(
        default=7200,
        ge=300,
        le=86400,
        alias="CODEX_CLOUD_TASK_TRACKING_TIMEOUT_SECONDS",
    )
    lease_timeout_seconds: int = Field(default=180, ge=60, le=900, alias="CODEX_CLOUD_LEASE_TIMEOUT_SECONDS")
    heartbeat_interval_seconds: float = Field(
        default=20.0,
        ge=5,
        le=120,
        alias="CODEX_CLOUD_HEARTBEAT_INTERVAL_SECONDS",
    )
    retry_delay_seconds: int = Field(default=30, ge=1, le=3600, alias="CODEX_CLOUD_RETRY_DELAY_SECONDS")
    shutdown_grace_seconds: float = Field(default=30, ge=1, le=300, alias="CODEX_CLOUD_SHUTDOWN_GRACE_SECONDS")
    memory_limit: str = Field(default="512m", pattern=r"^[1-9][0-9]*(?:[kKmMgG])?$", alias="CODEX_CLOUD_MEMORY_LIMIT")
    cpu_limit: float = Field(default=1.0, gt=0, le=8, alias="CODEX_CLOUD_CPU_LIMIT")
    pids_limit: int = Field(default=128, ge=32, le=1024, alias="CODEX_CLOUD_PIDS_LIMIT")
    max_output_bytes: int = Field(default=1_048_576, ge=65_536, le=10_485_760, alias="CODEX_CLOUD_MAX_OUTPUT_BYTES")

    @model_validator(mode="after")
    def validate_lease_window(self) -> "CodexCloudSettings":
        if self.lease_timeout_seconds <= self.heartbeat_interval_seconds * 2:
            message = "Codex Cloud lease timeout must exceed two heartbeat intervals"
            raise ValueError(message)
        if self.lease_timeout_seconds <= (self.command_timeout_seconds + (2 * self.poll_interval_seconds)):
            message = "Codex Cloud lease timeout must exceed the command timeout and two polling intervals"
            raise ValueError(message)
        return self

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentRunWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    worker_id: str = Field(default="local-run-worker", alias="AGENT_RUN_WORKER_ID")
    queues_csv: str = Field(default="agent-default", alias="AGENT_RUN_QUEUES")
    max_concurrency: int = Field(default=4, ge=1, le=64, alias="AGENT_RUN_MAX_CONCURRENCY")
    claim_batch_size: int = Field(default=4, ge=1, le=64, alias="AGENT_RUN_CLAIM_BATCH_SIZE")
    heartbeat_interval_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        alias="AGENT_RUN_HEARTBEAT_INTERVAL_SECONDS",
    )
    lease_timeout_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        alias="AGENT_RUN_LEASE_TIMEOUT_SECONDS",
    )
    poll_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        alias="AGENT_RUN_POLL_INTERVAL_SECONDS",
    )
    retry_delay_seconds: int = Field(
        default=30,
        ge=1,
        le=3600,
        alias="AGENT_RUN_RETRY_DELAY_SECONDS",
    )
    shutdown_grace_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=600.0,
        alias="AGENT_RUN_SHUTDOWN_GRACE_SECONDS",
    )

    @property
    def queues(self) -> tuple[str, ...]:
        return tuple(queue.strip() for queue in self.queues_csv.split(",") if queue.strip())


class AgentScheduleWorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    poll_interval_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=300.0,
        alias="AGENT_SCHEDULE_POLL_INTERVAL_SECONDS",
    )
    batch_size: int = Field(default=100, ge=1, le=500, alias="AGENT_SCHEDULE_BATCH_SIZE")
    queues_csv: str = Field(default="agent-default", alias="AGENT_RUN_QUEUES")

    @property
    def queues(self) -> tuple[str, ...]:
        return tuple(queue.strip() for queue in self.queues_csv.split(",") if queue.strip())

from enum import StrEnum
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseTlsMode(StrEnum):
    DRIVER_DEFAULT = "driver-default"
    VERIFY_FULL = "verify-full"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    db_host: str = Field(default="localhost", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(default="voxos", alias="DB_NAME")
    db_user: str = Field(default="voxos", alias="DB_USER")
    db_password: SecretStr | None = Field(default=None, alias="DB_PASSWORD")
    database_tls_mode: DatabaseTlsMode = Field(
        default=DatabaseTlsMode.DRIVER_DEFAULT,
        alias="DATABASE_TLS_MODE",
    )
    database_tls_ca_file: Path | None = Field(
        default=None,
        alias="DATABASE_TLS_CA_FILE",
    )

    def async_database_url(self) -> str:
        if self.database_url is not None:
            return normalize_async_database_url(self.database_url)

        password = ""
        if self.db_password is not None:
            encoded_password = quote(self.db_password.get_secret_value(), safe="")
            password = f":{encoded_password}"

        return (
            "postgresql+asyncpg://"
            f"{quote(self.db_user, safe='')}{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


def normalize_async_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url

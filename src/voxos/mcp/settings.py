from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    mcp_oauth_state_signing_secret: SecretStr | None = Field(
        default=None,
        alias="MCP_OAUTH_STATE_SIGNING_SECRET",
    )
    mcp_oauth_state_clock_skew_seconds: int = Field(
        default=60,
        ge=0,
        le=300,
        alias="MCP_OAUTH_STATE_CLOCK_SKEW_SECONDS",
    )
    mcp_credential_delegation_signing_secret: SecretStr | None = Field(
        default=None,
        alias="MCP_CREDENTIAL_DELEGATION_SIGNING_SECRET",
    )
    mcp_credential_delegation_ttl_seconds: int = Field(
        default=60,
        ge=15,
        le=300,
        alias="MCP_CREDENTIAL_DELEGATION_TTL_SECONDS",
    )
    mcp_credential_delegation_clock_skew_seconds: int = Field(
        default=15,
        ge=0,
        le=60,
        alias="MCP_CREDENTIAL_DELEGATION_CLOCK_SKEW_SECONDS",
    )
    mcp_tool_catalog_cache_ttl_seconds: int = Field(
        default=300,
        ge=1,
        le=3600,
        alias="MCP_TOOL_CATALOG_CACHE_TTL_SECONDS",
    )
    mcp_tool_catalog_cache_max_entries: int = Field(
        default=256,
        ge=1,
        le=4096,
        alias="MCP_TOOL_CATALOG_CACHE_MAX_ENTRIES",
    )

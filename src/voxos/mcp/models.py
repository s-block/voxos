import re
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from voxos.agent_runtime.models import JsonObject
from voxos.logos.models import ActivityIcon, BrandLogo
from voxos.mcp.url_policy import (
    normalize_host,
    normalized_hostname,
    validated_http_url,
    validated_mcp_outbound_url,
    validated_mcp_server_url_syntax,
)

HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
DEFAULT_OAUTH_CLIENT_NAME = "Voxos"
HEADER_NAME_ERROR = "header name contains invalid characters"
HEADER_NAME_AUTHORITY_ERROR = "header name is reserved for transport or infrastructure authority"
SCOPES_MUST_BE_STRINGS_ERROR = "scopes must be strings"
SCOPES_TYPE_ERROR = "scopes must be a string or list"
TOOL_NAMES_TYPE_ERROR = "tool names must be a string or list"
TOOL_NAMES_MUST_BE_STRINGS_ERROR = "tool names must be strings"
TRUSTED_PRIVATE_URL_REQUIRED_ERROR = "trusted private MCP variants require a URL"
TRUSTED_PRIVATE_HOST_MISMATCH_ERROR = "trusted private MCP host must match the variant URL"
OPTIONAL_AUTH_REQUIRES_NO_AUTH_ERROR = "optional MCP auth requires a no-auth primary connection"
OPTIONAL_AUTH_TYPE_ERROR = "optional MCP auth must use an API key header"
OPTIONAL_AUTH_CONFIGURATION_ERROR = "optional MCP API key auth requires a fixed header and secret"
FORBIDDEN_AUTHORITY_HEADER_NAMES = frozenset(
    {
        "connection",
        "content-length",
        "forwarded",
        "host",
        "metadata",
        "metadata-flavor",
        "proxy-authorization",
        "proxy-connection",
        "transfer-encoding",
        "x-google-metadata-request",
        "x-original-url",
        "x-rewrite-url",
    },
)
FORBIDDEN_AUTHORITY_HEADER_PREFIXES = ("proxy-", "x-forwarded-")


class McpTransportType(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"


class McpAuthType(StrEnum):
    NONE = "none"
    STATIC_BEARER = "static_bearer"
    API_KEY_HEADER = "api_key_header"
    OAUTH_AUTHORIZATION_CODE = "oauth_authorization_code"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"
    PRIVATE_KEY_JWT = "private_key_jwt"


class McpServerStatus(StrEnum):
    DISABLED = "disabled"
    UNTESTED = "untested"
    NEEDS_AUTH = "needs_auth"
    CONNECTED = "connected"
    FAILED = "failed"


class McpTrustState(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class McpSecretKind(StrEnum):
    STATIC_BEARER = "static_bearer"
    API_KEY = "api_key"
    OAUTH_TOKEN = "oauth_token"
    OAUTH_CLIENT_INFO = "oauth_client_info"
    PRIVATE_KEY = "private_key"


class McpPresetCategory(StrEnum):
    MCP_GATEWAYS = "mcp_gateways"
    TRAVEL_LIFESTYLE = "travel_lifestyle"
    PAYMENTS_COMMERCE = "payments_commerce"
    PRODUCTIVITY_COLLABORATION = "productivity_collaboration"
    WEB_SEARCH_RESEARCH = "web_search_research"
    BROWSER_AUTOMATION = "browser_automation"
    SCRAPING_ACTOR_PLATFORMS = "scraping_actor_platforms"
    DEVELOPER_OPERATIONS = "developer_operations"
    BUSINESS_SYSTEMS = "business_systems"
    KNOWLEDGE_DOCUMENTS = "knowledge_documents"
    DATA_ANALYTICS = "data_analytics"
    SMART_HOME_ENVIRONMENT = "smart_home_environment"


class McpPresetCompatibility(StrEnum):
    DIRECT_REMOTE = "direct_remote"
    CUSTOM_REMOTE = "custom_remote"
    GATEWAY_REQUIRED = "gateway_required"


class McpPresetToolRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class McpTokenEndpointAuthMethod(StrEnum):
    NONE = "none"
    CLIENT_SECRET_BASIC = "client_secret_basic"
    CLIENT_SECRET_POST = "client_secret_post"
    PRIVATE_KEY_JWT = "private_key_jwt"


class McpServerAuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_key_header_name: str | None = Field(default=None, min_length=1, max_length=128)
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    authorization_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    token_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    registration_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    token_endpoint_auth_method: McpTokenEndpointAuthMethod | None = None

    @field_validator("api_key_header_name")
    @classmethod
    def validate_header_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped_value = value.strip()
        if not HEADER_NAME_PATTERN.fullmatch(stripped_value):
            raise ValueError(HEADER_NAME_ERROR)
        normalized = stripped_value.lower()
        if normalized in FORBIDDEN_AUTHORITY_HEADER_NAMES or normalized.startswith(
            FORBIDDEN_AUTHORITY_HEADER_PREFIXES,
        ):
            raise ValueError(HEADER_NAME_AUTHORITY_ERROR)
        return stripped_value

    @field_validator(
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
    )
    @classmethod
    def validate_optional_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validated_mcp_outbound_url(value)

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return scopes_from_text(value)
        if isinstance(value, list | tuple):
            scopes: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError(SCOPES_MUST_BE_STRINGS_ERROR)
                stripped_item = item.strip()
                if stripped_item:
                    scopes.append(stripped_item)
            return tuple(dict.fromkeys(scopes))
        raise ValueError(SCOPES_TYPE_ERROR)


class McpOAuthClientInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=1)
    client_secret: str | None = Field(default=None, min_length=1, repr=False)
    token_endpoint: str = Field(min_length=1, max_length=2048)
    authorization_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    registration_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    token_endpoint_auth_method: McpTokenEndpointAuthMethod
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    resource: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator(
        "token_endpoint",
        "authorization_endpoint",
        "registration_endpoint",
        "resource",
    )
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validated_mcp_outbound_url(value)


class McpOAuthToken(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: str = Field(min_length=1, repr=False)
    token_type: str = Field(default="Bearer", min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1, repr=False)
    scope: str | None = Field(default=None)
    expires_at: datetime | None = None

    @property
    def scopes(self) -> tuple[str, ...]:
        if self.scope is None:
            return ()
        return scopes_from_text(self.scope)


class McpAuthSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authenticated: bool
    needs_reauth: bool
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = Field(default_factory=tuple)


class McpPresetAuthGuide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auth_type: McpAuthType
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=512)
    requires_secret: bool = False
    api_key_header_name: str | None = Field(default=None, min_length=1, max_length=128)
    oauth_scopes: tuple[str, ...] = Field(default_factory=tuple)
    requires_oauth_client: bool = False
    oauth_token_endpoint_auth_method: McpTokenEndpointAuthMethod | None = None
    requires_provider_oauth: bool = False
    provider_oauth_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    provider_oauth_description: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
    )

    @field_validator("api_key_header_name")
    @classmethod
    def validate_api_key_header_name(cls, value: str | None) -> str | None:
        return McpServerAuthConfig.validate_header_name(value)

    @field_validator("oauth_scopes", mode="before")
    @classmethod
    def normalize_oauth_scopes(cls, value: object) -> tuple[str, ...]:
        return McpServerAuthConfig.normalize_scopes(value)


class McpPresetToolGuide(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    label: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, min_length=1, max_length=512)
    risk: McpPresetToolRisk = McpPresetToolRisk.MEDIUM
    recommended: bool = False
    read_only: bool = False
    disabled_by_default: bool = False
    activity_label: str | None = Field(default=None, min_length=1, max_length=128)
    activity_icon: ActivityIcon | None = None
    route_action: str | None = Field(default=None, min_length=1, max_length=128)
    automatic_priority: int | None = Field(default=None, ge=0, le=10_000)

    @model_validator(mode="after")
    def validate_route_metadata(self) -> Self:
        if (self.route_action is None) != (self.automatic_priority is None):
            message = "route_action and automatic_priority must be configured together"
            raise ValueError(message)
        return self


class McpServicePresetVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    compatibility: McpPresetCompatibility
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    url_placeholder: str | None = Field(default=None, min_length=1, max_length=2048)
    trusted_private_host: str | None = Field(default=None, min_length=1, max_length=253)
    transport: McpTransportType = McpTransportType.STREAMABLE_HTTP
    auth: McpPresetAuthGuide
    optional_auth: McpPresetAuthGuide | None = None
    starter_allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
    risky_tools: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_url_policy(self) -> Self:
        if self.url is None:
            if self.trusted_private_host is not None:
                raise ValueError(TRUSTED_PRIVATE_URL_REQUIRED_ERROR)
            return self
        if self.trusted_private_host is None:
            validated_mcp_outbound_url(self.url)
            return self

        validated_mcp_server_url_syntax(self.url)
        if normalized_hostname(self.url) != normalize_host(self.trusted_private_host):
            raise ValueError(TRUSTED_PRIVATE_HOST_MISMATCH_ERROR)
        return self

    @model_validator(mode="after")
    def validate_optional_auth(self) -> Self:
        if self.optional_auth is None:
            return self
        if self.auth.auth_type != McpAuthType.NONE:
            raise ValueError(OPTIONAL_AUTH_REQUIRES_NO_AUTH_ERROR)
        if self.optional_auth.auth_type != McpAuthType.API_KEY_HEADER:
            raise ValueError(OPTIONAL_AUTH_TYPE_ERROR)
        if not self.optional_auth.requires_secret or self.optional_auth.api_key_header_name is None:
            raise ValueError(OPTIONAL_AUTH_CONFIGURATION_ERROR)
        return self

    @field_validator("starter_allowed_tools", "risky_tools", mode="before")
    @classmethod
    def normalize_tool_names(cls, value: object) -> tuple[str, ...]:
        return normalize_tool_names(value)


class McpServicePreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    category: McpPresetCategory
    label: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1024)
    cost_posture: str = Field(min_length=1, max_length=512)
    free_available: bool
    catalog_version: str = Field(min_length=1, max_length=32)
    researched_at: date
    variants: tuple[McpServicePresetVariant, ...] = Field(min_length=1)
    tool_guidance: tuple[McpPresetToolGuide, ...] = Field(default_factory=tuple)
    setup_steps: tuple[str, ...] = Field(default_factory=tuple)
    docs_urls: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    logo: BrandLogo


class McpPresetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_version: str
    researched_at: date
    presets: tuple[McpServicePreset, ...] = Field(default_factory=tuple)


class McpPresetServerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str | None = Field(default=None, min_length=1, max_length=256)
    variant_id: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool = False
    non_admin_tools_enabled: bool = False
    allowed_tools: tuple[str, ...] | None = None
    url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("label", "variant_id", "url", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validated_mcp_server_url_syntax(value)

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_allowed_tools(cls, value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        return normalize_tool_names(value)


class McpServerCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1, max_length=256)
    url: str = Field(min_length=1, max_length=2048)
    transport: McpTransportType = McpTransportType.STREAMABLE_HTTP
    auth_type: McpAuthType = McpAuthType.NONE
    enabled: bool = False
    non_admin_tools_enabled: bool = False
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
    api_key_header_name: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("label", "url", "api_key_header_name", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return validated_mcp_server_url_syntax(value)

    @field_validator("api_key_header_name")
    @classmethod
    def validate_api_key_header_name(cls, value: str | None) -> str | None:
        return McpServerAuthConfig.validate_header_name(value)

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_allowed_tools(cls, value: object) -> tuple[str, ...]:
        return normalize_tool_names(value)

    def auth_config(self) -> McpServerAuthConfig:
        return McpServerAuthConfig(api_key_header_name=self.api_key_header_name)


class McpServerUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str | None = Field(default=None, min_length=1, max_length=256)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    transport: McpTransportType | None = None
    enabled: bool | None = None
    non_admin_tools_enabled: bool | None = None
    allowed_tools: tuple[str, ...] | None = None
    api_key_header_name: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("label", "url", "api_key_header_name", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validated_mcp_server_url_syntax(value)

    @field_validator("api_key_header_name")
    @classmethod
    def validate_api_key_header_name(cls, value: str | None) -> str | None:
        return McpServerAuthConfig.validate_header_name(value)

    @field_validator("allowed_tools", mode="before")
    @classmethod
    def normalize_allowed_tools(cls, value: object) -> tuple[str, ...] | None:
        if value is None:
            return None
        return normalize_tool_names(value)


class McpStaticAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    secret: SecretStr = Field(min_length=1)
    api_key_header_name: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("api_key_header_name", mode="before")
    @classmethod
    def strip_header_name(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("api_key_header_name")
    @classmethod
    def validate_api_key_header_name(cls, value: str | None) -> str | None:
        return McpServerAuthConfig.validate_header_name(value)


class McpOAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    redirect_uri: str = Field(min_length=1, max_length=2048)
    scopes: tuple[str, ...] = Field(default_factory=tuple)
    client_name: str = Field(default=DEFAULT_OAUTH_CLIENT_NAME, min_length=1, max_length=128)
    client_id: str | None = Field(default=None, min_length=1, max_length=512)
    client_secret: SecretStr | None = None
    authorization_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    token_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    registration_endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    token_endpoint_auth_method: McpTokenEndpointAuthMethod | None = None

    @field_validator(
        "redirect_uri",
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
        mode="before",
    )
    @classmethod
    def strip_urls(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator(
        "redirect_uri",
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
    )
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validated_http_url(value)

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> tuple[str, ...]:
        return McpServerAuthConfig.normalize_scopes(value)


class McpOAuthStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_url: str
    expires_at: datetime


class McpClientCredentialsAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=1, max_length=512)
    client_secret: SecretStr = Field(min_length=1)
    token_endpoint: str = Field(min_length=1, max_length=2048)
    scopes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("token_endpoint", mode="before")
    @classmethod
    def strip_token_endpoint(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("token_endpoint")
    @classmethod
    def validate_token_endpoint(cls, value: str) -> str:
        return validated_mcp_outbound_url(value)

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> tuple[str, ...]:
        return McpServerAuthConfig.normalize_scopes(value)


class McpPrivateKeyJwtAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=1, max_length=512)
    private_key: SecretStr = Field(min_length=1)
    token_endpoint: str = Field(min_length=1, max_length=2048)
    algorithm: str = Field(default="RS256", min_length=1, max_length=32)
    scopes: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("token_endpoint", mode="before")
    @classmethod
    def strip_token_endpoint(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("token_endpoint")
    @classmethod
    def validate_token_endpoint(cls, value: str) -> str:
        return validated_mcp_outbound_url(value)

    @field_validator("scopes", mode="before")
    @classmethod
    def normalize_scopes(cls, value: object) -> tuple[str, ...]:
        return McpServerAuthConfig.normalize_scopes(value)


class McpCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: tuple[str, ...] = Field(default_factory=tuple)


class McpDiscoveredToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2048)
    input_schema: JsonObject = Field(default_factory=dict)
    guidance: McpPresetToolGuide | None = None


class McpServerSafeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str | None = Field(default=None, min_length=1, max_length=128)
    preset_variant_id: str | None = Field(default=None, min_length=1, max_length=128)
    preset_label: str | None = Field(default=None, min_length=1, max_length=128)
    catalog_version: str | None = Field(default=None, min_length=1, max_length=32)
    researched_at: date | None = None
    category: McpPresetCategory | None = None
    docs_urls: tuple[str, ...] = Field(default_factory=tuple)
    warnings: tuple[str, ...] = Field(default_factory=tuple)
    tool_guidance: tuple[McpPresetToolGuide, ...] = Field(default_factory=tuple)
    tool_definitions: tuple[McpDiscoveredToolDefinition, ...] = Field(default_factory=tuple)
    platform_default: bool = False
    default_allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
    logo: BrandLogo | None = None


class McpServerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    url: str
    transport: McpTransportType
    auth_type: McpAuthType
    enabled: bool
    non_admin_tools_enabled: bool
    trust_state: McpTrustState
    status: McpServerStatus
    status_message: str | None = None
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple)
    discovered_tools: tuple[str, ...] = Field(default_factory=tuple)
    safe_metadata: McpServerSafeMetadata = Field(default_factory=McpServerSafeMetadata)
    logo: BrandLogo | None = None
    auth: McpAuthSummary
    optional_auth: McpPresetAuthGuide | None = None
    optional_auth_configured: bool = False
    provider_auth: McpAuthSummary | None = None
    created_at: datetime
    updated_at: datetime


class McpServerListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    servers: tuple[McpServerResponse, ...] = Field(default_factory=tuple)


class McpOAuthCallbackResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    server_id: str
    status: McpServerStatus

    @classmethod
    def completed(cls, server_id: str) -> Self:
        return cls(server_id=server_id, status=McpServerStatus.UNTESTED)


def normalize_tool_names(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_values: tuple[object, ...] = tuple(value.split(","))
    elif isinstance(value, list | tuple):
        raw_values = tuple(value)
    else:
        raise ValueError(TOOL_NAMES_TYPE_ERROR)

    tools: list[str] = []
    for item in raw_values:
        if not isinstance(item, str):
            raise ValueError(TOOL_NAMES_MUST_BE_STRINGS_ERROR)
        stripped_item = item.strip()
        if stripped_item:
            tools.append(stripped_item)
    return tuple(dict.fromkeys(tools))


def scopes_from_text(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(scope for scope in value.split() if scope))

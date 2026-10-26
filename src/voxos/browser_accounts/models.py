from datetime import datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from voxos.browser_runtime.origins import normalize_browser_origin

DEFAULT_BROWSER_COUNTRY_CODE = "GB"
BROWSER_COUNTRY_CODE_PATTERN = r"^[A-Z]{2}$"


class BrowserAccountProvider(StrEnum):
    STAGEHAND_LOCAL = "stagehand_local"
    BROWSER_USE_CLOUD = "browser_use_cloud"
    BROWSERLESS = "browserless"


class BrowserAccountAuthenticationMode(StrEnum):
    MANAGED_PROFILE = "managed_profile"
    STORED_CREDENTIALS = "stored_credentials"


class BrowserAccountStatus(StrEnum):
    SETUP_REQUIRED = "setup_required"
    SETUP_IN_PROGRESS = "setup_in_progress"
    READY = "ready"
    EXPIRED = "expired"
    REVOKED = "revoked"
    DELETION_PENDING = "deletion_pending"
    ERROR = "error"


class BrowserAccountAuditEventKind(StrEnum):
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_UPDATED = "account_updated"
    SETUP_STARTED = "setup_started"
    SETUP_COMPLETED = "setup_completed"
    SETUP_CANCELLED = "setup_cancelled"
    APPROVAL_CREATED = "approval_created"
    APPROVAL_DENIED = "approval_denied"
    AUTHORITY_UNSEALED = "authority_unsealed"
    SESSION_STARTED = "session_started"
    SESSION_STOPPED = "session_stopped"
    PROVIDER_ERROR = "provider_error"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    REVOKED = "revoked"
    REMOTE_DELETED = "remote_deleted"
    DELETION_PENDING = "deletion_pending"
    RECONCILED = "reconciled"


class BrowserAccountLeaseKind(StrEnum):
    SETUP = "setup"
    TASK = "task"


class BrowserAccountErrorCategory(StrEnum):
    PROVIDER_AUTH = "provider_auth"
    PROVIDER_REQUEST_INVALID = "provider_request_invalid"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    PROFILE_MISSING = "profile_missing"
    SESSION_MISSING = "session_missing"
    SESSION_TIMEOUT = "session_timeout"
    SETUP_INCOMPLETE = "setup_incomplete"
    CLEANUP_FAILED = "cleanup_failed"
    UNKNOWN = "unknown"


class _BrowserAccountDetailsRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    label: str = Field(min_length=1, max_length=128)
    primary_origin: str = Field(min_length=1, max_length=512)
    authentication_origins: tuple[str, ...] = Field(
        default_factory=tuple,
        max_length=8,
    )
    browser_country_code: str = Field(
        default=DEFAULT_BROWSER_COUNTRY_CODE,
        min_length=2,
        max_length=2,
        pattern=BROWSER_COUNTRY_CODE_PATTERN,
    )

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("primary_origin", mode="before")
    @classmethod
    def normalize_primary_origin(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_browser_origin(value)
        return value

    @field_validator("authentication_origins", mode="before")
    @classmethod
    def normalize_authentication_origins(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(normalize_browser_origin(item) for item in value)
        return value

    @field_validator("browser_country_code", mode="before")
    @classmethod
    def normalize_browser_country_code(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def validate_origins(self) -> "_BrowserAccountDetailsRequest":
        if self.primary_origin in self.authentication_origins:
            message = "authentication_origins must not repeat primary_origin"
            raise ValueError(message)
        if len(set(self.authentication_origins)) != len(
            self.authentication_origins,
        ):
            message = "authentication_origins must be unique"
            raise ValueError(message)
        return self


class BrowserAccountCreateRequest(_BrowserAccountDetailsRequest):
    provider: BrowserAccountProvider | None = None
    provider_connection_id: str | None = Field(default=None, min_length=1, max_length=36)
    agent_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    username: SecretStr | None = Field(default=None, min_length=1, max_length=512)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("agent_ids")
    @classmethod
    def unique_agent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            message = "agent_ids must be unique"
            raise ValueError(message)
        return value

    @model_validator(mode="after")
    def validate_provider_connection(self) -> "BrowserAccountCreateRequest":
        if self.provider == BrowserAccountProvider.STAGEHAND_LOCAL and self.provider_connection_id is not None:
            message = "Local browser accounts must not include a provider connection"
            raise ValueError(message)
        if (
            self.provider in {BrowserAccountProvider.BROWSER_USE_CLOUD, BrowserAccountProvider.BROWSERLESS}
            and self.provider_connection_id is None
        ):
            message = "Hosted browser accounts require a provider connection"
            raise ValueError(message)
        return self


class BrowserAccountUpdateRequest(_BrowserAccountDetailsRequest):
    """Editable, non-secret Browser Account details."""


class BrowserAccountCredentialsRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    username: SecretStr = Field(min_length=1, max_length=512)
    password: SecretStr = Field(min_length=1, max_length=4096)


class BrowserAccountAgentAssignmentsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=32)

    @field_validator("agent_ids")
    @classmethod
    def unique_agent_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            message = "agent_ids must be unique"
            raise ValueError(message)
        return value


class BrowserAccountApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str = Field(min_length=1, max_length=36)
    chat_id: str = Field(min_length=1, max_length=36)
    client_turn_id: str = Field(min_length=1, max_length=64)
    task_summary: str = Field(min_length=1, max_length=512)

    @field_validator("task_summary", mode="before")
    @classmethod
    def normalize_task_summary(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(value.split())
        return value


class BrowserAccountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    label: str
    provider: BrowserAccountProvider
    provider_connection_id: str | None
    primary_origin: str
    authentication_origins: tuple[str, ...]
    browser_country_code: str
    authentication_mode: BrowserAccountAuthenticationMode
    status: BrowserAccountStatus
    error_category: BrowserAccountErrorCategory | None = None
    agent_ids: tuple[str, ...] = Field(default_factory=tuple)
    has_credentials: bool = False
    last_verified_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def approved_origins(self) -> tuple[str, ...]:
        return (self.primary_origin, *self.authentication_origins)


class BrowserAccountListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: tuple[BrowserAccountResponse, ...] = Field(default_factory=tuple)


class BrowserAccountSetupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account: BrowserAccountResponse
    live_url: str = Field(min_length=1, max_length=4096)
    expires_at: datetime


class BrowserAccountAuthorizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    account_id: str
    account_label: str
    provider: BrowserAccountProvider
    agent_id: str
    session_id: str
    approved_origins: tuple[str, ...]
    task_summary: str
    approved_at: datetime
    expires_at: datetime
    consumed_at: datetime | None = None

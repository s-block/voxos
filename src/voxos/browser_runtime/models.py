from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from voxos.browser_runtime.contracts import BrowserTaskActionMode
from voxos.browser_runtime.origins import normalize_browser_origin

INVALID_BROWSER_START_URL = "Browser start URL must be a public HTTPS URL"
PROFILE_NAVIGATION_OVERRIDE = "Saved browser profiles use their configured origins"


class BrowserTaskArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instruction: str = Field(min_length=1, max_length=12_000)
    start_url: str | None = Field(default=None, min_length=1, max_length=4096)
    allowed_origins: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    profile: str | None = Field(default=None, min_length=1, max_length=128)
    action_mode: BrowserTaskActionMode = BrowserTaskActionMode.READ_ONLY

    @field_validator("instruction", mode="before")
    @classmethod
    def normalize_instruction(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("start_url", mode="before")
    @classmethod
    def normalize_start_url(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_browser_start_url(value)
        return value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def normalize_allowed_origins(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(normalize_browser_origin(item) for item in value)
        return value

    @model_validator(mode="after")
    def validate_task_scope(self) -> "BrowserTaskArguments":
        if len(set(self.allowed_origins)) != len(self.allowed_origins):
            message = "allowed_origins must be unique"
            raise ValueError(message)
        if self.profile is not None and self.allowed_origins:
            raise ValueError(PROFILE_NAVIGATION_OVERRIDE)
        if self.start_url is not None and self.profile is None:
            start_origin = browser_url_origin(self.start_url)
            if self.allowed_origins and start_origin not in self.allowed_origins:
                message = "start_url origin must be included in allowed_origins"
                raise ValueError(message)
        return self


def normalize_browser_start_url(value: str) -> str:
    stripped = value.strip()
    try:
        parsed = urlsplit(stripped)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(INVALID_BROWSER_START_URL) from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port not in {None, 443}
    ):
        raise ValueError(INVALID_BROWSER_START_URL)
    origin = normalize_browser_origin(f"https://{parsed.hostname}")
    normalized_host = urlsplit(origin).hostname
    if normalized_host is None:
        raise ValueError(INVALID_BROWSER_START_URL)
    return urlunsplit(("https", normalized_host, parsed.path or "/", parsed.query, ""))


def browser_url_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.hostname is None:
        raise ValueError(INVALID_BROWSER_START_URL)
    return normalize_browser_origin(f"https://{parsed.hostname}")

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import SecretStr


class BrowserTaskActionMode(StrEnum):
    READ_ONLY = "read_only"
    STATE_CHANGES_ALLOWED = "state_changes_allowed"


class BrowserTaskModelAdapter(StrEnum):
    STAGEHAND_NATIVE = "stagehand_native"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True, slots=True)
class BrowserTaskModelCredential:
    adapter: BrowserTaskModelAdapter
    model_name: str
    base_url: str
    api_key: SecretStr


@dataclass(frozen=True, slots=True)
class BrowserTaskVariable:
    value: SecretStr
    description: str


@dataclass(frozen=True, slots=True)
class BrowserTaskRequest:
    instruction: str
    start_url: str
    allowed_origins: tuple[str, ...]
    action_mode: BrowserTaskActionMode
    browser_country_code: str
    max_steps: int
    variables: dict[str, BrowserTaskVariable]
    model_credential: BrowserTaskModelCredential | None = None


@dataclass(frozen=True, slots=True)
class BrowserTaskResult:
    output_text: str
    successful: bool


class BrowserTaskExecutor(Protocol):
    async def execute_task(
        self,
        *,
        request: BrowserTaskRequest,
    ) -> BrowserTaskResult:
        """Execute one bounded browser task and release its browser session."""


class BrowserTaskModelResolver(Protocol):
    async def resolve(self, *, user_id: str) -> BrowserTaskModelCredential:
        """Resolve the authenticated user's selected browser-task model transport."""

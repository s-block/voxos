from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pydantic import SecretStr

from voxos.browser_accounts.models import (
    BrowserAccountErrorCategory,
    BrowserAccountProvider,
)
from voxos.browser_accounts.secrets import (
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_runtime.contracts import (
    BrowserTaskRequest,
    BrowserTaskResult,
)


@dataclass(frozen=True, slots=True)
class BrowserProviderCredential:
    provider: BrowserAccountProvider = BrowserAccountProvider.BROWSER_USE_CLOUD
    api_key: SecretStr | None = None


@dataclass(frozen=True, slots=True)
class BrowserProviderCapabilities:
    human_live_view: bool
    managed_profiles: bool
    remote_profile_deletion: bool
    recording_can_be_disabled: bool
    exact_domain_restriction: bool
    persistent_state: bool
    native_secret_injection: bool
    concurrent_profile_sessions: int


@dataclass(frozen=True, slots=True)
class BrowserProviderSetupSession:
    handle: OpaqueBrowserSessionHandle
    live_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserProviderTaskSession:
    task_handle: SecretStr
    session_handle: OpaqueBrowserSessionHandle


class BrowserProviderOperationError(RuntimeError):
    def __init__(self, category: BrowserAccountErrorCategory) -> None:
        super().__init__("Browser provider operation failed")
        self.category = category


class BrowserTaskProvider(Protocol):
    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        """Stop a provider session and flush profile state."""

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        """Start one policy-constrained browser task."""

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        """Wait for the provider task to reach a terminal state."""


class BrowserAccountProviderAdapter(BrowserTaskProvider, Protocol):
    capabilities: BrowserProviderCapabilities

    async def create_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        account_label: str,
        external_user_reference: str,
    ) -> OpaqueBrowserProfileHandle:
        """Create an isolated provider-managed browser profile."""

    async def delete_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> None:
        """Delete a provider-managed browser profile."""

    async def profile_exists(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> bool:
        """Return whether the profile remains available at the provider."""

    async def start_setup_session(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
        browser_country_code: str,
    ) -> BrowserProviderSetupSession:
        """Start a non-recorded human-controlled setup session."""

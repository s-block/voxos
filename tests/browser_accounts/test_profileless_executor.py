import pytest
from pydantic import SecretStr

from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.providers.base import (
    BrowserProviderCredential,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.providers.profileless import ProfilelessBrowserTaskExecutor
from voxos.browser_accounts.secrets import (
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskRequest,
    BrowserTaskResult,
)


class FakeProvider:
    def __init__(self) -> None:
        self.profile: OpaqueBrowserProfileHandle | None = None
        self.stopped = False

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        assert credential.provider == BrowserAccountProvider.STAGEHAND_LOCAL
        self.profile = profile
        return BrowserProviderTaskSession(
            task_handle=SecretStr("task-1"),
            session_handle=OpaqueBrowserSessionHandle(SecretStr("session-1")),
        )

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        return BrowserTaskResult(output_text="done", successful=True)

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_profileless_executor_uses_local_provider_and_releases_session() -> None:
    provider = FakeProvider()
    executor = ProfilelessBrowserTaskExecutor(
        provider=provider,
        settings=BrowserAccountSettings(),
    )

    result = await executor.execute_task(
        request=BrowserTaskRequest(
            instruction="Read the page title",
            start_url="https://example.com",
            allowed_origins=("https://example.com",),
            action_mode=BrowserTaskActionMode.READ_ONLY,
            browser_country_code="GB",
            max_steps=10,
            variables={},
        ),
    )

    assert result.output_text == "done"
    assert provider.profile is None
    assert provider.stopped is True

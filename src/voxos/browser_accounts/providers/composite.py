from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.providers.base import (
    BrowserAccountProviderAdapter,
    BrowserProviderCapabilities,
    BrowserProviderCredential,
    BrowserProviderSetupSession,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.secrets import (
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_runtime.contracts import BrowserTaskRequest, BrowserTaskResult


class CompositeBrowserAccountProvider:
    capabilities = BrowserProviderCapabilities(
        human_live_view=True,
        managed_profiles=True,
        remote_profile_deletion=True,
        recording_can_be_disabled=True,
        exact_domain_restriction=False,
        persistent_state=True,
        native_secret_injection=False,
        concurrent_profile_sessions=1,
    )

    def __init__(
        self,
        *,
        browser_use: BrowserAccountProviderAdapter,
        browserless: BrowserAccountProviderAdapter,
        stagehand_local: BrowserAccountProviderAdapter,
    ) -> None:
        self._providers = {
            BrowserAccountProvider.BROWSER_USE_CLOUD: browser_use,
            BrowserAccountProvider.BROWSERLESS: browserless,
            BrowserAccountProvider.STAGEHAND_LOCAL: stagehand_local,
        }

    def _provider(
        self,
        credential: BrowserProviderCredential,
    ) -> BrowserAccountProviderAdapter:
        return self._providers[credential.provider]

    async def create_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        account_label: str,
        external_user_reference: str,
    ) -> OpaqueBrowserProfileHandle:
        return await self._provider(credential).create_profile(
            credential=credential,
            account_label=account_label,
            external_user_reference=external_user_reference,
        )

    async def delete_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> None:
        await self._provider(credential).delete_profile(
            credential=credential,
            profile=profile,
        )

    async def profile_exists(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> bool:
        return await self._provider(credential).profile_exists(
            credential=credential,
            profile=profile,
        )

    async def start_setup_session(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
        browser_country_code: str,
    ) -> BrowserProviderSetupSession:
        return await self._provider(credential).start_setup_session(
            credential=credential,
            profile=profile,
            browser_country_code=browser_country_code,
        )

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        await self._provider(credential).stop_session(
            credential=credential,
            session=session,
        )

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        return await self._provider(credential).start_task(
            credential=credential,
            profile=profile,
            request=request,
        )

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        return await self._provider(credential).wait_for_task(
            credential=credential,
            task=task,
        )

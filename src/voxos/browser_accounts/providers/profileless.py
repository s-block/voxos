import asyncio
import logging
from dataclasses import dataclass

from voxos.browser_accounts.models import BrowserAccountProvider
from voxos.browser_accounts.providers.base import (
    BrowserProviderCredential,
    BrowserTaskProvider,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import BrowserTaskRequest, BrowserTaskResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProfilelessBrowserTaskExecutor:
    """Adapt the local provider lifecycle to the general browser runtime."""

    provider: BrowserTaskProvider
    settings: BrowserAccountSettings

    async def execute_task(self, *, request: BrowserTaskRequest) -> BrowserTaskResult:
        credential = BrowserProviderCredential(
            provider=BrowserAccountProvider.STAGEHAND_LOCAL,
        )
        task = await self.provider.start_task(
            credential=credential,
            profile=None,
            request=request,
        )
        try:
            return await self.provider.wait_for_task(
                credential=credential,
                task=task,
            )
        finally:
            cleanup_task = asyncio.create_task(
                self.provider.stop_session(
                    credential=credential,
                    session=task.session_handle,
                ),
            )
            await self._finish_cleanup(cleanup_task)

    async def _finish_cleanup(self, cleanup_task: asyncio.Task[None]) -> None:
        try:
            async with asyncio.timeout(self.settings.cleanup_timeout_seconds):
                await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await self._finish_cleanup_after_cancellation(cleanup_task)
            raise
        except Exception:
            logger.exception("Failed to stop a public browser task session")

    async def _finish_cleanup_after_cancellation(
        self,
        cleanup_task: asyncio.Task[None],
    ) -> None:
        try:
            async with asyncio.timeout(self.settings.cleanup_timeout_seconds):
                await asyncio.shield(cleanup_task)
        except (asyncio.CancelledError, Exception):
            logger.exception("Failed to stop a cancelled public browser task session")

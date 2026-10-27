from dataclasses import dataclass

from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskExecutor,
    BrowserTaskModelResolver,
    BrowserTaskRequest,
    BrowserTaskResult,
)


@dataclass(frozen=True, slots=True)
class BrowserRuntimeService:
    """Execute provider-neutral browser tasks without a saved browser profile."""

    executor: BrowserTaskExecutor
    model_credential_resolver: BrowserTaskModelResolver
    max_task_steps: int
    browser_country_code: str

    async def execute_task(
        self,
        *,
        user_id: str,
        instruction: str,
        start_url: str,
        allowed_origins: tuple[str, ...],
        action_mode: BrowserTaskActionMode,
    ) -> BrowserTaskResult:
        model_credential = await self.model_credential_resolver.resolve(
            user_id=user_id,
        )
        return await self.executor.execute_task(
            request=BrowserTaskRequest(
                instruction=instruction,
                start_url=start_url,
                allowed_origins=allowed_origins,
                action_mode=action_mode,
                browser_country_code=self.browser_country_code,
                max_steps=self.max_task_steps,
                variables={},
                model_credential=model_credential,
            ),
        )

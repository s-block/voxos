import pytest
from pydantic import SecretStr

from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskModelAdapter,
    BrowserTaskModelCredential,
    BrowserTaskRequest,
    BrowserTaskResult,
)
from voxos.browser_runtime.service import BrowserRuntimeService


class FakeModelCredentialResolver:
    async def resolve(self, *, user_id: str) -> BrowserTaskModelCredential:
        assert user_id == "user-1"
        return BrowserTaskModelCredential(
            adapter=BrowserTaskModelAdapter.STAGEHAND_NATIVE,
            model_name="openai/gpt-5-mini",
            base_url="https://api.openai.com/v1",
            api_key=SecretStr("model-secret"),
        )


class FakeBrowserTaskExecutor:
    def __init__(self) -> None:
        self.request: BrowserTaskRequest | None = None

    async def execute_task(self, *, request: BrowserTaskRequest) -> BrowserTaskResult:
        self.request = request
        return BrowserTaskResult(
            output_text="The example title is Example Domain.",
            successful=True,
        )


@pytest.mark.asyncio
async def test_runtime_executes_agent_instruction_without_a_saved_profile() -> None:
    executor = FakeBrowserTaskExecutor()
    service = BrowserRuntimeService(
        executor=executor,
        model_credential_resolver=FakeModelCredentialResolver(),
        max_task_steps=40,
        browser_country_code="GB",
    )

    result = await service.execute_task(
        user_id="user-1",
        instruction="Open the page and report its title",
        start_url="https://example.com",
        allowed_origins=("https://example.com",),
        action_mode=BrowserTaskActionMode.READ_ONLY,
    )

    assert result.output_text == "The example title is Example Domain."
    assert executor.request is not None
    assert executor.request.instruction == "Open the page and report its title"
    assert executor.request.variables == {}
    assert executor.request.model_credential is not None
    assert executor.request.model_credential.model_name == "openai/gpt-5-mini"

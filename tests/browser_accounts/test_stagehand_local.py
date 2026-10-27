import httpx
import orjson
import pytest
from pydantic import SecretStr

from voxos.browser_accounts.models import (
    BrowserAccountErrorCategory,
    BrowserAccountProvider,
)
from voxos.browser_accounts.providers.base import (
    BrowserProviderCredential,
    BrowserProviderOperationError,
)
from voxos.browser_accounts.providers.stagehand_local import StagehandLocalProvider
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskModelAdapter,
    BrowserTaskModelCredential,
    BrowserTaskRequest,
    BrowserTaskVariable,
)


def browser_task_request() -> BrowserTaskRequest:
    return BrowserTaskRequest(
        instruction="Read the next payment date",
        start_url="https://example.com/billing",
        allowed_origins=("https://example.com", "https://login.example.com"),
        action_mode=BrowserTaskActionMode.READ_ONLY,
        browser_country_code="GB",
        max_steps=25,
        variables={
            "login_identifier": BrowserTaskVariable(
                value=SecretStr("user@example.com"),
                description="The approved website username or email",
            ),
            "login_password": BrowserTaskVariable(
                value=SecretStr("website-password"),
                description="The approved website password",
            ),
        },
        model_credential=BrowserTaskModelCredential(
            adapter=BrowserTaskModelAdapter.STAGEHAND_NATIVE,
            model_name="openai/gpt-5.4-mini",
            base_url="https://api.openai.com/v1",
            api_key=SecretStr("model-key"),
        ),
    )


@pytest.mark.asyncio
async def test_stagehand_local_rejects_missing_service_credentials_before_sending_task() -> None:
    contacted = False

    async def handle(_: httpx.Request) -> httpx.Response:
        nonlocal contacted
        contacted = True
        return httpx.Response(200, json={"status": "completed", "output": "result"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StagehandLocalProvider(
            http_client=client,
            settings=BrowserAccountSettings(_env_file=None, stagehand_service_token=None),
        )
        credential = BrowserProviderCredential(provider=BrowserAccountProvider.STAGEHAND_LOCAL)
        task = await provider.start_task(credential=credential, profile=None, request=browser_task_request())
        with pytest.raises(BrowserProviderOperationError) as error:
            await provider.wait_for_task(credential=credential, task=task)
        await provider.stop_session(credential=credential, session=task.session_handle)
    assert error.value.category == BrowserAccountErrorCategory.PROVIDER_AUTH
    assert contacted is False


@pytest.mark.asyncio
async def test_stagehand_local_sends_general_task_and_redacts_secrets() -> None:
    captured: dict[str, object] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-browser-service-token-with-enough-length"
        captured.update(orjson.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": "user@example.com used website-password with model-key",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StagehandLocalProvider(
            http_client=client,
            settings=BrowserAccountSettings(
                stagehand_base_url="http://browser-agent:3100",
                stagehand_service_token=SecretStr("test-browser-service-token-with-enough-length"),
            ),
        )
        credential = BrowserProviderCredential(
            provider=BrowserAccountProvider.STAGEHAND_LOCAL,
        )
        task = await provider.start_task(
            credential=credential,
            profile=None,
            request=browser_task_request(),
        )
        result = await provider.wait_for_task(credential=credential, task=task)
        await provider.stop_session(
            credential=credential,
            session=task.session_handle,
        )

    assert captured == {
        "instruction": "Read the next payment date",
        "startUrl": "https://example.com/billing",
        "allowedOrigins": ["https://example.com", "https://login.example.com"],
        "actionMode": "read_only",
        "maxSteps": 25,
        "variables": {
            "login_identifier": {
                "value": "user@example.com",
                "description": "The approved website username or email",
            },
            "login_password": {
                "value": "website-password",  # pragma: allowlist secret
                "description": "The approved website password",
            },
        },
        "model": {
            "adapter": "stagehand_native",
            "modelName": "openai/gpt-5.4-mini",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "model-key",  # pragma: allowlist secret
        },
    }
    assert result.successful is True
    assert result.output_text == "[redacted] used [redacted] with [redacted]"


@pytest.mark.asyncio
async def test_stagehand_local_accepts_a_task_without_secret_variables() -> None:
    captured: dict[str, object] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-browser-service-token-with-enough-length"
        captured.update(orjson.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output": "Public result"})

    request = browser_task_request()
    request = BrowserTaskRequest(
        instruction="Summarize the public documentation",
        start_url=request.start_url,
        allowed_origins=request.allowed_origins,
        action_mode=request.action_mode,
        browser_country_code=request.browser_country_code,
        max_steps=request.max_steps,
        variables={},
        model_credential=request.model_credential,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StagehandLocalProvider(
            http_client=client,
            settings=BrowserAccountSettings(
                stagehand_base_url="http://browser-agent:3100",
                stagehand_service_token=SecretStr("test-browser-service-token-with-enough-length"),
            ),
        )
        credential = BrowserProviderCredential(provider=BrowserAccountProvider.STAGEHAND_LOCAL)
        task = await provider.start_task(credential=credential, profile=None, request=request)
        result = await provider.wait_for_task(credential=credential, task=task)
        await provider.stop_session(credential=credential, session=task.session_handle)

    assert captured["variables"] == {}
    assert result.output_text == "Public result"


@pytest.mark.asyncio
async def test_stagehand_local_maps_adapter_rejection_without_exposing_body() -> None:
    async def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": "secret provider detail"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = StagehandLocalProvider(
            http_client=client,
            settings=BrowserAccountSettings(
                stagehand_base_url="http://browser-agent:3100",
                stagehand_service_token=SecretStr("test-browser-service-token-with-enough-length"),
            ),
        )
        credential = BrowserProviderCredential(provider=BrowserAccountProvider.STAGEHAND_LOCAL)
        task = await provider.start_task(
            credential=credential,
            profile=None,
            request=browser_task_request(),
        )

        with pytest.raises(BrowserProviderOperationError) as error:
            await provider.wait_for_task(credential=credential, task=task)
        await provider.stop_session(credential=credential, session=task.session_handle)

    assert error.value.category == BrowserAccountErrorCategory.PROVIDER_REJECTED
    assert "secret provider detail" not in str(error.value)

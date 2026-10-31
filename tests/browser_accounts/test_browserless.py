from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import orjson
import pytest
from pydantic import SecretStr
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

import voxos.browser_accounts.providers.browserless as browserless_module
from voxos.browser_accounts.models import (
    BrowserAccountErrorCategory,
    BrowserAccountProvider,
)
from voxos.browser_accounts.providers.base import (
    BrowserProviderCredential,
    BrowserProviderOperationError,
)
from voxos.browser_accounts.providers.browserless import (
    BROWSERLESS_EMPTY_RESULT,
    BROWSERLESS_FAILED_RESULT,
    BrowserlessProvider,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import (
    BrowserTaskActionMode,
    BrowserTaskRequest,
    BrowserTaskResult,
    BrowserTaskVariable,
)


class FakeBrowserlessConnection:
    def __init__(self, messages: tuple[str, ...] = ()) -> None:
        self.sent: list[str] = []
        self.messages = messages

    async def __aenter__(self) -> "FakeBrowserlessConnection":
        return self

    async def __aexit__(self, *args: object) -> None:
        del args

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._messages()

    async def _messages(self) -> AsyncIterator[str]:
        for message in self.messages:
            yield message


def browser_task_request() -> BrowserTaskRequest:
    return BrowserTaskRequest(
        instruction="Read the latest invoice total",
        start_url="https://billing.example.com",
        allowed_origins=("https://billing.example.com",),
        action_mode=BrowserTaskActionMode.READ_ONLY,
        browser_country_code="GB",
        max_steps=12,
        variables={
            "login_identifier": BrowserTaskVariable(
                value=SecretStr("user@example.com"),
                description="The approved website username or email",
            ),
            "login_password": BrowserTaskVariable(
                value=SecretStr("password-123"),
                description="The approved website password",
            ),
        },
    )


async def run_browserless_messages(
    monkeypatch: pytest.MonkeyPatch,
    messages: tuple[str, ...],
) -> BrowserTaskResult:
    connection = FakeBrowserlessConnection(messages)

    def fake_connect(url: str, **kwargs: object) -> FakeBrowserlessConnection:
        del url, kwargs
        return connection

    monkeypatch.setattr(browserless_module, "connect", fake_connect)
    provider = BrowserlessProvider(BrowserAccountSettings(enabled=True))
    credential = BrowserProviderCredential(
        api_key=SecretStr("browserless-token"),
        provider=BrowserAccountProvider.BROWSERLESS,
    )
    task = await provider.start_task(
        credential=credential,
        profile=None,
        request=browser_task_request(),
    )
    try:
        return await provider.wait_for_task(
            credential=credential,
            task=task,
        )
    finally:
        await provider.stop_session(
            credential=credential,
            session=task.session_handle,
        )


@pytest.mark.asyncio
async def test_browserless_agent_receives_credentials_and_redacts_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeBrowserlessConnection(
        (
            '{"type":"data","text":"Checking the billing account"}',
            (
                '{"type":"end","result":{"success":true,"reason_code":"completed",'
                '"final_url":"https://billing.example.com/account",'
                '"evidence":"Invoice: £42 for user@example.com using password-123"}}'
            ),
        ),
    )
    captured_url: list[str] = []

    def fake_connect(url: str, **kwargs: object) -> FakeBrowserlessConnection:
        del kwargs
        captured_url.append(url)
        return connection

    monkeypatch.setattr(browserless_module, "connect", fake_connect)
    provider = BrowserlessProvider(
        BrowserAccountSettings(
            enabled=True,
            task_timeout_seconds=300,
        ),
    )
    credential = BrowserProviderCredential(
        api_key=SecretStr("browserless-token"),
        provider=BrowserAccountProvider.BROWSERLESS,
    )
    task = await provider.start_task(
        credential=credential,
        profile=None,
        request=browser_task_request(),
    )

    result = await provider.wait_for_task(credential=credential, task=task)

    assert result.successful is True
    assert result.output_text == "Invoice: £42 for [redacted] using [redacted]"
    assert len(captured_url) == 1
    parsed_url = urlsplit(captured_url[0])
    assert parsed_url.scheme == "wss"
    assert parsed_url.netloc == "production-lon.browserless.io"
    assert parsed_url.path == "/chromium/cli"
    assert parse_qs(parsed_url.query) == {"token": ["browserless-token"]}
    assert len(connection.sent) == 1
    assert not connection.sent[0].endswith("\n")
    start_message = orjson.loads(connection.sent[0])
    assert set(start_message) == {"type", "prompt"}
    assert start_message["type"] == "start"
    assert "Start URL: https://billing.example.com" in start_message["prompt"]
    assert "Allowed origins: https://billing.example.com" in start_message["prompt"]
    assert "Maximum browser steps: 12" in start_message["prompt"]
    assert "Instruction: Read the latest invoice total" in start_message["prompt"]
    assert "Variable %login_identifier%" in start_message["prompt"]
    assert "Variable %login_password%" in start_message["prompt"]
    assert "user@example.com" in start_message["prompt"]
    assert "password-123" in start_message["prompt"]


@pytest.mark.asyncio
async def test_browserless_agent_uses_structured_failure_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await run_browserless_messages(
        monkeypatch,
        (
            (
                '{"type":"end","result":{"success":false,'
                '"reason_code":"invalid_credentials",'
                '"evidence":"Login failed for user@example.com with password-123"}}'
            ),
        ),
    )

    assert result.successful is False
    assert result.output_text == (
        f"{BROWSERLESS_FAILED_RESULT}\n"
        "Reason: invalid_credentials\n"
        "Evidence: Login failed for [redacted] with [redacted]"
    )


@pytest.mark.asyncio
async def test_browserless_agent_rejects_success_without_usable_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await run_browserless_messages(
        monkeypatch,
        ('{"type":"end","result":{"success":true}}',),
    )

    assert result == BrowserTaskResult(
        output_text=BROWSERLESS_EMPTY_RESULT,
        successful=False,
    )


@pytest.mark.asyncio
async def test_browserless_agent_uses_streamed_output_when_evidence_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await run_browserless_messages(
        monkeypatch,
        (
            '{"type":"data","text":"Next payment: £42 "}',
            '{"type":"data","text":"on 1 September"}',
            '{"type":"end","result":{"success":true}}',
        ),
    )

    assert result == BrowserTaskResult(
        output_text="Next payment: £42 on 1 September",
        successful=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_category"),
    [
        (
            '{"type":"end","result":{"success":"false"}}',
            BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
        ),
        (
            '{"type":"data","data":"wrong field"}',
            BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
        ),
        (
            '{"type":"error","retryable":false}',
            BrowserAccountErrorCategory.PROVIDER_REJECTED,
        ),
        (
            '{"type":"error","retryable":true}',
            BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
        ),
    ],
)
async def test_browserless_agent_rejects_invalid_or_error_terminal_events(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    expected_category: BrowserAccountErrorCategory,
) -> None:
    with pytest.raises(BrowserProviderOperationError) as error:
        await run_browserless_messages(monkeypatch, (message,))

    assert error.value.category == expected_category


@pytest.mark.asyncio
async def test_browserless_agent_rejects_connection_without_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(BrowserProviderOperationError) as error:
        await run_browserless_messages(
            monkeypatch,
            ('{"type":"data","text":"Still working"}',),
        )

    assert error.value.category == BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_category"),
    [
        (400, BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID),
        (401, BrowserAccountErrorCategory.PROVIDER_AUTH),
        (403, BrowserAccountErrorCategory.PROVIDER_AUTH),
        (429, BrowserAccountErrorCategory.PROVIDER_REJECTED),
        (500, BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE),
    ],
)
async def test_browserless_agent_classifies_handshake_failure(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_category: BrowserAccountErrorCategory,
) -> None:
    def rejected_connect(url: str, **kwargs: object) -> FakeBrowserlessConnection:
        del url, kwargs
        raise InvalidStatus(
            Response(
                status_code=status_code,
                reason_phrase="Rejected",
                headers=Headers(),
            ),
        )

    monkeypatch.setattr(browserless_module, "connect", rejected_connect)
    provider = BrowserlessProvider(BrowserAccountSettings(enabled=True))
    credential = BrowserProviderCredential(
        api_key=SecretStr("browserless-token"),
        provider=BrowserAccountProvider.BROWSERLESS,
    )
    with pytest.raises(BrowserProviderOperationError) as error:
        await provider.start_task(
            credential=credential,
            profile=None,
            request=browser_task_request(),
        )

    assert error.value.category == expected_category

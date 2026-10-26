import asyncio
import logging
import sys
from collections.abc import Iterator
from unittest.mock import patch

import orjson
import pytest
import sentry_sdk
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sentry_sdk.client import Client
from sentry_sdk.envelope import Envelope
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.excepthook import ExcepthookIntegration
from sentry_sdk.transport import Transport

from voxos.observability.sentry import SentrySettings, initialize_sentry

TEST_DSN = "https://public@example.test/1"
PRIVATE_PAYLOAD = "private-tenant-content"


class RecordingTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.envelopes: list[Envelope] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        self.envelopes.append(envelope)


@pytest.fixture
def transport() -> Iterator[RecordingTransport]:
    recorder = RecordingTransport()
    original_init = sentry_sdk.init
    with patch("voxos.observability.sentry.sentry_sdk.init", wraps=original_init) as init:
        # Use the real SDK/integrations with an in-memory transport, never HTTP.
        def init_client(*args: object, **kwargs: object) -> None:
            client = Client(*args, **kwargs, transport=recorder)
            sentry_sdk.get_global_scope().set_client(client)

        init.side_effect = init_client
        initialize_sentry("test-api", SentrySettings(dsn=TEST_DSN, _env_file=None))
        yield recorder
    sentry_sdk.get_client().close()
    sentry_sdk.get_global_scope().set_client(None)


def test_sentry_disabled_without_dsn() -> None:
    with patch("voxos.observability.sentry.sentry_sdk.init") as init:
        initialize_sentry("api", SentrySettings(dsn="", _env_file=None))
    init.assert_not_called()


@pytest.mark.parametrize("rate", [-0.01, 1.01, float("nan")])
def test_sentry_rejects_invalid_sampling(rate: float) -> None:
    with pytest.raises(ValidationError):
        SentrySettings(traces_sample_rate=rate, _env_file=None)


def test_sdk_keeps_stack_and_strips_private_payloads(transport: RecordingTransport) -> None:
    with sentry_sdk.new_scope() as scope:
        scope.set_user({"id": PRIVATE_PAYLOAD, "email": PRIVATE_PAYLOAD})
        scope.set_extra("provider_response", PRIVATE_PAYLOAD)
        scope.set_context("conversation", {"prompt": PRIVATE_PAYLOAD})
        scope.set_tag("tenant", PRIVATE_PAYLOAD)
        try:
            raise ValueError(PRIVATE_PAYLOAD)
        except ValueError:
            sentry_sdk.capture_exception()
    payload = transport.envelopes[0].items[0].payload.json
    assert payload is not None
    assert PRIVATE_PAYLOAD.encode() not in orjson.dumps(payload)
    exception = payload["exception"]["values"][0]
    assert exception["type"] == "ValueError"
    assert exception["stacktrace"]["frames"][-1]["function"] == "test_sdk_keeps_stack_and_strips_private_payloads"
    assert payload["tags"] == {"voxos.service": "test-api"}


def test_fastapi_unhandled_exception_is_reported(transport: RecordingTransport) -> None:
    app = FastAPI()

    @app.post("/sentry-test")
    async def fail() -> None:
        raise RuntimeError(PRIVATE_PAYLOAD)

    response = TestClient(app, raise_server_exceptions=False).post(
        "/sentry-test",
        headers={"Authorization": f"Bearer {PRIVATE_PAYLOAD}"},
        json={"prompt": PRIVATE_PAYLOAD},
    )
    assert response.status_code == 500
    errors = [item.payload.json for envelope in transport.envelopes for item in envelope.items if item.type == "event"]
    assert len(errors) == 1
    assert errors[0] is not None
    assert PRIVATE_PAYLOAD.encode() not in orjson.dumps(errors[0])
    assert errors[0]["exception"]["values"][0]["type"] == "RuntimeError"


def test_worker_error_logs_report_errors_only(transport: RecordingTransport) -> None:
    logger = logging.getLogger("voxos.sentry-test")
    logger.warning(PRIVATE_PAYLOAD)
    assert transport.envelopes == []
    logger.error(PRIVATE_PAYLOAD)
    assert len(transport.envelopes) == 1
    assert PRIVATE_PAYLOAD.encode() not in transport.envelopes[0].serialize()
    payload = transport.envelopes[0].items[0].payload.json
    assert payload is not None
    frames = payload["threads"]["values"][0]["stacktrace"]["frames"]
    assert any(frame["function"] == "test_worker_error_logs_report_errors_only" for frame in frames)


def test_conservative_settings_and_repeated_initialization(transport: RecordingTransport) -> None:
    client = sentry_sdk.get_client()
    initialize_sentry("another-worker", SentrySettings(dsn=TEST_DSN, _env_file=None))
    assert sentry_sdk.get_client() is client
    assert client.options["sample_rate"] == 1
    assert client.options["traces_sample_rate"] == 0.01
    assert client.options["include_local_variables"] is False
    assert client.options["max_request_body_size"] == "never"
    assert client.options["enable_logs"] is False
    assert client.options["profiles_sample_rate"] == 0
    assert "openai" not in client.integrations


def test_asyncio_task_errors_are_reported_after_sync_initialization_in_each_loop(
    transport: RecordingTransport,
) -> None:
    client = sentry_sdk.get_client()
    assert client.get_integration(AsyncioIntegration) is None

    async def fail() -> None:
        raise RuntimeError(PRIVATE_PAYLOAD)

    async def run() -> None:
        initialize_sentry("test-api")
        loop = asyncio.get_running_loop()
        task_factory = loop.get_task_factory()
        assert task_factory is not None
        initialize_sentry("test-api")
        assert loop.get_task_factory() is task_factory
        assert sentry_sdk.get_client() is client
        integration = client.get_integration(AsyncioIntegration)
        assert integration is not None
        assert integration.task_spans is False
        with pytest.raises(RuntimeError, match=PRIVATE_PAYLOAD):
            await asyncio.create_task(fail())

    # Integration setup is cached by the SDK, but each new loop needs its own factory.
    asyncio.run(run())
    asyncio.run(run())
    errors = [item.payload.json for envelope in transport.envelopes for item in envelope.items if item.type == "event"]
    assert len(errors) == 2
    for error in errors:
        assert error is not None
        assert PRIVATE_PAYLOAD.encode() not in orjson.dumps(error)
        exception = error["exception"]["values"][0]
        assert exception["type"] == "RuntimeError"
        assert exception["mechanism"] == {"type": "asyncio", "handled": False}
        assert exception["stacktrace"]["frames"][-1]["function"] == "fail"


def test_default_excepthook_reports_fatal_top_level_coroutine(transport: RecordingTransport) -> None:
    assert sentry_sdk.get_client().get_integration(ExcepthookIntegration) is not None
    assert "atexit" in sentry_sdk.get_client().integrations

    async def fail() -> None:
        initialize_sentry("test-api")
        raise RuntimeError(PRIVATE_PAYLOAD)

    try:
        asyncio.run(fail())
    except RuntimeError as exc:
        # The interpreter invokes this hook when a top-level asyncio.run() fails.
        sys.excepthook(type(exc), exc, exc.__traceback__)

    errors = [item.payload.json for envelope in transport.envelopes for item in envelope.items if item.type == "event"]
    assert len(errors) == 1
    assert errors[0] is not None
    assert PRIVATE_PAYLOAD.encode() not in orjson.dumps(errors[0])
    exception = errors[0]["exception"]["values"][0]
    assert exception["type"] == "RuntimeError"
    assert exception["mechanism"] == {"type": "excepthook", "handled": False}
    assert exception["stacktrace"]["frames"][-1]["function"] == "fail"


def test_health_checks_do_not_consume_trace_quota(transport: RecordingTransport) -> None:
    sampler = sentry_sdk.get_client().options["traces_sampler"]
    assert sampler is not None
    assert sampler({"asgi_scope": {"path": "/health"}}) == 0
    assert sampler({"asgi_scope": {"path": "/api/chats"}}) == 0.01

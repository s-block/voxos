import asyncio
import logging
from unittest.mock import AsyncMock

import orjson
import pytest
from pytest import MonkeyPatch

import voxos.whatsapp.bridge as bridge_module
from voxos.whatsapp.bridge import (
    BRIDGE_CHILD_ENVIRONMENT,
    WhatsAppBridgeClient,
    _bridge_child_environment,
)
from voxos.whatsapp.settings import WhatsAppWorkerSettings


async def ignore_event(event: object) -> None:
    del event


async def wait_forever() -> None:
    await asyncio.Event().wait()


async def wait_for_event_dispatch(client: WhatsAppBridgeClient) -> None:
    for _ in range(10):
        if not client._event_tasks:
            return
        await asyncio.sleep(0)
    message = "bridge event dispatch did not complete"
    raise AssertionError(message)


def test_bridge_failure_log_includes_type_without_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redacted_detail = "sensitive bridge detail"
    caplog.set_level(logging.ERROR, logger="voxos.whatsapp.bridge")

    bridge_module._log_sanitized_failure(
        "WhatsApp bridge event handling failed",
        RuntimeError(redacted_detail),
        event="session_status",
    )

    assert ("WhatsApp bridge event handling failed [failure_type=RuntimeError event=session_status") in caplog.text
    assert redacted_detail not in caplog.text


def test_bridge_child_environment_does_not_inherit_application_secrets() -> None:
    assert {
        "NODE_ENV": "production",
        "PATH": bridge_module.os.defpath,
        "TZ": "UTC",
    } == BRIDGE_CHILD_ENVIRONMENT
    assert _bridge_child_environment(
        WhatsAppWorkerSettings(
            whatsapp_bridge_request_timeout_seconds=12.345,
        ),
    ) == {
        **BRIDGE_CHILD_ENVIRONMENT,
        "VOXOS_WHATSAPP_RPC_TIMEOUT_MS": "12345",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("logout", "expected_command"),
    (
        (False, "stop_session"),
        (True, "logout_if_linked"),
    ),
)
async def test_stop_session_uses_explicit_local_or_remote_lifecycle_command(
    logout: bool,
    expected_command: str,
) -> None:
    client = WhatsAppBridgeClient(
        settings=WhatsAppWorkerSettings(),
        event_handler=ignore_event,
    )
    command = AsyncMock(return_value={})
    client.command = command

    await client.stop_session(
        session_handle="integration-1",
        generation=7,
        logout=logout,
    )

    command.assert_awaited_once_with(
        session_handle="integration-1",
        generation=7,
        command=expected_command,
    )


@pytest.mark.asyncio
async def test_bridge_isolates_events_when_dispatch_capacity_is_exhausted(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(bridge_module, "MAX_EVENT_TASKS", 1)
    client = WhatsAppBridgeClient(
        settings=WhatsAppWorkerSettings(),
        event_handler=ignore_event,
    )
    blocker = asyncio.create_task(wait_forever())
    client._event_tasks.add(blocker)
    frame = orjson.dumps(
        {
            "protocol_version": 2,
            "frame_type": "event",
            "request_id": "event-1",
            "session_handle": "integration-1",
            "generation": 1,
            "event": "session_status",
            "payload": {"state": "connecting"},
        },
    )

    try:
        client._handle_frame(frame)
        assert client._event_tasks == {blocker}
    finally:
        blocker.cancel()
        await asyncio.gather(blocker, return_exceptions=True)


@pytest.mark.asyncio
async def test_bridge_dispatches_a_durable_event_replay_once() -> None:
    received_request_ids: list[str] = []

    async def record_event(event: object) -> None:
        assert isinstance(event, bridge_module.BridgeEventFrame)
        received_request_ids.append(event.request_id)

    client = WhatsAppBridgeClient(
        settings=WhatsAppWorkerSettings(),
        event_handler=record_event,
    )
    frame = orjson.dumps(
        {
            "protocol_version": 2,
            "frame_type": "event",
            "request_id": "durable-event-1",
            "session_handle": "integration-1",
            "generation": 1,
            "event": "session_status",
            "payload": {"state": "connecting"},
        },
    )

    client._handle_frame(frame)
    await wait_for_event_dispatch(client)
    client._handle_frame(frame)
    await asyncio.sleep(0)

    assert received_request_ids == ["durable-event-1"]


@pytest.mark.asyncio
async def test_bridge_does_not_dispatch_an_inflight_durable_event_replay() -> None:
    release_event = asyncio.Event()
    received_request_ids: list[str] = []

    async def wait_for_release(event: object) -> None:
        assert isinstance(event, bridge_module.BridgeEventFrame)
        received_request_ids.append(event.request_id)
        await release_event.wait()

    client = WhatsAppBridgeClient(
        settings=WhatsAppWorkerSettings(),
        event_handler=wait_for_release,
    )
    frame = orjson.dumps(
        {
            "protocol_version": 2,
            "frame_type": "event",
            "request_id": "durable-event-1",
            "session_handle": "integration-1",
            "generation": 1,
            "event": "session_status",
            "payload": {"state": "connecting"},
        },
    )

    client._handle_frame(frame)
    client._handle_frame(frame)
    await asyncio.sleep(0)
    release_event.set()
    await wait_for_event_dispatch(client)

    assert received_request_ids == ["durable-event-1"]

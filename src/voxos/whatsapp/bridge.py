import asyncio
import logging
import os
import shutil
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from voxos.whatsapp.models import WhatsAppLinkMethod
from voxos.whatsapp.settings import WhatsAppWorkerSettings

logger = logging.getLogger(__name__)
WHATSAPP_BRIDGE_PROTOCOL_VERSION = 2
BRIDGE_SESSION_HANDLE = "bridge"
MAX_PENDING_COMMANDS = 1024
MAX_EVENT_TASKS = 1024
MAX_EVENT_TASKS_PER_SESSION = 64
MAX_COMPLETED_EVENT_REQUESTS = 4096
BRIDGE_CHILD_ENVIRONMENT = {
    "NODE_ENV": "production",
    "PATH": os.defpath,
    "TZ": "UTC",
}


def _bridge_child_environment(
    settings: WhatsAppWorkerSettings,
) -> dict[str, str]:
    return {
        **BRIDGE_CHILD_ENVIRONMENT,
        "VOXOS_WHATSAPP_RPC_TIMEOUT_MS": str(
            int(settings.whatsapp_bridge_request_timeout_seconds * 1000),
        ),
    }


def _log_sanitized_failure(
    message: str,
    error: BaseException,
    *,
    event: str | None = None,
    event_request_id: str | None = None,
) -> None:
    failure_type = type(error).__name__
    failure_code = error.code if isinstance(error, WhatsAppBridgeError) else None
    extra = {
        "failure_type": failure_type,
        "failure_code": failure_code,
    }
    if event is not None:
        extra["event"] = event
    if event_request_id is not None:
        extra["event_request_id"] = event_request_id
    details = [f"failure_type={failure_type}"]
    if failure_code is not None:
        details.append(f"failure_code={failure_code}")
    if event is not None:
        details.append(f"event={event}")
    if event_request_id is not None:
        details.append(f"event_request_id={event_request_id}")
    logger.error("%s [%s]", message, " ".join(details), extra=extra)


class WhatsAppBridgeError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        ambiguous: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.ambiguous = ambiguous


class WhatsAppBridgeUnavailableError(WhatsAppBridgeError):
    def __init__(self, *, ambiguous: bool = False) -> None:
        super().__init__(
            "bridge_unavailable",
            retryable=True,
            ambiguous=ambiguous,
        )


class WhatsAppBridgeProtocolError(WhatsAppBridgeError):
    def __init__(self) -> None:
        super().__init__("invalid_bridge_protocol")


class BridgeErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=64)
    retryable: bool
    ambiguous: bool


class BridgeResponseFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[2]
    frame_type: Literal["response"]
    request_id: str = Field(min_length=1, max_length=128)
    session_handle: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=0)
    success: bool
    payload: dict[str, object] | None = None
    error: BridgeErrorPayload | None = None

    @model_validator(mode="after")
    def validate_result_shape(self) -> "BridgeResponseFrame":
        if self.success and self.error is not None:
            message = "successful bridge response must not contain error"
            raise ValueError(message)
        if not self.success and self.error is None:
            message = "failed bridge response must contain error"
            raise ValueError(message)
        return self


class BridgeEventFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[2]
    frame_type: Literal["event"]
    request_id: str = Field(min_length=1, max_length=128)
    session_handle: str = Field(min_length=1, max_length=128)
    generation: int = Field(ge=0)
    event: str = Field(min_length=1, max_length=64)
    payload: dict[str, object]


class BridgeHandshake(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[2]
    bridge_version: str = Field(min_length=1, max_length=64)
    baileys_version: str = Field(min_length=1, max_length=64)
    max_frame_bytes: int = Field(gt=0)
    max_sessions: int = Field(gt=0)
    rpc_timeout_ms: int = Field(gt=0)
    capabilities: tuple[str, ...]


class BridgeStartResult(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    state: str = Field(min_length=1, max_length=32)
    registered: bool
    linked: bool
    reconnect_attempts: int = Field(ge=0)
    resumed: bool


class BridgeSendResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_message_id: str = Field(min_length=1, max_length=256)


BridgeEventHandler = Callable[[BridgeEventFrame], Awaitable[None]]
BridgeEventKey = tuple[str, int, str]


@dataclass(frozen=True, slots=True)
class PendingBridgeCommand:
    session_handle: str
    generation: int
    command: str
    future: asyncio.Future[BridgeResponseFrame]


class WhatsAppBridgeClient:
    def __init__(
        self,
        *,
        settings: WhatsAppWorkerSettings,
        event_handler: BridgeEventHandler,
    ) -> None:
        self._settings = settings
        self._event_handler = event_handler
        self._process: asyncio.subprocess.Process | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, PendingBridgeCommand] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._event_task_counts: dict[str, int] = {}
        self._event_tasks_by_key: dict[BridgeEventKey, asyncio.Task[None]] = {}
        self._completed_event_keys: OrderedDict[BridgeEventKey, None] = OrderedDict()
        self._closed = False
        self._handshake: BridgeHandshake | None = None

    @property
    def handshake(self) -> BridgeHandshake:
        if self._handshake is None:
            raise WhatsAppBridgeUnavailableError
        return self._handshake

    @property
    def running(self) -> bool:
        process = self._process
        return (
            not self._closed
            and process is not None
            and process.returncode is None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def start(self) -> BridgeHandshake:
        if self._process is not None:
            message = "WhatsApp bridge client has already been started"
            raise RuntimeError(message)
        self._closed = False
        executable = shutil.which(self._settings.whatsapp_bridge_executable)
        if executable is None:
            raise WhatsAppBridgeUnavailableError
        self._process = await asyncio.create_subprocess_exec(
            executable,
            self._settings.whatsapp_bridge_script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_bridge_child_environment(self._settings),
            limit=self._settings.whatsapp_ipc_max_frame_bytes + 1,
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(),
            name="whatsapp-bridge-reader",
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(),
            name="whatsapp-bridge-stderr",
        )
        try:
            payload = await self.command(
                session_handle=BRIDGE_SESSION_HANDLE,
                generation=0,
                command="handshake",
                timeout_seconds=self._settings.whatsapp_bridge_start_timeout_seconds,
            )
            handshake = BridgeHandshake.model_validate(payload)
            required_capabilities = {
                "challenge_revisions",
                "multi_session",
                "remote_auth",
                "durable_events",
                "ordered_durable_events",
                "linked_state",
                "logout_if_linked",
                "qr",
                "pairing_code",
                "one_to_one_text",
                "pn_lid_identity",
            }
            if not required_capabilities.issubset(handshake.capabilities):
                raise WhatsAppBridgeProtocolError
            if handshake.max_frame_bytes != self._settings.whatsapp_ipc_max_frame_bytes:
                raise WhatsAppBridgeProtocolError
            if handshake.rpc_timeout_ms != int(
                self._settings.whatsapp_bridge_request_timeout_seconds * 1000,
            ):
                raise WhatsAppBridgeProtocolError
            self._handshake = handshake
            return handshake
        except (ValidationError, WhatsAppBridgeError) as error:
            await self.close()
            if isinstance(error, WhatsAppBridgeError):
                raise
            raise WhatsAppBridgeProtocolError from error

    async def start_session(
        self,
        *,
        session_handle: str,
        generation: int,
        credentials: object | None,
        requested_method: WhatsAppLinkMethod,
        request_revision: int,
    ) -> BridgeStartResult:
        payload = await self.command(
            session_handle=session_handle,
            generation=generation,
            command="start_session",
            payload={
                "credentials": credentials,
                "requested_method": requested_method.value,
                "request_revision": request_revision,
            },
        )
        try:
            return BridgeStartResult.model_validate(payload)
        except ValidationError as error:
            raise WhatsAppBridgeProtocolError from error

    async def stop_session(
        self,
        *,
        session_handle: str,
        generation: int,
        logout: bool = False,
    ) -> None:
        await self.command(
            session_handle=session_handle,
            generation=generation,
            command="logout_if_linked" if logout else "stop_session",
        )

    async def request_challenge(
        self,
        *,
        session_handle: str,
        generation: int,
        method: WhatsAppLinkMethod,
        phone_number: str | None,
        regenerate: bool,
        request_revision: int,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "method": method.value,
            "requested_method": method.value,
            "request_revision": request_revision,
            "regenerate": regenerate,
        }
        if method == WhatsAppLinkMethod.PAIRING_CODE:
            if phone_number is None:
                message = "pairing code requires a phone number"
                raise ValueError(message)
            payload["phone_number"] = phone_number.lstrip("+")
        return await self.command(
            session_handle=session_handle,
            generation=generation,
            command="request_challenge",
            payload=payload,
        )

    async def send_text(
        self,
        *,
        session_handle: str,
        generation: int,
        target_jid: str,
        body: str,
    ) -> BridgeSendResult:
        payload = await self.command(
            session_handle=session_handle,
            generation=generation,
            command="send_text",
            payload={
                "to": target_jid,
                "text": body,
                "timeout_ms": int(
                    self._settings.whatsapp_bridge_request_timeout_seconds * 1000,
                ),
            },
        )
        try:
            return BridgeSendResult.model_validate(payload)
        except ValidationError as error:
            raise WhatsAppBridgeProtocolError from error

    async def respond_to_rpc(
        self,
        *,
        event: BridgeEventFrame,
        result: object | None = None,
        error_code: str | None = None,
    ) -> None:
        success = error_code is None
        payload: dict[str, object] = {
            "rpc_request_id": event.request_id,
            "success": success,
        }
        if success:
            payload["result"] = result
        else:
            payload["error_code"] = error_code
        await self.command(
            session_handle=event.session_handle,
            generation=event.generation,
            command="rpc_result",
            payload=payload,
        )

    async def command(
        self,
        *,
        session_handle: str,
        generation: int,
        command: str,
        payload: Mapping[str, object] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        process = self._process
        if self._closed or process is None or process.returncode is not None:
            raise WhatsAppBridgeUnavailableError(
                ambiguous=command == "send_text",
            )
        stdin = process.stdin
        if stdin is None:
            raise WhatsAppBridgeUnavailableError(
                ambiguous=command == "send_text",
            )
        if len(self._pending) >= MAX_PENDING_COMMANDS:
            code = "bridge_command_capacity_exceeded"
            raise WhatsAppBridgeError(
                code,
                retryable=True,
            )
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[BridgeResponseFrame] = loop.create_future()
        pending = PendingBridgeCommand(
            session_handle=session_handle,
            generation=generation,
            command=command,
            future=future,
        )
        self._pending[request_id] = pending
        frame = {
            "protocol_version": WHATSAPP_BRIDGE_PROTOCOL_VERSION,
            "frame_type": "command",
            "request_id": request_id,
            "session_handle": session_handle,
            "generation": generation,
            "command": command,
            "payload": dict(payload or {}),
        }
        try:
            encoded = orjson.dumps(frame) + b"\n"
            if len(encoded) > self._settings.whatsapp_ipc_max_frame_bytes:
                raise WhatsAppBridgeProtocolError
            async with self._write_lock:
                stdin.write(encoded)
                await stdin.drain()
            timeout = (
                timeout_seconds
                if timeout_seconds is not None
                else self._settings.whatsapp_bridge_request_timeout_seconds
            )
            response = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as error:
            raise WhatsAppBridgeUnavailableError(
                ambiguous=command == "send_text",
            ) from error
        except (BrokenPipeError, ConnectionError) as error:
            raise WhatsAppBridgeUnavailableError(
                ambiguous=command == "send_text",
            ) from error
        finally:
            self._pending.pop(request_id, None)
        if not response.success:
            bridge_error = response.error
            if bridge_error is None:
                raise WhatsAppBridgeProtocolError
            raise WhatsAppBridgeError(
                bridge_error.code,
                retryable=bridge_error.retryable,
                ambiguous=bridge_error.ambiguous,
            )
        return response.payload or {}

    async def close(self) -> None:
        if self._closed:
            return
        process = self._process
        if process is not None and process.returncode is None:
            try:
                await self.command(
                    session_handle=BRIDGE_SESSION_HANDLE,
                    generation=0,
                    command="shutdown",
                    timeout_seconds=self._settings.whatsapp_worker_shutdown_timeout_seconds,
                )
            except WhatsAppBridgeError:
                pass
        self._closed = True
        self._fail_pending()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._settings.whatsapp_worker_shutdown_timeout_seconds,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
        await _cancel_task(self._reader_task)
        await _cancel_task(self._stderr_task)
        if self._event_tasks:
            for task in tuple(self._event_tasks):
                task.cancel()
            await asyncio.gather(*self._event_tasks, return_exceptions=True)
            self._event_tasks.clear()
        self._event_task_counts.clear()
        self._event_tasks_by_key.clear()
        self._completed_event_keys.clear()
        self._process = None
        self._reader_task = None
        self._stderr_task = None
        self._handshake = None

    async def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                try:
                    line = await process.stdout.readline()
                except ValueError as error:
                    raise WhatsAppBridgeProtocolError from error
                if not line:
                    break
                if len(line) > self._settings.whatsapp_ipc_max_frame_bytes or not line.endswith(b"\n"):
                    raise WhatsAppBridgeProtocolError
                self._handle_frame(line)
        except (
            ValidationError,
            orjson.JSONDecodeError,
            WhatsAppBridgeError,
        ) as error:
            _log_sanitized_failure(
                "WhatsApp bridge protocol reader failed",
                error,
            )
        finally:
            self._fail_pending()

    def _handle_frame(self, line: bytes) -> None:
        raw = orjson.loads(line)
        if not isinstance(raw, dict):
            raise WhatsAppBridgeProtocolError
        frame_type = raw.get("frame_type")
        if frame_type == "response":
            response = BridgeResponseFrame.model_validate(raw)
            pending = self._pending.get(response.request_id)
            if pending is None:
                return
            if response.session_handle != pending.session_handle or response.generation != pending.generation:
                raise WhatsAppBridgeProtocolError
            if not pending.future.done():
                pending.future.set_result(response)
            return
        if frame_type == "event":
            event = BridgeEventFrame.model_validate(raw)
            event_key = (event.session_handle, event.generation, event.request_id)
            if event_key in self._event_tasks_by_key or event_key in self._completed_event_keys:
                # Durable events are delivered at least once. A replay must
                # never repeat a persisted side effect or send a second RPC
                # acknowledgement after the bridge has completed the request.
                return
            session_event_count = self._event_task_counts.get(
                event.session_handle,
                0,
            )
            if event.event not in {"auth_get", "auth_set"} and (
                len(self._event_tasks) >= MAX_EVENT_TASKS or session_event_count >= MAX_EVENT_TASKS_PER_SESSION
            ):
                # Correlated durable events are retried by the bridge when no
                # acknowledgement arrives. Isolate overload to this session
                # instead of terminating the shared bridge reader.
                return
            task = asyncio.create_task(
                self._dispatch_event(event),
                name=f"whatsapp-bridge-event-{event.event}",
            )
            self._event_tasks.add(task)
            self._event_tasks_by_key[event_key] = task
            self._event_task_counts[event.session_handle] = session_event_count + 1
            task.add_done_callback(
                partial(self._event_completed, event, event_key),
            )
            return
        raise WhatsAppBridgeProtocolError

    async def _dispatch_event(self, event: BridgeEventFrame) -> None:
        await self._event_handler(event)

    def _event_completed(
        self,
        event: BridgeEventFrame,
        event_key: BridgeEventKey,
        task: asyncio.Task[None],
    ) -> None:
        self._event_tasks.discard(task)
        self._event_tasks_by_key.pop(event_key, None)
        remaining = self._event_task_counts.get(event.session_handle, 1) - 1
        if remaining <= 0:
            self._event_task_counts.pop(event.session_handle, None)
        else:
            self._event_task_counts[event.session_handle] = remaining
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log_sanitized_failure(
                "WhatsApp bridge event handling failed",
                error,
                event=event.event,
                event_request_id=event.request_id,
            )
            return
        self._completed_event_keys[event_key] = None
        if len(self._completed_event_keys) > MAX_COMPLETED_EVENT_REQUESTS:
            self._completed_event_keys.popitem(last=False)

    async def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        while True:
            line = await process.stderr.readline()
            if not line:
                return
            logger.warning("WhatsApp bridge emitted a diagnostic")

    def _fail_pending(self) -> None:
        for pending in self._pending.values():
            if not pending.future.done():
                pending.future.set_exception(
                    WhatsAppBridgeUnavailableError(
                        ambiguous=pending.command == "send_text",
                    ),
                )


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

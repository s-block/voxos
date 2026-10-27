import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import orjson
from fastapi import WebSocket
from livekit import rtc
from sqlmodel import col, select
from starlette.websockets import WebSocketDisconnect

from voxos.auth.models import AuthenticatedUser
from voxos.db.models import utc_now
from voxos.integrations.errors import IntegrationResourceError, IntegrationWebhookError
from voxos.integrations.models import (
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationProviderType,
    IntegrationResourceStatus,
)
from voxos.integrations.providers.livekit_audio import (
    phone_audio_track_publish_options,
)
from voxos.integrations.providers.telnyx.webhooks import (
    TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME,
)
from voxos.integrations.providers.twilio.codec import (
    pcm16_bytes_to_ulaw_bytes,
    ulaw_bytes_to_pcm16_bytes,
)
from voxos.integrations.records import IntegrationCallSessionRecord, IntegrationResourceRecord
from voxos.integrations.services import (
    IntegrationWebhookRouteContext,
    UserIntegrationService,
)
from voxos.voice.call_routing import LiveKitCallRouter
from voxos.voice.livekit_tokens import (
    LiveKitVoiceTokenRequest,
    ResolvedLiveKitCredentials,
)

TELNYX_MEDIA_SAMPLE_RATE = 8000
TELNYX_MEDIA_CHANNELS = 1
TELNYX_MEDIA_FRAME_DURATION_MS = 20
TELNYX_MEDIA_SAMPLES_PER_FRAME = TELNYX_MEDIA_SAMPLE_RATE * TELNYX_MEDIA_FRAME_DURATION_MS // 1000
TELNYX_MEDIA_PCM_BYTES_PER_FRAME = TELNYX_MEDIA_SAMPLES_PER_FRAME * TELNYX_MEDIA_CHANNELS * 2
TELNYX_MEDIA_PCMU_BYTES_PER_FRAME = TELNYX_MEDIA_SAMPLES_PER_FRAME * TELNYX_MEDIA_CHANNELS
TELNYX_MEDIA_CODEC = "PCMU"
TELNYX_MEDIA_ERROR_TITLE_MAX_LENGTH = 128
TELNYX_AUDIO_TRACK_NAME = "telnyx-caller-audio"
TELNYX_PARTICIPANT_PREFIX = "telnyx"
CALL_SESSION_ROUTE_METADATA_ERROR = "Call session is missing LiveKit route metadata."
CALL_SESSION_ROOM_ERROR = "Call session is missing a LiveKit room."
STREAM_ROUTE_RESOURCE_MISSING_ERROR = "Telnyx media stream route is not bound to a resource."
STREAM_RESOURCE_MISMATCH_ERROR = "Telnyx media stream resource is invalid."
STREAM_AUTH_TOKEN_INVALID_ERROR = "Telnyx media stream auth token is invalid."
STREAM_FRAME_TYPE_ERROR = "Telnyx stream frame must be a JSON object."
STREAM_START_ORDER_ERROR = "Telnyx stream did not start with a start frame."
STREAM_START_FRAME_ERROR = "Telnyx start frame is invalid."
STREAM_ID_MISSING_ERROR = "Telnyx stream ID is missing."
STREAM_CALL_CONTROL_ID_MISSING_ERROR = "Telnyx call control ID is missing."
STREAM_CALL_SESSION_MISMATCH_ERROR = "Telnyx stream does not match a call session."
STREAM_CALL_SESSION_LOOKUP_ATTEMPTS = 20
STREAM_CALL_SESSION_LOOKUP_DELAY_SECONDS = 0.1
MEDIA_BRIDGE_START_FAILURE_CODE = "media_bridge_start_failed"
MEDIA_DOWNLINK_FAILURE_CODE = "media_downlink_failed"
MEDIA_PROTOCOL_FAILURE_CODE = "media_stream_protocol_error"
MEDIA_DOWNLINK_FRAME_ERROR = "Telnyx media downlink frame is invalid."
MEDIA_DOWNLINK_ERROR = "Telnyx media downlink failed."
MEDIA_PROTOCOL_ERROR = "Telnyx media stream reported a protocol error."

logger = logging.getLogger(__name__)


class TelnyxMediaDownlinkError(RuntimeError):
    pass


class TelnyxMediaProtocolError(IntegrationWebhookError):
    def __init__(
        self,
        *,
        code: int | None,
        title: str | None,
    ) -> None:
        super().__init__(MEDIA_PROTOCOL_ERROR)
        self.code = code
        self.title = title


class TelnyxAudioBridgeSession(Protocol):
    async def publish_telnyx_payload(self, payload: str) -> None:
        """Publish a base64 encoded Telnyx media payload into LiveKit."""

    async def close(self) -> None:
        """Close bridge resources."""

    async def wait_for_downlink_failure(self) -> None:
        """Raise when LiveKit-to-Telnyx audio forwarding fails."""


class TelnyxAudioBridgeAdapter(Protocol):
    async def connect(
        self,
        *,
        websocket: WebSocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TelnyxAudioBridgeSession:
        """Connect a Telnyx stream to the runtime voice transport."""


@dataclass(slots=True)
class LiveKitTelnyxAudioBridgeSession:
    room: rtc.Room
    audio_source: rtc.AudioSource
    websocket: WebSocket
    call_session_id: str
    runtime_session_id: str
    stream_id: str
    outbound_tasks: set[asyncio.Task[None]] = field(default_factory=set)
    downlink_failures: asyncio.Queue[BaseException] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1),
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _uplink_started: bool = field(default=False, init=False, repr=False)
    _downlink_started: bool = field(default=False, init=False, repr=False)

    async def publish_telnyx_payload(self, payload: str) -> None:
        ulaw_payload = base64.b64decode(payload)
        pcm_payload = ulaw_bytes_to_pcm16_bytes(ulaw_payload)
        frame = rtc.AudioFrame.create(
            TELNYX_MEDIA_SAMPLE_RATE,
            TELNYX_MEDIA_CHANNELS,
            len(pcm_payload) // 2,
        )
        frame.data.cast("B")[:] = pcm_payload
        await self.audio_source.capture_frame(frame)
        if not self._uplink_started:
            self._uplink_started = True
            logger.info(
                "Started Telnyx PCMU media uplink",
                extra={
                    "call_session_id": self.call_session_id,
                    "runtime_session_id": self.runtime_session_id,
                    "stream_id": self.stream_id,
                    "codec": TELNYX_MEDIA_CODEC,
                    "sample_rate": TELNYX_MEDIA_SAMPLE_RATE,
                    "encoded_frame_bytes": len(ulaw_payload),
                },
            )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self.outbound_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.outbound_tasks.clear()
        try:
            await self.audio_source.aclose()
        finally:
            await self.room.disconnect()

    async def wait_for_downlink_failure(self) -> None:
        error = await self.downlink_failures.get()
        raise error

    def add_remote_track(self, track: object) -> None:
        if self._closed or not isinstance(track, rtc.RemoteAudioTrack):
            return
        task = asyncio.create_task(self._forward_remote_audio(track))
        self.outbound_tasks.add(task)
        task.add_done_callback(self._handle_outbound_task_done)

    def _handle_outbound_task_done(self, task: asyncio.Task[None]) -> None:
        self.outbound_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is None or not self.downlink_failures.empty():
            return
        self.downlink_failures.put_nowait(error)

    async def _forward_remote_audio(self, track: rtc.RemoteAudioTrack) -> None:
        audio_stream = rtc.AudioStream.from_track(
            track=track,
            sample_rate=TELNYX_MEDIA_SAMPLE_RATE,
            num_channels=TELNYX_MEDIA_CHANNELS,
            frame_size_ms=TELNYX_MEDIA_FRAME_DURATION_MS,
        )
        try:
            async for audio_event in audio_stream:
                await self.websocket.send_text(
                    telnyx_downlink_media_message(audio_event.frame),
                )
                if not self._downlink_started:
                    self._downlink_started = True
                    logger.info(
                        "Started Telnyx PCMU media downlink",
                        extra={
                            "call_session_id": self.call_session_id,
                            "runtime_session_id": self.runtime_session_id,
                            "stream_id": self.stream_id,
                            "codec": TELNYX_MEDIA_CODEC,
                            "sample_rate": TELNYX_MEDIA_SAMPLE_RATE,
                            "frame_duration_ms": (TELNYX_MEDIA_FRAME_DURATION_MS),
                            "encoded_frame_bytes": (TELNYX_MEDIA_PCMU_BYTES_PER_FRAME),
                        },
                    )
        except WebSocketDisconnect:
            return
        except TelnyxMediaDownlinkError:
            raise
        except Exception as exc:
            raise TelnyxMediaDownlinkError(MEDIA_DOWNLINK_ERROR) from exc
        finally:
            await audio_stream.aclose()


@dataclass(frozen=True, slots=True)
class LiveKitTelnyxAudioBridgeAdapter:
    livekit_url: str = field(repr=False)
    credentials: ResolvedLiveKitCredentials
    call_router: LiveKitCallRouter
    voice_agent_name: str

    async def connect(
        self,
        *,
        websocket: WebSocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TelnyxAudioBridgeSession:
        if call_session.agent_id is None or call_session.runtime_session_id is None:
            raise IntegrationWebhookError(CALL_SESSION_ROUTE_METADATA_ERROR)
        if call_session.livekit_room_name is None:
            raise IntegrationWebhookError(CALL_SESSION_ROOM_ERROR)
        is_predispatched_outbound = (
            call_session.direction == IntegrationDirection.OUTBOUND.value
            and call_session.media_participant_identity is not None
        )
        participant_identity = call_session.media_participant_identity if is_predispatched_outbound else None
        participant_name = participant_identity or (f"{TELNYX_PARTICIPANT_PREFIX}-{call_session.external_call_id}")
        token = await self.call_router.create_join_token(
            authenticated_user=AuthenticatedUser(
                user_id=user_id,
                firebase_user_id=f"telnyx:{user_id}",
            ),
            credentials=self.credentials,
            request=LiveKitVoiceTokenRequest(
                room_name=call_session.livekit_room_name,
                participant_identity=participant_identity,
                participant_name=participant_name,
                agent_name=(None if is_predispatched_outbound else self.voice_agent_name),
                agent_id=call_session.agent_id,
                runtime_session_id=call_session.runtime_session_id,
            ),
            source=IntegrationProviderType.TELNYX.value,
        )
        room = rtc.Room()
        audio_source = rtc.AudioSource(
            sample_rate=TELNYX_MEDIA_SAMPLE_RATE,
            num_channels=TELNYX_MEDIA_CHANNELS,
        )
        local_track = rtc.LocalAudioTrack.create_audio_track(
            TELNYX_AUDIO_TRACK_NAME,
            audio_source,
        )
        try:
            await room.connect(self.livekit_url, token.token)
            await room.local_participant.publish_track(
                local_track,
                phone_audio_track_publish_options(),
            )
        except BaseException:
            await asyncio.gather(
                audio_source.aclose(),
                room.disconnect(),
                return_exceptions=True,
            )
            raise
        session = LiveKitTelnyxAudioBridgeSession(
            room=room,
            audio_source=audio_source,
            websocket=websocket,
            call_session_id=call_session.id,
            runtime_session_id=call_session.runtime_session_id,
            stream_id=call_session.media_stream_id or "",
        )

        def on_track_subscribed(*args: object) -> None:
            if not args:
                return
            session.add_remote_track(args[0])

        room.on("track_subscribed", on_track_subscribed)
        return session


@dataclass(frozen=True, slots=True)
class TelnyxMediaStreamContext:
    user_id: str
    integration_id: str
    resource_id: str
    call_control_id: str
    call_session: IntegrationCallSessionRecord


@dataclass(frozen=True, slots=True)
class TelnyxMediaStreamBridge:
    service: UserIntegrationService
    audio_bridge_factory: Callable[
        [str],
        Awaitable[TelnyxAudioBridgeAdapter | None],
    ]

    async def handle_websocket(
        self,
        *,
        websocket: WebSocket,
        route_context: IntegrationWebhookRouteContext,
        outbound_call_session_id: str | None = None,
    ) -> None:
        try:
            resource, expected_stream_auth_token = await self._authenticated_resource(
                route_context=route_context,
            )
        except IntegrationWebhookError:
            await websocket.close(code=1008)
            return

        header_token = websocket.headers.get(TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME)
        await websocket.accept()
        connected_frame, start_frame = await self._receive_start_frame(websocket)
        verify_stream_auth_token(
            expected=expected_stream_auth_token,
            header_token=header_token,
            connected_frame=connected_frame,
        )
        call_control_id = call_control_id_from_start_frame(start_frame)
        if outbound_call_session_id is None:
            call_session = await self._call_session(
                user_id=route_context.route.user_id,
                integration_id=route_context.route.integration_id,
                resource_id=resource.id,
                call_control_id=call_control_id,
            )
        else:
            try:
                call_session = await self.service.bind_outbound_call_session(
                    user_id=route_context.route.user_id,
                    integration_id=route_context.route.integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.TELNYX,
                    call_session_id=outbound_call_session_id,
                    external_call_id=call_control_id,
                    payload_json=safe_start_frame(start_frame),
                )
            except IntegrationResourceError as exc:
                raise IntegrationWebhookError(str(exc)) from exc
        stream_context = TelnyxMediaStreamContext(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            call_control_id=call_control_id,
            call_session=call_session,
        )
        stream_id = stream_id_from_start_frame(start_frame)
        call_session.media_stream_id = stream_id
        call_session.updated_at = utc_now()
        await self.service.record_communication_event(
            user_id=stream_context.user_id,
            integration_id=stream_context.integration_id,
            resource_id=stream_context.resource_id,
            provider=IntegrationProviderType.TELNYX,
            event_type="media_stream_start",
            direction=call_direction_from_session(call_session),
            external_event_id=stream_id,
            idempotency_key=(f"telnyx:{stream_context.integration_id}:stream:{stream_id}:start"),
            payload_json=safe_start_frame(start_frame),
            safe_summary_json={
                "stream_id": stream_id,
                "call_control_id": stream_context.call_control_id,
            },
        )
        await self.service.session.commit()
        try:
            audio_bridge = await self.audio_bridge_factory(stream_context.user_id)
            if audio_bridge is not None:
                bridge_session = await audio_bridge.connect(
                    websocket=websocket,
                    call_session=call_session,
                    user_id=stream_context.user_id,
                )
                self.service.mark_call_media_ready(call_session)
                await self.service.session.commit()
        except BaseException:
            await self.service.fail_call_route(
                call_session,
                failure_code=MEDIA_BRIDGE_START_FAILURE_CODE,
            )
            raise
        if audio_bridge is None:
            await self.service.fail_call_route(
                call_session,
                failure_code=MEDIA_BRIDGE_START_FAILURE_CODE,
            )
            await websocket.close(code=1011)
            return
        try:
            await self._run_media_session(
                websocket=websocket,
                stream_context=stream_context,
                bridge_session=bridge_session,
            )
        except WebSocketDisconnect:
            logger.info(
                "Telnyx media stream disconnected",
                extra=media_log_context(stream_context),
            )
        except TelnyxMediaProtocolError as exc:
            logger.warning(
                "Telnyx media stream reported a protocol error",
                extra={
                    **media_log_context(stream_context),
                    "protocol_error_code": exc.code,
                    "protocol_error_title": exc.title,
                },
            )
            await self.service.fail_call_route(
                call_session,
                failure_code=MEDIA_PROTOCOL_FAILURE_CODE,
            )
            await close_websocket_safely(websocket, code=1011)
        except TelnyxMediaDownlinkError:
            logger.exception(
                "Telnyx media downlink failed",
                extra=media_log_context(stream_context),
            )
            await self.service.fail_call_route(
                call_session,
                failure_code=MEDIA_DOWNLINK_FAILURE_CODE,
            )
            await close_websocket_safely(websocket, code=1011)
        finally:
            await bridge_session.close()
            await self._mark_stream_closed(stream_context=stream_context)

    async def _run_media_session(
        self,
        *,
        websocket: WebSocket,
        stream_context: TelnyxMediaStreamContext,
        bridge_session: TelnyxAudioBridgeSession,
    ) -> None:
        frame_task = asyncio.create_task(
            self._process_frames(
                websocket=websocket,
                stream_context=stream_context,
                bridge_session=bridge_session,
            ),
            name="telnyx-media-uplink",
        )
        downlink_failure_task = asyncio.create_task(
            bridge_session.wait_for_downlink_failure(),
            name="telnyx-media-downlink-health",
        )
        tasks = (frame_task, downlink_failure_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if downlink_failure_task in done:
                await downlink_failure_task
            await frame_task
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _authenticated_resource(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
    ) -> tuple[IntegrationResourceRecord, str]:
        active_integration = await self.service.integration_is_connected(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        if not active_integration:
            raise IntegrationWebhookError(STREAM_RESOURCE_MISMATCH_ERROR)
        resource_id = route_context.route.resource_id
        if resource_id is None:
            raise IntegrationWebhookError(STREAM_ROUTE_RESOURCE_MISSING_ERROR)
        resource = await self.service.session.get(IntegrationResourceRecord, resource_id)
        if (
            resource is None
            or resource.user_id != route_context.route.user_id
            or resource.integration_id != route_context.route.integration_id
            or resource.provider != IntegrationProviderType.TELNYX.value
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise IntegrationWebhookError(STREAM_RESOURCE_MISMATCH_ERROR)
        credentials = await self.service.load_telnyx_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        return resource, credentials.stream_auth_token.get_secret_value()

    async def _call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        call_control_id: str,
    ) -> IntegrationCallSessionRecord:
        for attempt in range(STREAM_CALL_SESSION_LOOKUP_ATTEMPTS):
            statement = select(IntegrationCallSessionRecord).where(
                col(IntegrationCallSessionRecord.user_id) == user_id,
                col(IntegrationCallSessionRecord.integration_id) == integration_id,
                col(IntegrationCallSessionRecord.resource_id) == resource_id,
                col(IntegrationCallSessionRecord.provider) == IntegrationProviderType.TELNYX.value,
                col(IntegrationCallSessionRecord.external_call_id) == call_control_id,
            )
            result = await self.service.session.exec(statement)
            record = result.one_or_none()
            if record is not None:
                return record
            if attempt < STREAM_CALL_SESSION_LOOKUP_ATTEMPTS - 1:
                await asyncio.sleep(STREAM_CALL_SESSION_LOOKUP_DELAY_SECONDS)
        raise IntegrationWebhookError(STREAM_CALL_SESSION_MISMATCH_ERROR)

    async def _receive_start_frame(
        self,
        websocket: WebSocket,
    ) -> tuple[dict[str, object] | None, dict[str, object]]:
        connected_frame: dict[str, object] | None = None
        while True:
            payload = orjson.loads(await websocket.receive_text())
            if not isinstance(payload, dict):
                raise IntegrationWebhookError(STREAM_FRAME_TYPE_ERROR)
            event = payload.get("event")
            if event == "connected":
                connected_frame = payload
                continue
            if event != "start":
                raise IntegrationWebhookError(STREAM_START_ORDER_ERROR)
            start = payload.get("start")
            if not isinstance(start, dict):
                raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
            return connected_frame, payload

    async def _process_frames(
        self,
        *,
        websocket: WebSocket,
        stream_context: TelnyxMediaStreamContext,
        bridge_session: TelnyxAudioBridgeSession,
    ) -> None:
        while True:
            payload = orjson.loads(await websocket.receive_text())
            if not isinstance(payload, dict):
                continue
            event = payload.get("event")
            if event == "media":
                media = payload.get("media")
                if isinstance(media, dict):
                    media_payload = media.get("payload")
                    if isinstance(media_payload, str):
                        await bridge_session.publish_telnyx_payload(media_payload)
                continue
            if event == "error":
                error = telnyx_media_protocol_error(payload)
                safe_error = safe_error_frame(payload)
                stream_id = string_value(payload, "stream_id") or stream_context.call_session.media_stream_id
                await self.service.record_communication_event(
                    user_id=stream_context.user_id,
                    integration_id=stream_context.integration_id,
                    resource_id=stream_context.resource_id,
                    provider=IntegrationProviderType.TELNYX,
                    event_type="media_stream_error",
                    direction=IntegrationDirection.UNKNOWN,
                    external_event_id=stream_id,
                    idempotency_key=(
                        f"telnyx:{stream_context.integration_id}:stream:"
                        f"{stream_id or 'unknown'}:error:{error.code or 'unknown'}"
                    ),
                    payload_json=safe_error,
                    safe_summary_json=safe_error,
                    status=IntegrationEventStatus.FAILED,
                )
                raise error
            if event == "stop":
                stream_id = string_value(payload, "stream_id") or stream_context.call_session.media_stream_id
                await self.service.record_communication_event(
                    user_id=stream_context.user_id,
                    integration_id=stream_context.integration_id,
                    resource_id=stream_context.resource_id,
                    provider=IntegrationProviderType.TELNYX,
                    event_type="media_stream_stop",
                    direction=IntegrationDirection.UNKNOWN,
                    external_event_id=stream_id,
                    idempotency_key=(f"telnyx:{stream_context.integration_id}:stream:{stream_id or 'unknown'}:stop"),
                    payload_json=safe_stop_frame(payload),
                    safe_summary_json={"stream_id": stream_id},
                )
                return

    async def _mark_stream_closed(
        self,
        *,
        stream_context: TelnyxMediaStreamContext,
    ) -> None:
        stream_context.call_session.updated_at = utc_now()
        await self.service.session.commit()


def verify_stream_auth_token(
    *,
    expected: str,
    header_token: str | None,
    connected_frame: dict[str, object] | None,
) -> None:
    connected_token = string_value(connected_frame, "stream_auth_token") if connected_frame is not None else None
    if header_token == expected or connected_token == expected:
        return
    raise IntegrationWebhookError(STREAM_AUTH_TOKEN_INVALID_ERROR)


def stream_id_from_start_frame(start_frame: dict[str, object]) -> str:
    stream_id = string_value(start_frame, "stream_id")
    start = start_frame.get("start")
    if stream_id is None and isinstance(start, dict):
        stream_id = string_value(start, "stream_id")
    if stream_id is None:
        raise IntegrationWebhookError(STREAM_ID_MISSING_ERROR)
    return stream_id


def call_control_id_from_start_frame(start_frame: dict[str, object]) -> str:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
    call_control_id = string_value(start, "call_control_id")
    if call_control_id is None:
        raise IntegrationWebhookError(STREAM_CALL_CONTROL_ID_MISSING_ERROR)
    return call_control_id


def safe_start_frame(start_frame: dict[str, object]) -> dict[str, object]:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        return {}
    return {
        "event": "start",
        "stream_id": string_value(start_frame, "stream_id") or string_value(start, "stream_id"),
        "call_control_id": string_value(start, "call_control_id"),
        "call_session_id": string_value(start, "call_session_id"),
        "from": string_value(start, "from"),
        "to": string_value(start, "to"),
        "media_format": start.get("media_format"),
    }


def safe_stop_frame(stop_frame: dict[str, object]) -> dict[str, object]:
    return {
        "event": "stop",
        "stream_id": string_value(stop_frame, "stream_id"),
    }


def safe_error_frame(error_frame: dict[str, object]) -> dict[str, object]:
    error_payload = error_frame.get("payload")
    payload = error_payload if isinstance(error_payload, dict) else None
    return {
        "event": "error",
        "stream_id": string_value(error_frame, "stream_id"),
        "code": integer_value(payload, "code"),
        "title": bounded_string_value(
            payload,
            "title",
            max_length=TELNYX_MEDIA_ERROR_TITLE_MAX_LENGTH,
        ),
    }


def telnyx_media_protocol_error(
    error_frame: dict[str, object],
) -> TelnyxMediaProtocolError:
    safe_error = safe_error_frame(error_frame)
    code = safe_error["code"]
    title = safe_error["title"]
    return TelnyxMediaProtocolError(
        code=code if isinstance(code, int) else None,
        title=title if isinstance(title, str) else None,
    )


def telnyx_downlink_media_message(frame: rtc.AudioFrame) -> str:
    pcm_payload = frame.data.cast("B").tobytes()
    if (
        frame.sample_rate != TELNYX_MEDIA_SAMPLE_RATE
        or frame.num_channels != TELNYX_MEDIA_CHANNELS
        or frame.samples_per_channel != TELNYX_MEDIA_SAMPLES_PER_FRAME
        or len(pcm_payload) != TELNYX_MEDIA_PCM_BYTES_PER_FRAME
    ):
        raise TelnyxMediaDownlinkError(MEDIA_DOWNLINK_FRAME_ERROR)
    ulaw_payload = pcm16_bytes_to_ulaw_bytes(pcm_payload)
    if len(ulaw_payload) != TELNYX_MEDIA_PCMU_BYTES_PER_FRAME:
        raise TelnyxMediaDownlinkError(MEDIA_DOWNLINK_FRAME_ERROR)
    return orjson.dumps(
        {
            "event": "media",
            "media": {
                "payload": base64.b64encode(ulaw_payload).decode("ascii"),
            },
        },
    ).decode()


def call_direction_from_session(
    call_session: IntegrationCallSessionRecord,
) -> IntegrationDirection:
    return IntegrationDirection(call_session.direction)


def livekit_telnyx_audio_bridge_factory(
    *,
    call_router: LiveKitCallRouter,
    livekit_settings_resolver: Callable[[str], Awaitable[ResolvedLiveKitCredentials]],
    voice_agent_name: str,
) -> Callable[[str], Awaitable[TelnyxAudioBridgeAdapter | None]]:
    async def factory(user_id: str) -> TelnyxAudioBridgeAdapter | None:
        settings = await livekit_settings_resolver(user_id)
        return LiveKitTelnyxAudioBridgeAdapter(
            livekit_url=settings.livekit_url,
            credentials=settings,
            call_router=call_router,
            voice_agent_name=voice_agent_name,
        )

    return factory


def string_value(data: dict[str, object] | None, key: str) -> str | None:
    if data is None:
        return None
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def bounded_string_value(
    data: dict[str, object] | None,
    key: str,
    *,
    max_length: int,
) -> str | None:
    value = string_value(data, key)
    return value[:max_length] if value is not None else None


def integer_value(data: dict[str, object] | None, key: str) -> int | None:
    if data is None:
        return None
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def media_log_context(
    stream_context: TelnyxMediaStreamContext,
) -> dict[str, str | None]:
    return {
        "call_session_id": stream_context.call_session.id,
        "runtime_session_id": stream_context.call_session.runtime_session_id,
        "stream_id": stream_context.call_session.media_stream_id,
    }


async def close_websocket_safely(websocket: WebSocket, *, code: int) -> None:
    try:
        await websocket.close(code=code)
    except (RuntimeError, WebSocketDisconnect):
        return

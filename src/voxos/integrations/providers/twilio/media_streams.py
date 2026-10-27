import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

import orjson
from fastapi import WebSocket
from livekit import rtc
from sqlmodel import col, select

from voxos.auth.models import AuthenticatedUser
from voxos.db.models import utc_now
from voxos.integrations.errors import IntegrationWebhookError
from voxos.integrations.models import (
    IntegrationCallStatus,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationWebhookPurpose,
)
from voxos.integrations.providers.base import WebhookVerificationRequest
from voxos.integrations.providers.livekit_audio import (
    phone_audio_track_publish_options,
)
from voxos.integrations.providers.twilio.codec import (
    pcm16_bytes_to_ulaw_bytes,
    ulaw_bytes_to_pcm16_bytes,
)
from voxos.integrations.providers.twilio.webhooks import TwilioWebhookVerifier
from voxos.integrations.records import IntegrationCallSessionRecord, IntegrationResourceRecord
from voxos.integrations.services import (
    IntegrationWebhookRouteContext,
    UserIntegrationService,
    media_stream_url_for_resource,
)
from voxos.voice.call_routing import LiveKitCallRouter
from voxos.voice.errors import (
    InvalidLiveKitCredentialsError,
    MissingLiveKitCredentialsError,
    MissingVoiceRouteSignerError,
    VoiceWorkerRegistrationError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.livekit_tokens import (
    LiveKitVoiceTokenRequest,
    ResolvedLiveKitCredentials,
)

TWILIO_MEDIA_SAMPLE_RATE = 8000
TWILIO_MEDIA_CHANNELS = 1
TWILIO_AUDIO_TRACK_NAME = "twilio-caller-audio"
TWILIO_PARTICIPANT_PREFIX = "twilio"
CALL_SESSION_ROUTE_METADATA_ERROR = "Call session is missing LiveKit route metadata."
CALL_SESSION_ROOM_ERROR = "Call session is missing a LiveKit room."
STREAM_CALL_SESSION_MISMATCH_ERROR = "Twilio stream does not match a call session."
STREAM_ROUTE_RESOURCE_MISSING_ERROR = "Twilio media stream route is not bound to a resource."
STREAM_RESOURCE_MISMATCH_ERROR = "Twilio media stream resource is invalid."
STREAM_SIGNATURE_INVALID_ERROR = "Twilio media stream signature is invalid."
STREAM_FRAME_TYPE_ERROR = "Twilio stream frame must be a JSON object."
STREAM_START_ORDER_ERROR = "Twilio stream did not start with a start frame."
STREAM_START_FRAME_ERROR = "Twilio start frame is invalid."
STREAM_ACCOUNT_MISMATCH_ERROR = "Twilio stream account does not match the integration."
STREAM_SID_MISSING_ERROR = "Twilio stream SID is missing."
STREAM_CALL_SID_MISSING_ERROR = "Twilio stream CallSid is missing."
MEDIA_BRIDGE_START_FAILURE_CODE = "media_bridge_start_failed"


class TwilioAudioBridgeSession(Protocol):
    async def publish_twilio_payload(self, payload: str) -> None:
        """Publish a base64 encoded Twilio media payload into LiveKit."""

    async def close(self) -> None:
        """Close bridge resources."""


class TwilioAudioBridgeAdapter(Protocol):
    async def connect(
        self,
        *,
        websocket: WebSocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TwilioAudioBridgeSession:
        """Connect a Twilio stream to the runtime voice transport."""


@dataclass(slots=True)
class LiveKitTwilioAudioBridgeSession:
    room: rtc.Room
    audio_source: rtc.AudioSource
    stream_sid: str
    websocket: WebSocket
    outbound_tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def publish_twilio_payload(self, payload: str) -> None:
        ulaw_payload = base64.b64decode(payload)
        pcm_payload = ulaw_bytes_to_pcm16_bytes(ulaw_payload)
        frame = rtc.AudioFrame.create(
            TWILIO_MEDIA_SAMPLE_RATE,
            TWILIO_MEDIA_CHANNELS,
            len(pcm_payload) // 2,
        )
        frame.data.cast("B")[:] = pcm_payload
        await self.audio_source.capture_frame(frame)

    async def close(self) -> None:
        for task in self.outbound_tasks:
            task.cancel()
        if self.outbound_tasks:
            await asyncio.gather(*self.outbound_tasks, return_exceptions=True)
        await self.audio_source.aclose()
        await self.room.disconnect()

    def add_remote_track(self, track: object) -> None:
        if not isinstance(track, rtc.RemoteAudioTrack):
            return
        task = asyncio.create_task(self._forward_remote_audio(track))
        self.outbound_tasks.add(task)
        task.add_done_callback(self.outbound_tasks.discard)

    async def _forward_remote_audio(self, track: rtc.RemoteAudioTrack) -> None:
        audio_stream = rtc.AudioStream.from_track(
            track=track,
            sample_rate=TWILIO_MEDIA_SAMPLE_RATE,
            num_channels=TWILIO_MEDIA_CHANNELS,
        )
        try:
            async for audio_event in audio_stream:
                frame = audio_event.frame
                pcm_payload = frame.data.cast("B").tobytes()
                ulaw_payload = pcm16_bytes_to_ulaw_bytes(pcm_payload)
                await self.websocket.send_text(
                    orjson.dumps(
                        {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {
                                "payload": base64.b64encode(ulaw_payload).decode("ascii"),
                            },
                        },
                    ).decode(),
                )
        finally:
            await audio_stream.aclose()


@dataclass(frozen=True, slots=True)
class LiveKitTwilioAudioBridgeAdapter:
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
    ) -> TwilioAudioBridgeSession:
        if call_session.agent_id is None or call_session.runtime_session_id is None:
            raise IntegrationWebhookError(CALL_SESSION_ROUTE_METADATA_ERROR)
        if call_session.livekit_room_name is None:
            raise IntegrationWebhookError(CALL_SESSION_ROOM_ERROR)
        is_predispatched_outbound = (
            call_session.direction == IntegrationDirection.OUTBOUND.value
            and call_session.media_participant_identity is not None
        )
        participant_identity = call_session.media_participant_identity if is_predispatched_outbound else None
        participant_name = participant_identity or (f"{TWILIO_PARTICIPANT_PREFIX}-{call_session.external_call_id}")
        token = await self.call_router.create_join_token(
            authenticated_user=AuthenticatedUser(
                user_id=user_id,
                firebase_user_id=f"twilio:{user_id}",
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
            source=IntegrationProviderType.TWILIO.value,
        )
        room = rtc.Room()
        audio_source = rtc.AudioSource(
            sample_rate=TWILIO_MEDIA_SAMPLE_RATE,
            num_channels=TWILIO_MEDIA_CHANNELS,
        )
        local_track = rtc.LocalAudioTrack.create_audio_track(
            TWILIO_AUDIO_TRACK_NAME,
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
        session = LiveKitTwilioAudioBridgeSession(
            room=room,
            audio_source=audio_source,
            stream_sid=call_session.media_stream_id or "",
            websocket=websocket,
        )

        def on_track_subscribed(*args: object) -> None:
            if not args:
                return
            session.add_remote_track(args[0])

        room.on("track_subscribed", on_track_subscribed)
        return session


@dataclass(frozen=True, slots=True)
class TwilioMediaStreamContext:
    user_id: str
    integration_id: str
    resource_id: str
    account_sid: str
    call_sid: str
    call_session: IntegrationCallSessionRecord


@dataclass(frozen=True, slots=True)
class TwilioMediaStreamBridge:
    service: UserIntegrationService
    audio_bridge_factory: Callable[
        [str],
        Awaitable[TwilioAudioBridgeAdapter | None],
    ]

    async def handle_websocket(
        self,
        *,
        websocket: WebSocket,
        route_context: IntegrationWebhookRouteContext,
        signature: str | None,
    ) -> None:
        try:
            resource, account_sid = await self._authenticated_resource(
                route_context=route_context,
                signature=signature,
            )
        except IntegrationWebhookError:
            await websocket.close(code=1008)
            return

        await websocket.accept()
        start_frame = await self._receive_start_frame(websocket)
        verify_start_frame_account(start_frame=start_frame, account_sid=account_sid)
        call_sid = call_sid_from_start_frame(start_frame)
        call_session = await self._call_session(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            call_sid=call_sid,
        )
        stream_context = TwilioMediaStreamContext(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            account_sid=account_sid,
            call_sid=call_sid,
            call_session=call_session,
        )
        stream_sid = stream_sid_from_start_frame(start_frame)
        call_session.media_stream_id = stream_sid
        call_session.status = IntegrationCallStatus.IN_PROGRESS.value
        call_session.updated_at = utc_now()
        await self.service.record_twilio_event(
            user_id=stream_context.user_id,
            integration_id=stream_context.integration_id,
            resource_id=stream_context.resource_id,
            event_type="media_stream_start",
            direction=call_direction_from_session(call_session),
            external_event_id=stream_sid,
            idempotency_key=(f"twilio:{stream_context.account_sid}:stream:{stream_sid}:start"),
            payload_json=safe_start_frame(start_frame),
            safe_summary_json={
                "stream_sid": stream_sid,
                "call_sid": stream_context.call_sid,
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
            await self._process_frames(
                websocket=websocket,
                stream_context=stream_context,
                bridge_session=bridge_session,
            )
        finally:
            await bridge_session.close()
            await self._mark_stream_closed(
                stream_context=stream_context,
            )

    async def _authenticated_resource(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        signature: str | None,
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
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise IntegrationWebhookError(STREAM_RESOURCE_MISMATCH_ERROR)
        credentials = await self.service.load_twilio_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        result = TwilioWebhookVerifier().validate_webhook(
            WebhookVerificationRequest(
                provider=IntegrationProviderType.TWILIO,
                purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
                expected_url=media_stream_url_for_resource(resource),
                params={},
                signature=signature,
                signing_secret=credentials.auth_token,
            ),
        )
        if not result.valid:
            raise IntegrationWebhookError(STREAM_SIGNATURE_INVALID_ERROR)
        return resource, credentials.account_sid

    async def _call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        call_sid: str,
    ) -> IntegrationCallSessionRecord:
        statement = select(IntegrationCallSessionRecord).where(
            col(IntegrationCallSessionRecord.user_id) == user_id,
            col(IntegrationCallSessionRecord.integration_id) == integration_id,
            col(IntegrationCallSessionRecord.resource_id) == resource_id,
            col(IntegrationCallSessionRecord.external_call_id) == call_sid,
        )
        result = await self.service.session.exec(statement)
        record = result.one_or_none()
        if (
            record is None
            or record.user_id != user_id
            or record.integration_id != integration_id
            or record.resource_id != resource_id
            or record.external_call_id != call_sid
        ):
            raise IntegrationWebhookError(STREAM_CALL_SESSION_MISMATCH_ERROR)
        return record

    async def _receive_start_frame(self, websocket: WebSocket) -> dict[str, object]:
        while True:
            payload = orjson.loads(await websocket.receive_text())
            if not isinstance(payload, dict):
                raise IntegrationWebhookError(STREAM_FRAME_TYPE_ERROR)
            if payload.get("event") == "connected":
                continue
            if payload.get("event") != "start":
                raise IntegrationWebhookError(STREAM_START_ORDER_ERROR)
            start = payload.get("start")
            if not isinstance(start, dict):
                raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
            return payload

    async def _process_frames(
        self,
        *,
        websocket: WebSocket,
        stream_context: TwilioMediaStreamContext,
        bridge_session: TwilioAudioBridgeSession,
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
                        await bridge_session.publish_twilio_payload(media_payload)
                continue
            if event == "stop":
                stop = payload.get("stop")
                stream_sid = stop.get("streamSid") if isinstance(stop, dict) else None
                await self.service.record_twilio_event(
                    user_id=stream_context.user_id,
                    integration_id=stream_context.integration_id,
                    resource_id=stream_context.resource_id,
                    event_type="media_stream_stop",
                    direction=IntegrationDirection.UNKNOWN,
                    external_event_id=stream_sid if isinstance(stream_sid, str) else None,
                    idempotency_key=(
                        f"twilio:{stream_context.account_sid}:stream:"
                        f"{stream_sid if isinstance(stream_sid, str) else 'unknown'}:stop"
                    ),
                    payload_json=safe_stop_frame(payload),
                    safe_summary_json={"stream_sid": stream_sid},
                )
                return

    async def _mark_stream_closed(
        self,
        *,
        stream_context: TwilioMediaStreamContext,
    ) -> None:
        stream_context.call_session.updated_at = utc_now()
        await self.service.session.commit()


def verify_start_frame_account(
    *,
    start_frame: dict[str, object],
    account_sid: str,
) -> None:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
    if start.get("accountSid") != account_sid:
        raise IntegrationWebhookError(STREAM_ACCOUNT_MISMATCH_ERROR)


def stream_sid_from_start_frame(start_frame: dict[str, object]) -> str:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
    stream_sid = start.get("streamSid")
    if not isinstance(stream_sid, str) or not stream_sid.strip():
        raise IntegrationWebhookError(STREAM_SID_MISSING_ERROR)
    return stream_sid


def call_sid_from_start_frame(start_frame: dict[str, object]) -> str:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        raise IntegrationWebhookError(STREAM_START_FRAME_ERROR)
    call_sid = start.get("callSid")
    if not isinstance(call_sid, str) or not call_sid.strip():
        raise IntegrationWebhookError(STREAM_CALL_SID_MISSING_ERROR)
    return call_sid


def safe_start_frame(start_frame: dict[str, object]) -> dict[str, object]:
    start = start_frame.get("start")
    if not isinstance(start, dict):
        return {}
    return {
        "event": "start",
        "streamSid": start.get("streamSid"),
        "accountSid": start.get("accountSid"),
        "callSid": start.get("callSid"),
        "mediaFormat": start.get("mediaFormat"),
    }


def safe_stop_frame(stop_frame: dict[str, object]) -> dict[str, object]:
    stop = stop_frame.get("stop")
    if not isinstance(stop, dict):
        return {"event": "stop"}
    return {"event": "stop", "streamSid": stop.get("streamSid")}


def call_direction_from_session(
    call_session: IntegrationCallSessionRecord,
) -> IntegrationDirection:
    return IntegrationDirection(call_session.direction)


def livekit_audio_bridge_factory(
    *,
    call_router: LiveKitCallRouter,
    livekit_settings_resolver: Callable[[str], Awaitable[ResolvedLiveKitCredentials]],
    voice_agent_name: str,
) -> Callable[[str], Awaitable[TwilioAudioBridgeAdapter | None]]:
    async def factory(user_id: str) -> TwilioAudioBridgeAdapter | None:
        settings = await livekit_settings_resolver(user_id)
        return LiveKitTwilioAudioBridgeAdapter(
            livekit_url=settings.livekit_url,
            credentials=settings,
            call_router=call_router,
            voice_agent_name=voice_agent_name,
        )

    return factory


def missing_livekit_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        InvalidLiveKitCredentialsError
        | MissingLiveKitCredentialsError
        | MissingVoiceRouteSignerError
        | VoiceWorkerRegistrationError
        | VoiceWorkerUnavailableError,
    )

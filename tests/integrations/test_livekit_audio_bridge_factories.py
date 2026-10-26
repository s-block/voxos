from dataclasses import dataclass, field
from typing import Protocol, cast

import pytest
from fastapi import WebSocket
from livekit import rtc
from pydantic import SecretStr

from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import IntegrationDirection, IntegrationProviderType
from voxos.integrations.providers.livekit_audio import (
    phone_audio_track_publish_options,
)
from voxos.integrations.providers.telnyx import media_streams as telnyx_media_streams
from voxos.integrations.providers.telnyx.media_streams import (
    LiveKitTelnyxAudioBridgeAdapter,
    livekit_telnyx_audio_bridge_factory,
)
from voxos.integrations.providers.twilio import media_streams as twilio_media_streams
from voxos.integrations.providers.twilio.media_streams import (
    LiveKitTwilioAudioBridgeAdapter,
    livekit_audio_bridge_factory,
)
from voxos.integrations.records import IntegrationCallSessionRecord
from voxos.voice.call_routing import LiveKitCallRouter
from voxos.voice.livekit_tokens import (
    LiveKitVoiceToken,
    LiveKitVoiceTokenRequest,
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.settings import VoiceAgentSettings


class PhoneBridgeAdapter(Protocol):
    async def connect(
        self,
        *,
        websocket: WebSocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> object:
        """Connect a carrier bridge to LiveKit."""


@dataclass(slots=True)
class RecordingCallRouter:
    sources: list[str] = field(default_factory=list)
    requests: list[LiveKitVoiceTokenRequest] = field(default_factory=list)

    async def create_join_token(
        self,
        *,
        authenticated_user: AuthenticatedUser,
        credentials: ResolvedLiveKitCredentials,
        request: LiveKitVoiceTokenRequest,
        source: str,
    ) -> LiveKitVoiceToken:
        del credentials
        self.sources.append(source)
        self.requests.append(request)
        return LiveKitVoiceToken(
            token=f"join-{authenticated_user.user_id}",
            identity=request.participant_identity or authenticated_user.user_id,
            room_name=request.room_name,
            metadata="{}",
        )


@dataclass(slots=True)
class RecordingLocalParticipant:
    events: list[str]
    publish_options: list[rtc.TrackPublishOptions] = field(default_factory=list)
    publish_error: BaseException | None = None

    async def publish_track(
        self,
        _track: object,
        options: rtc.TrackPublishOptions,
    ) -> object:
        self.events.append("publish")
        self.publish_options.append(options)
        if self.publish_error is not None:
            raise self.publish_error
        return object()


@dataclass(slots=True)
class RecordingRoom:
    local_participant: RecordingLocalParticipant
    events: list[str]
    disconnect_calls: int = 0
    handlers: dict[str, object] = field(default_factory=dict)

    async def connect(self, _url: str, _token: str) -> None:
        self.events.append("connect")

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    def on(self, event: str, callback: object) -> None:
        self.handlers[event] = callback


@dataclass(slots=True)
class RecordingAudioSource:
    close_calls: int = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class FakeLocalAudioTrack:
    @staticmethod
    def create_audio_track(_name: str, _audio_source: object) -> object:
        return object()


def test_phone_audio_publish_options_are_fresh_microphone_options() -> None:
    first = phone_audio_track_publish_options()
    second = phone_audio_track_publish_options()

    assert first is not second
    assert first.source == rtc.TrackSource.SOURCE_MICROPHONE
    assert second.source == rtc.TrackSource.SOURCE_MICROPHONE

    first.source = rtc.TrackSource.SOURCE_UNKNOWN

    assert second.source == rtc.TrackSource.SOURCE_MICROPHONE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [IntegrationProviderType.TELNYX, IntegrationProviderType.TWILIO],
)
async def test_phone_bridge_publishes_caller_track_as_microphone_after_connect(
    monkeypatch: pytest.MonkeyPatch,
    provider: IntegrationProviderType,
) -> None:
    events: list[str] = []
    participant = RecordingLocalParticipant(events=events)
    room = RecordingRoom(local_participant=participant, events=events)
    audio_source = RecordingAudioSource()
    router = RecordingCallRouter()
    adapter = phone_bridge_adapter(provider=provider, router=router)
    patch_phone_bridge_rtc(
        monkeypatch=monkeypatch,
        provider=provider,
        room=room,
        audio_source=audio_source,
    )

    await adapter.connect(
        websocket=cast(WebSocket, object()),
        call_session=phone_call_session(provider),
        user_id="user-123",
    )

    assert events == ["connect", "publish"]
    assert len(participant.publish_options) == 1
    assert participant.publish_options[0].source == rtc.TrackSource.SOURCE_MICROPHONE
    assert router.sources == [provider.value]
    assert router.requests[0].agent_name == "voxos-voice-agent"
    assert router.requests[0].participant_identity is None
    assert audio_source.close_calls == 0
    assert room.disconnect_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [IntegrationProviderType.TELNYX, IntegrationProviderType.TWILIO],
)
async def test_predispatched_outbound_bridge_uses_stable_identity_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    provider: IntegrationProviderType,
) -> None:
    events: list[str] = []
    room = RecordingRoom(
        local_participant=RecordingLocalParticipant(events=events),
        events=events,
    )
    router = RecordingCallRouter()
    adapter = phone_bridge_adapter(provider=provider, router=router)
    patch_phone_bridge_rtc(
        monkeypatch=monkeypatch,
        provider=provider,
        room=room,
        audio_source=RecordingAudioSource(),
    )
    call = phone_call_session(provider)
    call.direction = IntegrationDirection.OUTBOUND.value
    call.media_participant_identity = f"call-{call.id}"

    await adapter.connect(
        websocket=cast(WebSocket, object()),
        call_session=call,
        user_id="user-123",
    )

    assert router.requests[0].participant_identity == f"call-{call.id}"
    assert router.requests[0].participant_name == f"call-{call.id}"
    assert router.requests[0].agent_name is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [IntegrationProviderType.TELNYX, IntegrationProviderType.TWILIO],
)
async def test_phone_bridge_publication_failure_closes_only_its_room_resources(
    monkeypatch: pytest.MonkeyPatch,
    provider: IntegrationProviderType,
) -> None:
    events: list[str] = []
    participant = RecordingLocalParticipant(
        events=events,
        publish_error=RuntimeError("publish failed"),
    )
    room = RecordingRoom(local_participant=participant, events=events)
    audio_source = RecordingAudioSource()
    adapter = phone_bridge_adapter(
        provider=provider,
        router=RecordingCallRouter(),
    )
    patch_phone_bridge_rtc(
        monkeypatch=monkeypatch,
        provider=provider,
        room=room,
        audio_source=audio_source,
    )

    with pytest.raises(RuntimeError, match="publish failed"):
        await adapter.connect(
            websocket=cast(WebSocket, object()),
            call_session=phone_call_session(provider),
            user_id="user-123",
        )

    assert events == ["connect", "publish"]
    assert audio_source.close_calls == 1
    assert room.disconnect_calls == 1


def phone_bridge_adapter(
    *,
    provider: IntegrationProviderType,
    router: RecordingCallRouter,
) -> PhoneBridgeAdapter:
    credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://saved-livekit.example.test",
        api_key="saved-livekit-key",  # pragma: allowlist secret
        api_secret=SecretStr("saved-livekit-secret"),  # pragma: allowlist secret
    )
    if provider == IntegrationProviderType.TELNYX:
        return LiveKitTelnyxAudioBridgeAdapter(
            livekit_url=credentials.livekit_url,
            credentials=credentials,
            call_router=cast(LiveKitCallRouter, router),
            voice_agent_name="voxos-voice-agent",
        )
    return LiveKitTwilioAudioBridgeAdapter(
        livekit_url=credentials.livekit_url,
        credentials=credentials,
        call_router=cast(LiveKitCallRouter, router),
        voice_agent_name="voxos-voice-agent",
    )


def phone_call_session(
    provider: IntegrationProviderType,
) -> IntegrationCallSessionRecord:
    return IntegrationCallSessionRecord(
        user_id="user-123",
        integration_id="integration-123",
        resource_id="resource-123",
        provider=provider.value,
        direction=IntegrationDirection.INBOUND.value,
        external_call_id="provider-call-123",
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        livekit_room_name="phone-room-123",
    )


def patch_phone_bridge_rtc(
    *,
    monkeypatch: pytest.MonkeyPatch,
    provider: IntegrationProviderType,
    room: RecordingRoom,
    audio_source: RecordingAudioSource,
) -> None:
    module = telnyx_media_streams if provider == IntegrationProviderType.TELNYX else twilio_media_streams
    monkeypatch.setattr(module.rtc, "Room", lambda: room)
    monkeypatch.setattr(
        module.rtc,
        "AudioSource",
        lambda **_options: audio_source,
    )
    monkeypatch.setattr(module.rtc, "LocalAudioTrack", FakeLocalAudioTrack)


@pytest.mark.asyncio
async def test_twilio_livekit_bridge_factory_resolves_user_livekit_credentials(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    resolved_user_ids: list[str] = []

    async def resolver(user_id: str) -> ResolvedLiveKitCredentials:
        resolved_user_ids.append(user_id)
        return ResolvedLiveKitCredentials(
            livekit_url="wss://saved-livekit.example.test",
            api_key="saved-livekit-key",  # pragma: allowlist secret
            api_secret=SecretStr("saved-livekit-secret"),  # pragma: allowlist secret
        )

    factory = livekit_audio_bridge_factory(
        call_router=call_router(sqlite_session_factory),
        livekit_settings_resolver=resolver,
        voice_agent_name="voxos-voice-agent",
    )

    adapter = await factory("user-123")

    assert resolved_user_ids == ["user-123"]
    assert adapter is not None
    assert adapter.livekit_url == "wss://saved-livekit.example.test"
    assert adapter.credentials.api_key == "saved-livekit-key"  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_telnyx_livekit_bridge_factory_resolves_user_livekit_credentials(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    resolved_user_ids: list[str] = []

    async def resolver(user_id: str) -> ResolvedLiveKitCredentials:
        resolved_user_ids.append(user_id)
        return ResolvedLiveKitCredentials(
            livekit_url="wss://saved-livekit.example.test",
            api_key="saved-livekit-key",  # pragma: allowlist secret
            api_secret=SecretStr("saved-livekit-secret"),  # pragma: allowlist secret
        )

    factory = livekit_telnyx_audio_bridge_factory(
        call_router=call_router(sqlite_session_factory),
        livekit_settings_resolver=resolver,
        voice_agent_name="voxos-voice-agent",
    )

    adapter = await factory("user-123")

    assert resolved_user_ids == ["user-123"]
    assert adapter is not None
    assert adapter.livekit_url == "wss://saved-livekit.example.test"
    assert adapter.credentials.api_key == "saved-livekit-key"  # pragma: allowlist secret


def call_router(session_factory: AsyncSessionFactory) -> LiveKitCallRouter:
    settings = VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=SecretStr(
            "route-signing-secret-at-least-32-characters",
        ),
    )
    return LiveKitCallRouter(
        session_factory=session_factory,
        settings=settings,
        token_template=LiveKitVoiceTokenService.from_settings(settings),
    )

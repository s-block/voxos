import jwt
import orjson
import pytest
from livekit import api
from pydantic import SecretStr

from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.voice import call_routing
from voxos.voice.call_routing import LiveKitCallRouter
from voxos.voice.errors import (
    InvalidVoiceRouteSignatureError,
    MissingLiveKitCredentialsError,
    MissingVoiceRouteSignerError,
)
from voxos.voice.livekit_tokens import (
    LiveKitIdentityMetadataBuilder,
    LiveKitVoiceTokenRequest,
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.route_signing import LiveKitRouteSigner
from voxos.voice.settings import VoiceAgentSettings


def authenticated_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="local-user-123",
        firebase_user_id="firebase-user-123",
        email="user@example.com",
        email_verified=True,
        display_name="Test User",
        photo_url="https://example.com/user.png",
    )


@pytest.mark.asyncio
async def test_livekit_call_router_persists_worker_demand(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=SecretStr(
            "route-signing-secret-at-least-32-characters",
        ),
    )
    router = LiveKitCallRouter(
        session_factory=sqlite_session_factory,
        settings=settings,
        token_template=LiveKitVoiceTokenService.from_settings(settings),
    )

    demand = await router.request_worker(
        user_id="user-123",
        credentials=ResolvedLiveKitCredentials(
            livekit_url="wss://example.livekit.cloud",
            api_key="livekit-key",  # pragma: allowlist secret
            api_secret=SecretStr("livekit-secret"),  # pragma: allowlist secret
        ),
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        room_name="room-123",
        source="mobile",
    )

    assert demand.runtime_session_id == "runtime-123"


def test_livekit_metadata_builder_uses_local_user_id() -> None:
    builder = LiveKitIdentityMetadataBuilder(user_metadata_key="user_id")

    metadata = builder.build_metadata(
        authenticated_user=authenticated_user(),
        client_metadata={
            "user_id": "client-supplied-user",
            "agent_id": "client-agent",
            "runtime_session_id": "client-session",
            "device": "browser",
        },
        agent_id="agent-123",
        runtime_session_id="session-123",
    )

    payload = orjson.loads(metadata)

    assert payload == {
        "agent_id": "agent-123",
        "device": "browser",
        "runtime_session_id": "session-123",
        "user_id": "local-user-123",
    }


def test_livekit_voice_token_service_mints_local_identity_token() -> None:
    route_signer = LiveKitRouteSigner(
        secret=SecretStr("route-signing-secret-at-least-32-characters"),
        ttl_seconds=900,
        time_clock=lambda: 1_000,
    )
    service = LiveKitVoiceTokenService(
        api_key="devkey",
        api_secret=SecretStr("0123456789abcdef0123456789abcdef"),
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
        route_signer=route_signer,
    )
    request = LiveKitVoiceTokenRequest(
        room_name=" voice-room ",
        participant_name=" Browser User ",
        client_metadata={
            "agent_id": "client-agent",
            "runtime_session_id": "client-session",
            "user_id": "client-supplied-user",
        },
        agent_name=" voxos-voice-agent ",
        agent_id="agent-123",
        runtime_session_id="session-123",
    )

    token = service.create_join_token(
        authenticated_user=authenticated_user(),
        request=request,
    )
    claims = jwt.decode(token.token, options={"verify_signature": False})

    assert token.identity == "local-user-123"
    assert token.room_name == "voice-room"
    metadata = orjson.loads(token.metadata)
    assert metadata["agent_id"] == "agent-123"
    assert metadata["runtime_session_id"] == "session-123"
    assert metadata["user_id"] == "local-user-123"
    assert isinstance(metadata["voxos_route"], str)
    signed_route = route_signer.verify(metadata["voxos_route"], room_name="voice-room")
    assert signed_route.user_id == "local-user-123"
    assert signed_route.agent_id == "agent-123"
    assert signed_route.runtime_session_id == "session-123"

    claim_metadata = orjson.loads(claims["metadata"])
    assert claim_metadata == metadata
    assert claim_metadata["agent_id"] == "agent-123"
    assert claim_metadata["runtime_session_id"] == "session-123"
    assert claim_metadata["user_id"] == "local-user-123"
    assert isinstance(claim_metadata["voxos_route"], str)
    assert claims["roomConfig"]["agents"][0]["agentName"] == "voxos-voice-agent"
    assert orjson.loads(claims["roomConfig"]["agents"][0]["metadata"]) == metadata
    assert claims["video"]["room"] == "voice-room"
    assert claims["video"]["roomJoin"] is True


def test_livekit_voice_token_service_requires_route_signer_for_agent_dispatch() -> None:
    service = LiveKitVoiceTokenService(
        api_key="devkey",
        api_secret=SecretStr("0123456789abcdef0123456789abcdef"),
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
    )

    with pytest.raises(MissingVoiceRouteSignerError):
        service.create_join_token(
            authenticated_user=authenticated_user(),
            request=LiveKitVoiceTokenRequest(
                room_name="voice-room",
                agent_name="voxos-voice-agent",
                agent_id="agent-123",
                runtime_session_id="session-123",
            ),
        )


def test_livekit_route_signer_rejects_malformed_token() -> None:
    route_signer = LiveKitRouteSigner(
        secret=SecretStr("route-signing-secret-at-least-32-characters"),
        ttl_seconds=900,
    )

    with pytest.raises(InvalidVoiceRouteSignatureError):
        route_signer.verify("not-base64.not-base64", room_name="voice-room")


def test_livekit_voice_token_service_keeps_non_agent_tokens_without_route() -> None:
    service = LiveKitVoiceTokenService(
        api_key="devkey",
        api_secret=SecretStr("0123456789abcdef0123456789abcdef"),
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
    )

    token = service.create_join_token(
        authenticated_user=authenticated_user(),
        request=LiveKitVoiceTokenRequest(
            room_name="voice-room",
            client_metadata={"voxos_route": "client-route"},
        ),
    )

    assert orjson.loads(token.metadata) == {
        "user_id": "local-user-123",
    }


def test_livekit_voice_token_service_uses_server_selected_participant_identity() -> None:
    service = LiveKitVoiceTokenService(
        api_key="devkey",
        api_secret=SecretStr("0123456789abcdef0123456789abcdef"),
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
    )

    token = service.create_join_token(
        authenticated_user=authenticated_user(),
        request=LiveKitVoiceTokenRequest(
            room_name="voice-room",
            participant_identity="call-session-123",
            participant_name="Carrier bridge",
        ),
    )
    claims = jwt.decode(token.token, options={"verify_signature": False})

    assert token.identity == "call-session-123"
    assert claims["sub"] == "call-session-123"


@pytest.mark.asyncio
async def test_livekit_call_router_dispatches_only_signed_route_metadata(
    sqlite_session_factory: AsyncSessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[api.CreateAgentDispatchRequest] = []

    class FakeAgentDispatch:
        async def create_dispatch(
            self,
            request: api.CreateAgentDispatchRequest,
        ) -> None:
            requests.append(request)

    class FakeLiveKitApi:
        def __init__(self, **_kwargs: object) -> None:
            self.agent_dispatch = FakeAgentDispatch()

        async def __aenter__(self) -> "FakeLiveKitApi":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(call_routing.api, "LiveKitAPI", FakeLiveKitApi)
    settings = VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=SecretStr(
            "route-signing-secret-at-least-32-characters",
        ),
    )
    router = LiveKitCallRouter(
        session_factory=sqlite_session_factory,
        settings=settings,
        token_template=LiveKitVoiceTokenService.from_settings(settings),
    )

    await router.dispatch_agent(
        user_id="user-123",
        credentials=ResolvedLiveKitCredentials(
            livekit_url="wss://livekit.example.test",
            api_key="livekit-key",  # pragma: allowlist secret
            api_secret=SecretStr("livekit-secret"),  # pragma: allowlist secret
        ),
        agent_id="agent-123",
        runtime_session_id="runtime-123",
        room_name="room-123",
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.agent_name == "voxos-voice-agent"
    assert request.room == "room-123"
    metadata = orjson.loads(request.metadata)
    assert tuple(metadata) == ("voxos_route",)
    assert "user-123" not in request.metadata


def test_livekit_voice_token_service_requires_credentials() -> None:
    service = LiveKitVoiceTokenService(
        api_key=None,
        api_secret=None,
        metadata_builder=LiveKitIdentityMetadataBuilder(user_metadata_key="user_id"),
        ttl_seconds=900,
    )

    with pytest.raises(MissingLiveKitCredentialsError):
        service.create_join_token(
            authenticated_user=authenticated_user(),
            request=LiveKitVoiceTokenRequest(room_name="voice-room"),
        )


def test_livekit_voice_token_service_from_settings_has_no_global_credentials() -> None:
    service = LiveKitVoiceTokenService.from_settings(VoiceAgentSettings(_env_file=None))

    assert "livekit_url" not in VoiceAgentSettings.model_fields
    assert "livekit_api_key" not in VoiceAgentSettings.model_fields
    assert "livekit_api_secret" not in VoiceAgentSettings.model_fields
    assert service.api_key is None
    assert service.api_secret is None
    assert service.route_signer is None

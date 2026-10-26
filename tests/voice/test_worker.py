import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import orjson
import pytest
from pydantic import SecretStr
from sqlmodel import select

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicySnapshot,
    effective_tool_policy_from_json,
)
from voxos.agent_runtime.records import AgentSessionRecord
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationCallOpeningState,
    IntegrationCallStatus,
    IntegrationDirection,
    IntegrationProviderType,
)
from voxos.integrations.records import IntegrationCallSessionRecord
from voxos.providers.base import (
    RealtimeSessionObserver,
    RealtimeVoiceSession,
    RealtimeVoiceSessionOptions,
)
from voxos.providers.models import OpenAIRealtimeConfig, RealtimeVoiceProvider
from voxos.providers.registry import VoiceProviderRegistry
from voxos.voice.browser_tools import OWNER_BROWSER_ACCOUNT_VOICE_SOURCE
from voxos.voice.caller_access import VoiceCallerAccess, VoiceCallerAccessLevel
from voxos.voice.credentials import VoiceCredentialResolver
from voxos.voice.demand import VoiceCallDemand, VoiceCallDemandStatus
from voxos.voice.errors import (
    MissingVoiceRouteMetadataError,
    MissingVoiceRouteSignerError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.models import ResolvedVoiceCredentials, UserVoicePreferences
from voxos.voice.outbound_calls import (
    OUTBOUND_CALL_KICKOFF_INSTRUCTIONS,
    OutboundCallContextResolver,
    OutboundCallLifecycleService,
)
from voxos.voice.route_signing import LiveKitRouteSigner
from voxos.voice.settings import VoiceAgentSettings
from voxos.voice.storage import VoicePreferenceStore
from voxos.voice.tools import VoiceLiveKitTool, VoiceToolProvider
from voxos.voice.worker import (
    VoiceAgentService,
    VoiceCallDemandLifecycle,
    extract_route_identity_from_context,
    extract_signed_route_identity_from_metadata,
    run_worker,
    validate_voice_worker_settings,
)

ROUTE_METADATA = '{"user_id":"user-123","agent_id":"agent-123","runtime_session_id":"session-123"}'


@dataclass(slots=True)
class FakeJob:
    metadata: str


@dataclass(slots=True)
class FakeRoom:
    metadata: str
    name: str | None = None


@dataclass(slots=True)
class FakeContext:
    job: FakeJob
    room: FakeRoom
    connected: bool = False
    worker_id: str = "worker-123"
    shutdown_reasons: list[str] = field(default_factory=list)

    @property
    def room_metadata(self) -> str:
        return self.room.metadata

    async def connect(self) -> None:
        self.connected = True

    def shutdown(self, reason: str = "") -> None:
        self.shutdown_reasons.append(reason)


@dataclass(slots=True)
class FakePreferenceStore:
    preferences: UserVoicePreferences

    async def get_preferences(self, user_id: str) -> UserVoicePreferences:
        assert user_id == self.preferences.user_id
        return self.preferences


@dataclass(slots=True)
class FakeCredentialResolver:
    credentials: ResolvedVoiceCredentials

    async def resolve(
        self,
        user_id: str,
        provider: RealtimeVoiceProvider,
        credential_ref: str,
    ) -> ResolvedVoiceCredentials:
        assert user_id == "user-123"
        assert provider == self.credentials.provider
        assert credential_ref == self.credentials.reference
        return self.credentials


@dataclass(slots=True)
class FakeSpeechHandle:
    interrupted: bool = False

    async def wait_for_playout(self) -> None:
        return None


@dataclass(slots=True)
class FakeProviderSession:
    started_with_room: object | None = None
    started_participant_identity: str | None = None
    audio_input_enabled: bool = True
    initial_reply_instructions: list[str | None] = field(default_factory=list)
    started_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def start(
        self,
        room: object,
        *,
        participant_identity: str | None = None,
        audio_input_enabled: bool = True,
    ) -> None:
        self.started_with_room = room
        self.started_participant_identity = participant_identity
        self.audio_input_enabled = audio_input_enabled
        self.started_event.set()

    def generate_initial_reply(
        self,
        *,
        instructions: str | None = None,
    ) -> FakeSpeechHandle:
        self.initial_reply_instructions.append(instructions)
        return FakeSpeechHandle()

    async def wait_for_media_ready(self) -> None:
        return None

    async def wait_for_first_speech(self) -> None:
        return None

    def set_audio_input_enabled(self, enabled: bool) -> None:
        self.audio_input_enabled = enabled


@dataclass(slots=True)
class FakeFactory:
    session: FakeProviderSession
    received_preferences: UserVoicePreferences | None = None
    received_session_options: RealtimeVoiceSessionOptions | None = None
    received_session_observer: RealtimeSessionObserver | None = None

    @property
    def provider(self) -> RealtimeVoiceProvider:
        return RealtimeVoiceProvider.OPENAI_REALTIME

    async def create_session(
        self,
        preferences: UserVoicePreferences,
        credentials: ResolvedVoiceCredentials,
        tool_provider: VoiceToolProvider,
        session_options: RealtimeVoiceSessionOptions,
        session_observer: RealtimeSessionObserver | None = None,
    ) -> RealtimeVoiceSession:
        self.received_preferences = preferences
        self.received_session_options = session_options
        self.received_session_observer = session_observer
        return self.session


class EmptyToolProvider:
    async def get_tools(self, user_id: str) -> Sequence[VoiceLiveKitTool]:
        return ()


@dataclass(slots=True)
class FakeDemandLifecycleClient:
    active_runtime_ids: list[str]
    heartbeat_runtime_ids: list[str]
    completed_runtime_ids: list[str]
    heartbeat_error: Exception | None = None
    demand_source: str = "livekit_api"

    async def mark_active(
        self,
        runtime_session_id: str,
        _worker_id: str,
        *,
        agent_id: str,
        room_name: str,
    ) -> VoiceCallDemand:
        assert agent_id == "agent-123"
        assert room_name == "room-123"
        self.active_runtime_ids.append(runtime_session_id)
        return activated_demand(
            source=self.demand_source,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            worker_id=_worker_id,
        )

    async def heartbeat(self, runtime_session_id: str, _worker_id: str) -> None:
        self.heartbeat_runtime_ids.append(runtime_session_id)
        if self.heartbeat_error is not None:
            raise self.heartbeat_error

    async def complete(self, runtime_session_id: str, _worker_id: str) -> None:
        self.completed_runtime_ids.append(runtime_session_id)


@dataclass(slots=True)
class FakeInstructionProvider:
    rendered_instructions: str
    received_user_id: str | None = None
    received_preferences: UserVoicePreferences | None = None

    async def render_instructions(
        self,
        user_id: str,
        preferences: UserVoicePreferences,
    ) -> str:
        self.received_user_id = user_id
        self.received_preferences = preferences
        return self.rendered_instructions


@dataclass(slots=True)
class FakeCallerAwareInstructionProvider:
    rendered_instructions: str
    received_scope: AgentScope | None = None
    received_caller_access: VoiceCallerAccess | None = None
    received_policy: EffectiveToolPolicySnapshot | None = None

    async def render_instructions_for_session(
        self,
        *,
        scope: AgentScope,
        preferences: UserVoicePreferences,
        caller_access: VoiceCallerAccess,
        effective_tool_policy: EffectiveToolPolicySnapshot,
    ) -> str:
        del preferences
        self.received_scope = scope
        self.received_caller_access = caller_access
        self.received_policy = effective_tool_policy
        return self.rendered_instructions


def route_signer(now: int = 1_000) -> LiveKitRouteSigner:
    return LiveKitRouteSigner(
        secret=SecretStr("route-signing-secret-at-least-32-characters"),
        ttl_seconds=900,
        time_clock=lambda: now,
    )


def activated_demand(
    *,
    source: str = "livekit_api",
    user_id: str = "user-123",
    agent_id: str = "agent-123",
    runtime_session_id: str = "session-123",
    room_name: str = "voice-room",
    worker_id: str = "worker-123",
) -> VoiceCallDemand:
    now = datetime.now(UTC)
    return VoiceCallDemand(
        id="demand-123",
        user_id=user_id,
        agent_id=agent_id,
        runtime_session_id=runtime_session_id,
        room_name=room_name,
        source=source,
        project_key="a" * 64,
        status=VoiceCallDemandStatus.ACTIVE,
        worker_id=worker_id,
        worker_ready_expires_at=None,
        failure_code=None,
        expires_at=now + timedelta(seconds=60),
    )


def signed_route_metadata(
    *,
    signer: LiveKitRouteSigner | None = None,
    room_name: str = "voice-room",
    user_id: str = "user-123",
    agent_id: str = "agent-123",
    runtime_session_id: str = "session-123",
) -> str:
    resolved_signer = signer or route_signer()
    route_token = resolved_signer.sign(
        user_id=user_id,
        agent_id=agent_id,
        runtime_session_id=runtime_session_id,
        room_name=room_name,
    )
    return f'{{"voxos_route":"{route_token}"}}'


def test_run_worker_configures_voxos_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_logging: list[bool] = []
    received_settings: list[tuple[VoiceAgentSettings, bool]] = []
    settings = VoiceAgentSettings()

    def configure_logging() -> None:
        configured_logging.append(True)

    async def run_supervisor(
        resolved_settings: VoiceAgentSettings | None = None,
        *,
        until_idle: bool = False,
    ) -> None:
        assert resolved_settings is not None
        received_settings.append((resolved_settings, until_idle))

    monkeypatch.setattr(
        "voxos.voice.worker.configure_voxos_logging",
        configure_logging,
    )
    monkeypatch.setattr(
        "voxos.voice.worker.run_database_backed_worker_from_settings",
        run_supervisor,
    )

    run_worker(settings, until_idle=True)

    assert configured_logging == [True]
    assert received_settings == [(settings, True)]


def test_extract_signed_route_identity_from_metadata() -> None:
    signer = route_signer()
    identity = extract_signed_route_identity_from_metadata(
        metadata=signed_route_metadata(signer=signer),
        route_metadata_key="voxos_route",
        route_signer=signer,
        room_name="voice-room",
    )

    assert identity is not None
    assert identity.user_id == "user-123"
    assert identity.agent_id == "agent-123"
    assert identity.runtime_session_id == "session-123"


def test_extract_route_identity_with_signer_rejects_unsigned_legacy_metadata() -> None:
    context = FakeContext(
        job=FakeJob(metadata=ROUTE_METADATA),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )

    with pytest.raises(MissingVoiceRouteMetadataError):
        extract_route_identity_from_context(
            context=context,
            metadata_key="user_id",
            agent_metadata_key="agent_id",
            runtime_session_metadata_key="runtime_session_id",
            route_signer=route_signer(),
        )


def test_extract_route_identity_with_signer_ignores_forged_legacy_keys() -> None:
    signer = route_signer()
    payload = orjson.loads(signed_route_metadata(signer=signer))
    payload.update(
        {
            "user_id": "attacker",
            "agent_id": "other-agent",
            "runtime_session_id": "other-session",
        },
    )
    context = FakeContext(
        job=FakeJob(metadata=orjson.dumps(payload).decode()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )

    identity = extract_route_identity_from_context(
        context=context,
        metadata_key="user_id",
        agent_metadata_key="agent_id",
        runtime_session_metadata_key="runtime_session_id",
        route_signer=signer,
    )

    assert identity.user_id == "user-123"
    assert identity.agent_id == "agent-123"
    assert identity.runtime_session_id == "session-123"


def test_extract_route_identity_from_context_raises_when_missing() -> None:
    context = FakeContext(job=FakeJob(metadata="{}"), room=FakeRoom(metadata="{}", name="voice-room"))

    with pytest.raises(MissingVoiceRouteMetadataError):
        extract_route_identity_from_context(
            context=context,
            metadata_key="user_id",
            agent_metadata_key="agent_id",
            runtime_session_metadata_key="runtime_session_id",
            route_signer=route_signer(),
        )


def test_extract_route_identity_from_context_requires_route_signer() -> None:
    context = FakeContext(
        job=FakeJob(metadata=ROUTE_METADATA),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )

    with pytest.raises(MissingVoiceRouteSignerError):
        extract_route_identity_from_context(
            context=context,
            metadata_key="user_id",
            agent_metadata_key="agent_id",
            runtime_session_metadata_key="runtime_session_id",
        )


def test_extract_route_identity_from_context_requires_room_name() -> None:
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}"),
    )

    with pytest.raises(MissingVoiceRouteMetadataError):
        extract_route_identity_from_context(
            context=context,
            metadata_key="user_id",
            agent_metadata_key="agent_id",
            runtime_session_metadata_key="runtime_session_id",
            route_signer=route_signer(),
        )


def test_validate_voice_worker_settings_requires_route_signer() -> None:
    settings = VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=None,
    )

    with pytest.raises(MissingVoiceRouteSignerError):
        validate_voice_worker_settings(settings)


@pytest.mark.asyncio
async def test_voice_demand_lifecycle_completes_on_job_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeDemandLifecycleClient([], [], [], demand_source="telnyx")
    lifecycle = VoiceCallDemandLifecycle(
        demand_client=client,
        user_id="user-123",
        runtime_session_id="runtime-123",
        agent_id="agent-123",
        room_name="room-123",
        worker_id="worker-123",
        heartbeat_interval_seconds=0.01,
        shutdown_job=lambda _reason: None,
    )

    await lifecycle.start()
    await asyncio.sleep(0.02)

    assert client.active_runtime_ids == ["runtime-123"]
    assert client.heartbeat_runtime_ids
    assert client.completed_runtime_ids == []

    caplog.set_level(logging.INFO, logger="voxos.voice.worker")
    await lifecycle.close("room_closed")
    await lifecycle.close("duplicate_callback")

    assert client.completed_runtime_ids == ["runtime-123"]
    completion_records = [record for record in caplog.records if record.getMessage() == "Completed LiveKit call demand"]
    assert len(completion_records) == 1
    assert completion_records[0].source == "telnyx"
    assert completion_records[0].close_reason == "room_closed"


@pytest.mark.asyncio
async def test_voice_demand_lifecycle_stops_job_after_lease_loss() -> None:
    client = FakeDemandLifecycleClient(
        [],
        [],
        [],
        heartbeat_error=VoiceWorkerUnavailableError("demand_lease_lost"),
    )
    shutdown_reasons: list[str] = []
    shutdown_requested = asyncio.Event()

    def shutdown_job(reason: str) -> None:
        shutdown_reasons.append(reason)
        shutdown_requested.set()

    lifecycle = VoiceCallDemandLifecycle(
        demand_client=client,
        user_id="user-123",
        runtime_session_id="runtime-123",
        agent_id="agent-123",
        room_name="room-123",
        worker_id="worker-123",
        heartbeat_interval_seconds=0.01,
        shutdown_job=shutdown_job,
    )

    await lifecycle.start()
    await asyncio.wait_for(shutdown_requested.wait(), timeout=0.2)

    assert client.heartbeat_runtime_ids == ["runtime-123"]
    assert shutdown_reasons == ["voice_demand_lease_lost"]
    await lifecycle.close("lease_lost")


@pytest.mark.asyncio
async def test_voice_agent_service_orchestrates_session_start() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    provider_session = FakeProviderSession()
    factory = FakeFactory(provider_session)
    session_options = RealtimeVoiceSessionOptions(
        user_away_timeout_seconds=30.0,
        delete_room_on_close=True,
    )
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    signer = route_signer()
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(
            factories=(factory,),
        ),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        session_options=session_options,
        route_signer=signer,
    )

    await service.start(context, demand=activated_demand())

    assert context.connected is True
    assert provider_session.started_with_room is context.room
    assert provider_session.initial_reply_instructions == [None]
    assert factory.received_session_options == session_options


@pytest.mark.asyncio
async def test_voice_agent_service_adds_fresh_owner_location_only_to_live_prompt(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(
            agent_instructions="Base instructions.",
        ),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    location_metadata = {
        "latitude": 42.4304,
        "longitude": 19.2594,
        "horizontal_accuracy_m": 25,
        "captured_at": utc_now().isoformat(),
    }
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(
            metadata=orjson.dumps(
                {
                    "room": "test",
                    "voxos_location": location_metadata,
                },
            ).decode(),
            name="voice-room",
        ),
    )
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        runtime_store=runtime_store,
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand())

    assert factory.received_preferences is not None
    instructions = factory.received_preferences.provider_config.agent_instructions
    assert "# Current Device Location" in instructions
    assert "Latitude: 42.4304000" in instructions
    async with sqlite_session_factory() as session:
        runtime_session = await session.get(AgentSessionRecord, "session-123")
    assert runtime_session is not None
    assert runtime_session.room_metadata == {"room": "test"}
    assert runtime_session.prompt_snapshot is not None
    assert "# Current Device Location" not in runtime_session.prompt_snapshot
    assert "42.4304000" not in runtime_session.prompt_snapshot


@pytest.mark.asyncio
async def test_voice_agent_service_drops_location_for_limited_non_client_source() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Base instructions."),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    route_metadata = orjson.loads(signed_route_metadata())
    route_metadata["voxos_location"] = {
        "latitude": 42.4304,
        "longitude": 19.2594,
        "horizontal_accuracy_m": 25,
        "captured_at": utc_now().isoformat(),
    }
    context = FakeContext(
        job=FakeJob(metadata=orjson.dumps(route_metadata).decode()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand(source="telnyx"))

    assert factory.received_preferences is not None
    instructions = factory.received_preferences.provider_config.agent_instructions
    assert "# Current Device Location" not in instructions
    assert "42.4304000" not in instructions


@pytest.mark.asyncio
async def test_voice_agent_service_drops_stale_owner_location() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Base instructions."),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    route_metadata = orjson.loads(signed_route_metadata())
    route_metadata["voxos_location"] = {
        "latitude": 42.4304,
        "longitude": 19.2594,
        "horizontal_accuracy_m": 25,
        "captured_at": (utc_now() - timedelta(minutes=16)).isoformat(),
    }
    context = FakeContext(
        job=FakeJob(metadata=orjson.dumps(route_metadata).decode()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand())

    assert factory.received_preferences is not None
    instructions = factory.received_preferences.provider_config.agent_instructions
    assert "# Current Device Location" not in instructions
    assert "42.4304000" not in instructions


@pytest.mark.asyncio
@pytest.mark.parametrize("first_signal", ("answer", "media"))
async def test_outbound_opening_waits_for_answer_and_media_then_uses_objective(
    sqlite_session_factory: AsyncSessionFactory,
    first_signal: str,
) -> None:
    objective = "Introduce yourself and explain tomorrow's weather forecast."
    call = IntegrationCallSessionRecord(
        user_id="user-123",
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.TWILIO.value,
        direction=IntegrationDirection.OUTBOUND.value,
        external_call_id="reservation-123",
        status=IntegrationCallStatus.INITIATING.value,
        contact_display_name="Ada Lovelace",
        agent_id="agent-123",
        runtime_session_id="session-123",
        outbound_objective=objective,
        opening_state=IntegrationCallOpeningState.PENDING.value,
        opening_attempt_count=0,
    )
    call.media_participant_identity = f"call-{call.id}"
    async with sqlite_session_factory() as session:
        session.add(call)
        await session.commit()

    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Base instructions."),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    provider_session = FakeProviderSession()
    factory = FakeFactory(provider_session)
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        instruction_provider=FakeInstructionProvider("Caller-safe instructions."),
        runtime_store=runtime_store,
        route_signer=route_signer(),
        outbound_call_context_resolver=OutboundCallContextResolver(
            session_factory=sqlite_session_factory,
        ),
        outbound_call_lifecycle_service=OutboundCallLifecycleService(
            session_factory=sqlite_session_factory,
            poll_interval_seconds=0.01,
        ),
        outbound_answer_timeout_seconds=1.0,
        outbound_opening_timeout_seconds=1.0,
    )

    start_task = asyncio.create_task(
        service.start(
            context,
            demand=activated_demand(source=IntegrationProviderType.TWILIO.value),
        ),
    )
    await asyncio.wait_for(provider_session.started_event.wait(), timeout=1)

    assert provider_session.started_participant_identity == f"call-{call.id}"
    assert provider_session.audio_input_enabled is False
    assert provider_session.initial_reply_instructions == []
    async with sqlite_session_factory() as session:
        stored_call = await session.get(IntegrationCallSessionRecord, call.id)
        assert stored_call is not None
        assert stored_call.agent_ready_at is not None
        if first_signal == "answer":
            stored_call.answered_at = utc_now()
            stored_call.status = IntegrationCallStatus.IN_PROGRESS.value
        else:
            stored_call.media_ready_at = utc_now()
        await session.commit()
    await asyncio.sleep(0.03)
    assert provider_session.initial_reply_instructions == []

    async with sqlite_session_factory() as session:
        stored_call = await session.get(IntegrationCallSessionRecord, call.id)
        assert stored_call is not None
        if first_signal == "answer":
            stored_call.media_ready_at = utc_now()
        else:
            stored_call.answered_at = utc_now()
            stored_call.status = IntegrationCallStatus.IN_PROGRESS.value
        await session.commit()

    await asyncio.wait_for(start_task, timeout=1)

    assert provider_session.initial_reply_instructions == [
        OUTBOUND_CALL_KICKOFF_INSTRUCTIONS,
    ]
    assert provider_session.audio_input_enabled is True
    assert factory.received_preferences is not None
    rendered_instructions = factory.received_preferences.provider_config.agent_instructions
    assert "# Outbound Call Task" in rendered_instructions
    assert "Ada Lovelace" in rendered_instructions
    assert objective in rendered_instructions
    assert "On your first spoken turn only, introduce yourself" in rendered_instructions
    assert "without reintroducing yourself" in rendered_instructions
    assert "treat that part as completed by the first spoken turn" in rendered_instructions
    assert "one-time opening" in OUTBOUND_CALL_KICKOFF_INSTRUCTIONS
    assert "Do not repeat the introduction on later turns" in OUTBOUND_CALL_KICKOFF_INSTRUCTIONS
    assert context.shutdown_reasons == []
    async with sqlite_session_factory() as session:
        stored_call = await session.get(IntegrationCallSessionRecord, call.id)
        runtime_session = await session.get(AgentSessionRecord, "session-123")
    assert stored_call is not None
    assert stored_call.opening_state == IntegrationCallOpeningState.COMPLETED.value
    assert stored_call.opening_attempt_count == 1
    assert stored_call.first_agent_speech_at is not None
    assert stored_call.opening_completed_at is not None
    assert runtime_session is not None
    assert objective in runtime_session.prompt_snapshot


@pytest.mark.asyncio
async def test_outbound_call_context_and_opening_claim_are_exact_and_idempotent(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    call = IntegrationCallSessionRecord(
        user_id="user-123",
        integration_id="integration-123",
        resource_id="resource-123",
        provider=IntegrationProviderType.TELNYX.value,
        direction=IntegrationDirection.OUTBOUND.value,
        external_call_id="reservation-123",
        status=IntegrationCallStatus.IN_PROGRESS.value,
        agent_id="agent-123",
        runtime_session_id="session-123",
        outbound_objective="Ask whether Ada needs help.",
        media_participant_identity="call-session-123",
        opening_state=IntegrationCallOpeningState.PENDING.value,
        opening_attempt_count=0,
        answered_at=utc_now(),
        media_ready_at=utc_now(),
    )
    async with sqlite_session_factory() as session:
        session.add(call)
        await session.commit()
    resolver = OutboundCallContextResolver(session_factory=sqlite_session_factory)

    context = await resolver.resolve(
        user_id="user-123",
        agent_id="agent-123",
        runtime_session_id="session-123",
        provider=IntegrationProviderType.TELNYX.value,
    )

    assert context is not None
    assert (
        await resolver.resolve(
            user_id="user-123",
            agent_id="other-agent",
            runtime_session_id="session-123",
            provider=IntegrationProviderType.TELNYX.value,
        )
        is None
    )
    assert (
        await resolver.resolve(
            user_id="other-user",
            agent_id="agent-123",
            runtime_session_id="session-123",
            provider=IntegrationProviderType.TELNYX.value,
        )
        is None
    )
    lifecycle = OutboundCallLifecycleService(
        session_factory=sqlite_session_factory,
        poll_interval_seconds=0.01,
    )
    claims = await asyncio.gather(
        lifecycle.claim_opening(context, max_attempts=2),
        lifecycle.claim_opening(context, max_attempts=2),
    )

    assert sorted(claims) == [False, True]
    async with sqlite_session_factory() as session:
        stored_call = await session.get(IntegrationCallSessionRecord, call.id)
    assert stored_call is not None
    assert stored_call.opening_state == IntegrationCallOpeningState.CLAIMED.value
    assert stored_call.opening_attempt_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["telnyx", "twilio"])
async def test_voice_agent_service_disables_speech_idle_timeout_for_phone_source(
    source: str,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata='{"source":"livekit_api"}', name="voice-room"),
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        session_options=RealtimeVoiceSessionOptions(
            user_away_timeout_seconds=30.0,
            delete_room_on_close=True,
        ),
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand(source=source))

    assert factory.received_session_options == RealtimeVoiceSessionOptions(
        user_away_timeout_seconds=None,
        delete_room_on_close=True,
        telephony=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["livekit_api", "unknown_source"])
async def test_voice_agent_service_keeps_speech_idle_timeout_for_non_phone_source(
    source: str,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata='{"source":"telnyx"}', name="voice-room"),
    )
    session_options = RealtimeVoiceSessionOptions(
        user_away_timeout_seconds=45.0,
        delete_room_on_close=False,
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        session_options=session_options,
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand(source=source))

    assert factory.received_session_options is session_options


@pytest.mark.asyncio
async def test_voice_agent_service_rejects_demand_for_another_route() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        route_signer=route_signer(),
    )

    with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
        await service.start(
            context,
            demand=activated_demand(source="telnyx", runtime_session_id="other-session"),
        )

    assert exc_info.value.reason_code == "demand_route_mismatch"
    assert factory.received_session_options is None


@pytest.mark.asyncio
async def test_voice_agent_service_applies_rendered_instructions_to_provider_config() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Base instructions."),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    provider_session = FakeProviderSession()
    factory = FakeFactory(provider_session)
    instruction_provider = FakeInstructionProvider("Rendered persona instructions.")
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    signer = route_signer()
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        instruction_provider=instruction_provider,
        route_signer=signer,
    )

    await service.start(context, demand=activated_demand())

    assert instruction_provider.received_user_id == "user-123"
    assert instruction_provider.received_preferences is preferences
    assert factory.received_preferences is not None
    assert factory.received_preferences.provider_config.agent_instructions == "Rendered persona instructions."
    assert preferences.provider_config.agent_instructions == "Base instructions."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "expected_access_level", "expected_scope_source"),
    (
        ("telnyx", VoiceCallerAccessLevel.LIMITED_CONTACT, "voice.worker"),
        (
            "future_voice_provider",
            VoiceCallerAccessLevel.LIMITED_CONTACT,
            "voice.worker",
        ),
        (
            "livekit_api",
            VoiceCallerAccessLevel.OWNER,
            OWNER_BROWSER_ACCOUNT_VOICE_SOURCE,
        ),
    ),
)
async def test_voice_session_source_fallback_is_fail_closed(
    source: str,
    expected_access_level: VoiceCallerAccessLevel,
    expected_scope_source: str,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
        provider_config=OpenAIRealtimeConfig(agent_instructions="Private instructions."),
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    factory = FakeFactory(FakeProviderSession())
    instruction_provider = FakeCallerAwareInstructionProvider(
        "Caller-aware instructions.",
    )
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata="{}", name="voice-room"),
    )
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        instruction_provider=instruction_provider,
        route_signer=route_signer(),
    )

    await service.start(context, demand=activated_demand(source=source))

    assert instruction_provider.received_caller_access is not None
    assert instruction_provider.received_caller_access.level == expected_access_level
    assert instruction_provider.received_scope is not None
    assert instruction_provider.received_scope.source == expected_scope_source
    assert instruction_provider.received_policy is not None
    assert instruction_provider.received_policy.entries == ()
    assert factory.received_preferences is not None
    assert factory.received_preferences.provider_config.agent_instructions == "Caller-aware instructions."


@pytest.mark.asyncio
async def test_voice_agent_service_creates_runtime_session_when_configured(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        model="gpt-realtime",
        voice="marin",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )
    provider_session = FakeProviderSession()
    factory = FakeFactory(provider_session)
    context = FakeContext(
        job=FakeJob(metadata=signed_route_metadata()),
        room=FakeRoom(metadata='{"room":"test"}', name="voice-room"),
    )
    signer = route_signer()
    runtime_store = SqlModelAgentRuntimeStore(sqlite_session_factory)
    service = VoiceAgentService(
        preference_store=FakePreferenceStore(preferences),
        credential_resolver=FakeCredentialResolver(credentials),
        provider_registry=VoiceProviderRegistry(factories=(factory,)),
        tool_provider=EmptyToolProvider(),
        user_metadata_key="user_id",
        instruction_provider=FakeInstructionProvider("Rendered runtime instructions."),
        runtime_store=runtime_store,
        route_signer=signer,
    )

    await service.start(context, demand=activated_demand())

    assert factory.received_session_observer is not None
    async with sqlite_session_factory() as session:
        result = await session.exec(select(AgentSessionRecord))
        records = result.all()
    assert len(records) == 1
    assert records[0].id == "session-123"
    assert records[0].user_id == "user-123"
    assert records[0].agent_id == "agent-123"
    assert records[0].model == "gpt-realtime"
    assert records[0].prompt_hash is not None
    assert effective_tool_policy_from_json(records[0].effective_tool_policy_json) is not None


def test_fake_dependencies_satisfy_protocols() -> None:
    preferences = UserVoicePreferences(
        user_id="user-123",
        credential_ref="openai-local",
    )
    credentials = ResolvedVoiceCredentials(
        provider=RealtimeVoiceProvider.OPENAI_REALTIME,
        reference="openai-local",
        token=SecretStr("sk-test-secret"),
    )

    preference_store: VoicePreferenceStore = FakePreferenceStore(preferences)
    credential_resolver: VoiceCredentialResolver = FakeCredentialResolver(credentials)

    assert preference_store is not None
    assert credential_resolver is not None

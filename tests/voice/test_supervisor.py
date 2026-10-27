import asyncio
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import orjson
import pytest
from livekit import agents
from livekit.agents.job import JobAcceptArguments
from livekit.protocol import agent as agent_protocol
from livekit.protocol import models as livekit_models
from pydantic import SecretStr
from sqlalchemy import update
from sqlmodel import col, select

from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory, voice_worker_session_factory
from voxos.voice.demand import (
    VoiceCallDemand,
    VoiceCallDemandClient,
    VoiceCallDemandRecord,
    VoiceCallDemandStatus,
    VoiceProjectClaimResult,
    VoiceProjectKeyFactory,
    VoiceProjectWorkerLeaseRecord,
    VoiceWorkerDemandStore,
    VoiceWorkerWarmRequest,
    VoiceWorkerWarmRequestClient,
)
from voxos.voice.endpoint_policy import LiveKitEndpointPolicy
from voxos.voice.errors import VoiceWorkerUnavailableError
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import ResolvedLiveKitCredentials
from voxos.voice.route_signing import LiveKitRouteSigner
from voxos.voice.settings import VoiceAgentSettings
from voxos.voice.supervisor import (
    LiveKitProjectRegistration,
    ProjectJobAdmissionController,
    ProjectWorkerCredentialsMessage,
    ProjectWorkerEvent,
    ProjectWorkerEventKind,
    ProjectWorkerHandle,
    ProjectWorkerLaunchOptions,
    VoiceWorkerSupervisor,
    clear_livekit_credential_environment,
    project_worker_idle_processes,
    project_worker_options,
    run_project_worker_server,
)


@dataclass(slots=True)
class FakeProjectWorkerHandle:
    project_key: str
    livekit_url: str
    events: list[ProjectWorkerEvent]
    stopped: bool = False

    def poll_events(self) -> tuple[ProjectWorkerEvent, ...]:
        events = tuple(self.events)
        self.events.clear()
        return events

    async def stop(self, timeout_seconds: float) -> None:
        assert timeout_seconds > 0
        self.stopped = True


@dataclass(slots=True)
class FakeProjectWorkerLauncher:
    failed_urls: set[str] = field(default_factory=set)
    registrations: list[LiveKitProjectRegistration] = field(default_factory=list)
    handles: list[FakeProjectWorkerHandle] = field(default_factory=list)

    def start(self, registration: LiveKitProjectRegistration) -> ProjectWorkerHandle:
        self.registrations.append(registration)
        if registration.livekit_url in self.failed_urls:
            event = ProjectWorkerEvent(
                kind=ProjectWorkerEventKind.FAILED,
                failure_code="registration_rejected",
                failure_type="AuthenticationError",
            )
        else:
            event = ProjectWorkerEvent(
                kind=ProjectWorkerEventKind.READY,
                worker_id=f"worker-{registration.project_key[:12]}",
            )
        handle = FakeProjectWorkerHandle(
            project_key=registration.project_key,
            livekit_url=registration.livekit_url,
            events=[event],
        )
        self.handles.append(handle)
        return handle


async def unused_entrypoint(_context: agents.JobContext) -> None:
    return None


def project_launch_options(
    *,
    project_key: str = "a" * 64,
    settings: VoiceAgentSettings | None = None,
) -> ProjectWorkerLaunchOptions:
    resolved_settings = settings or supervisor_settings()
    assert resolved_settings.voice_route_signing_secret is not None
    return ProjectWorkerLaunchOptions(
        project_key=project_key,
        agent_name=resolved_settings.voice_agent_name,
        start_timeout_seconds=resolved_settings.voice_worker_start_timeout_seconds,
        shutdown_timeout_seconds=(resolved_settings.voice_worker_shutdown_timeout_seconds),
        route_metadata_key=resolved_settings.voice_route_metadata_key,
        route_signing_secret=resolved_settings.voice_route_signing_secret,
        route_ttl_seconds=resolved_settings.voice_token_ttl_seconds,
        route_clock_skew_seconds=(resolved_settings.voice_route_clock_skew_seconds),
        demand_ttl_seconds=resolved_settings.voice_worker_demand_ttl_seconds,
        max_active_projects_per_tenant=(resolved_settings.voice_worker_max_active_projects_per_tenant),
        idle_project_ttl_seconds=resolved_settings.voice_worker_idle_ttl_seconds,
        poll_interval_seconds=resolved_settings.voice_worker_poll_interval_seconds,
        idle_processes=project_worker_idle_processes(resolved_settings),
    )


@dataclass(slots=True)
class FakeJobDecision:
    accepted: int = 0
    rejected: list[bool] = field(default_factory=list)


def job_request(
    *,
    room_name: str,
    metadata: str,
    decision: FakeJobDecision,
    agent_name: str = "voxos-voice-agent",
    job_id: str = "job-test",
) -> agents.JobRequest:
    async def on_accept(_arguments: JobAcceptArguments) -> None:
        decision.accepted += 1

    async def on_reject(terminate: bool) -> None:
        decision.rejected.append(terminate)

    return agents.JobRequest(
        job=agent_protocol.Job(
            id=job_id,
            agent_name=agent_name,
            metadata=metadata,
            room=livekit_models.Room(name=room_name),
        ),
        on_accept=on_accept,
        on_reject=on_reject,
    )


def signed_route_metadata(
    *,
    settings: VoiceAgentSettings,
    user_id: str,
    agent_id: str,
    runtime_session_id: str,
    room_name: str,
) -> str:
    assert settings.voice_route_signing_secret is not None
    route_token = LiveKitRouteSigner(
        secret=settings.voice_route_signing_secret,
        ttl_seconds=settings.voice_token_ttl_seconds,
        clock_skew_seconds=settings.voice_route_clock_skew_seconds,
    ).sign(
        user_id=user_id,
        agent_id=agent_id,
        runtime_session_id=runtime_session_id,
        room_name=room_name,
    )
    return orjson.dumps(
        {settings.voice_route_metadata_key: route_token},
    ).decode()


def test_project_worker_options_use_only_explicit_project_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)

    prewarmed_processes: list[agents.JobProcess] = []

    def prewarm(process: agents.JobProcess) -> None:
        assert os.environ["LIVEKIT_URL"] == "wss://calling-tenant.livekit.test"
        prewarmed_processes.append(process)

    launch_options = project_launch_options(
        settings=supervisor_settings().model_copy(
            update={"voice_worker_idle_processes": 1},
        ),
    )
    options = project_worker_options(
        credentials=ProjectWorkerCredentialsMessage(
            livekit_url="wss://calling-tenant.livekit.test",
            api_key="calling-tenant-key",  # pragma: allowlist secret
            api_secret="calling-tenant-secret",  # noqa: S106  # pragma: allowlist secret
        ),
        options=launch_options,
        entrypoint=unused_entrypoint,
        prewarm_fnc=prewarm,
    )

    assert options.ws_url == "wss://calling-tenant.livekit.test"
    assert options.api_key == "calling-tenant-key"  # pragma: allowlist secret
    assert options.api_secret == "calling-tenant-secret"  # pragma: allowlist secret
    assert options.host == "127.0.0.1"
    assert options.port == 0
    assert options.num_idle_processes == 1
    assert math.isinf(options.load_threshold)
    assert options.shutdown_process_timeout == pytest.approx(0.15)
    assert options.job_executor_type == agents.JobExecutorType.PROCESS
    process = agents.JobProcess(
        executor_type=agents.JobExecutorType.PROCESS,
        user_arguments=None,
        http_proxy=None,
    )
    options.prewarm_fnc(process)
    assert os.environ["LIVEKIT_URL"] == "wss://calling-tenant.livekit.test"
    assert os.environ["LIVEKIT_API_KEY"] == "calling-tenant-key"  # pragma: allowlist secret
    assert os.environ["LIVEKIT_API_SECRET"] == "calling-tenant-secret"  # pragma: allowlist secret
    assert prewarmed_processes == [process]
    clear_livekit_credential_environment()


def test_activity_warming_keeps_one_idle_job_process() -> None:
    assert (
        project_worker_idle_processes(
            supervisor_settings().model_copy(
                update={
                    "voice_warm_user_workers": True,
                    "voice_worker_idle_processes": 0,
                },
            ),
        )
        == 1
    )
    assert (
        project_worker_idle_processes(
            supervisor_settings().model_copy(
                update={
                    "voice_warm_user_workers": False,
                    "voice_worker_idle_processes": 0,
                },
            ),
        )
        == 0
    )


def test_project_worker_scrubs_inherited_and_sdk_livekit_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://wrong.livekit.test")
    monkeypatch.setenv("LIVEKIT_API_KEY", "wrong-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "wrong-secret")

    clear_livekit_credential_environment()

    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        assert name not in os.environ


@dataclass(slots=True)
class FakeProjectWorkerConnection:
    sent: list[ProjectWorkerEvent] = field(default_factory=list)
    _stop_consumed: bool = False

    def send(self, event: ProjectWorkerEvent) -> None:
        self.sent.append(event)

    def poll(self) -> bool:
        return not self._stop_consumed and any(event.kind == ProjectWorkerEventKind.READY for event in self.sent)

    def recv(self) -> str:
        self._stop_consumed = True
        return "stop"


@dataclass(slots=True)
class FakeAgentServer:
    registered_callback: object | None = None
    active_jobs: dict[str, object] = field(default_factory=dict)
    drained: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    _connection_failed: bool = False
    _connecting: bool = False

    def on(self, event_name: str, callback: object) -> None:
        assert event_name == "worker_registered"
        self.registered_callback = callback

    async def run(self) -> None:
        assert callable(self.registered_callback)
        self.registered_callback("worker-test", object())
        await self.closed.wait()

    async def drain(self, *, timeout: int) -> None:
        assert timeout >= 1
        self.drained = True

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_project_server_registers_scrubs_environment_and_drains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = FakeProjectWorkerConnection()
    server = FakeAgentServer()

    def build_server(_options: agents.WorkerOptions) -> FakeAgentServer:
        monkeypatch.setenv("LIVEKIT_URL", "wss://tenant.livekit.test")
        monkeypatch.setenv("LIVEKIT_API_KEY", "tenant-key")
        monkeypatch.setenv("LIVEKIT_API_SECRET", "tenant-secret")
        return server

    monkeypatch.setattr(
        agents.AgentServer,
        "from_server_options",
        staticmethod(build_server),
    )

    await run_project_worker_server(
        connection=connection,  # type: ignore[arg-type]
        credentials=ProjectWorkerCredentialsMessage(
            livekit_url="wss://tenant.livekit.test",
            api_key="tenant-key",  # pragma: allowlist secret
            api_secret="tenant-secret",  # noqa: S106  # pragma: allowlist secret
        ),
        options=project_launch_options(
            settings=supervisor_settings().model_copy(
                update={
                    "voice_worker_start_timeout_seconds": 1,
                    "voice_worker_shutdown_timeout_seconds": 0.1,
                    "voice_worker_lease_ttl_seconds": 1,
                },
            ),
        ),
        entrypoint=unused_entrypoint,
    )

    assert [event.kind for event in connection.sent] == [
        ProjectWorkerEventKind.READY,
        ProjectWorkerEventKind.STOPPED,
    ]
    assert server.drained is True
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        assert name not in os.environ


def supervisor_settings() -> VoiceAgentSettings:
    return VoiceAgentSettings(
        _env_file=None,
        voice_route_signing_secret=SecretStr(
            "route-signing-secret-at-least-32-characters",
        ),
        voice_worker_poll_interval_seconds=0.01,
        voice_worker_start_timeout_seconds=1.0,
        voice_worker_settings_resolution_timeout_seconds=0.5,
        voice_worker_lease_renewal_concurrency=1,
        voice_worker_demand_ttl_seconds=30,
        voice_worker_idle_ttl_seconds=0.0,
        voice_worker_lease_ttl_seconds=2.0,
        voice_worker_shutdown_timeout_seconds=1.0,
        voice_livekit_endpoint_policy=LiveKitEndpointPolicy.TRUSTED_PRIVATE,
    )


def test_voice_settings_reject_invalid_worker_lifecycle_at_construction() -> None:
    with pytest.raises(ValueError, match="DEMAND_TTL_SECONDS must exceed"):
        VoiceAgentSettings(
            _env_file=None,
            voice_worker_demand_ttl_seconds=1,
        )


@pytest.mark.asyncio
async def test_supervisor_rejects_settings_resolution_timeout_at_start_timeout(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_settings_resolution_timeout_seconds": 1.0},
    )

    with pytest.raises(
        ValueError,
        match="SETTINGS_RESOLUTION_TIMEOUT_SECONDS plus one poll interval",
    ):
        supervisor(
            settings=settings,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            launcher=FakeProjectWorkerLauncher(),
        )


@pytest.mark.asyncio
async def test_supervisor_requires_demand_ttl_beyond_start_deadline(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_demand_ttl_seconds": 1},
    )

    with pytest.raises(ValueError, match="DEMAND_TTL_SECONDS must exceed"):
        supervisor(
            settings=settings,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            launcher=FakeProjectWorkerLauncher(),
        )


@pytest.mark.asyncio
async def test_supervisor_requires_retention_through_idle_project_window(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={
            "voice_worker_idle_ttl_seconds": 10,
            "voice_worker_demand_retention_seconds": 9,
        },
    )

    with pytest.raises(ValueError, match="DEMAND_RETENTION_SECONDS must be at least"):
        supervisor(
            settings=settings,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            launcher=FakeProjectWorkerLauncher(),
        )


async def save_settings(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    user_id: str,
    livekit_url: str,
    api_key: str,
    credential_data: str,
) -> ResolvedLiveKitCredentials:
    async with session_factory() as session:
        service = UserLiveKitSettingsService(
            session=session,
            secret_vault=secret_vault,
        )
        await service.set_settings(
            user_id=user_id,
            livekit_url=livekit_url,
            api_key=SecretStr(api_key),
            api_secret=SecretStr(credential_data),
        )
        return await service.resolve_settings(user_id)


def demand_client(
    *,
    session_factory: AsyncSessionFactory,
    user_id: str,
    settings: VoiceAgentSettings,
) -> VoiceCallDemandClient:
    assert settings.voice_route_signing_secret is not None
    return VoiceCallDemandClient(
        session_factory=tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        ),
        user_id=user_id,
        project_key_factory=VoiceProjectKeyFactory(
            settings.voice_route_signing_secret,
        ),
        demand_ttl_seconds=settings.voice_worker_demand_ttl_seconds,
        poll_interval_seconds=settings.voice_worker_poll_interval_seconds,
        start_timeout_seconds=settings.voice_worker_start_timeout_seconds,
        max_active_projects_per_tenant=(settings.voice_worker_max_active_projects_per_tenant),
        idle_project_ttl_seconds=settings.voice_worker_idle_ttl_seconds,
    )


async def request_call(
    *,
    client: VoiceCallDemandClient,
    credentials: ResolvedLiveKitCredentials,
    suffix: str,
) -> str:
    demand = await client.request(
        credentials=credentials,
        agent_id=f"agent-{suffix}",
        runtime_session_id=f"runtime-{suffix}",
        room_name=f"room-{suffix}",
        source="test",
    )
    return demand.id


def supervisor(
    *,
    settings: VoiceAgentSettings,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    launcher: FakeProjectWorkerLauncher,
    supervisor_id: str = "test-supervisor",
) -> VoiceWorkerSupervisor:
    return VoiceWorkerSupervisor(
        settings=settings,
        session_factory=session_factory,
        secret_vault=secret_vault,
        entrypoint=unused_entrypoint,
        launcher=launcher,
        supervisor_id=supervisor_id,
    )


@pytest.mark.asyncio
async def test_run_until_idle_exits_when_there_is_no_voice_demand(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker_supervisor = supervisor(
        settings=supervisor_settings(),
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=FakeProjectWorkerLauncher(),
    )

    await asyncio.wait_for(worker_supervisor.run_until_idle(), timeout=1)

    assert worker_supervisor._workers == {}


@pytest.mark.asyncio
async def test_supervisor_starts_and_retains_project_for_activity_warm_request(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_idle_ttl_seconds": 60.0},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="active-tenant",
        livekit_url="wss://active.livekit.test",
        api_key="active-key",  # pragma: allowlist secret
        credential_data="active-secret",
    )
    assert settings.voice_route_signing_secret is not None
    warm_client = VoiceWorkerWarmRequestClient(
        session_factory=tenant_scoped_session_factory(
            session_factory=sqlite_session_factory,
            user_id="active-tenant",
        ),
        user_id="active-tenant",
        project_key_factory=VoiceProjectKeyFactory(
            settings.voice_route_signing_secret,
        ),
    )
    warm_request = await warm_client.request(credentials=credentials)
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()
    await worker_supervisor.refresh()

    assert [registration.project_key for registration in launcher.registrations] == [
        warm_request.project_key,
    ]
    assert warm_request.project_key in worker_supervisor._workers
    live_demands = await VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    ).list_live_demands()
    assert len(live_demands) == 1
    assert isinstance(live_demands[0], VoiceWorkerWarmRequest)
    assert launcher.handles[0].stopped is False
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_two_tenants_with_different_projects_receive_simultaneous_calls(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials_a, credentials_b = await asyncio.gather(
        save_settings(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            user_id="tenant-a",
            livekit_url="wss://tenant-a.livekit.test",
            api_key="tenant-a-key",  # pragma: allowlist secret
            credential_data="tenant-a-secret",
        ),
        save_settings(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            user_id="tenant-b",
            livekit_url="wss://tenant-b.livekit.test",
            api_key="tenant-b-key",  # pragma: allowlist secret
            credential_data="tenant-b-secret",
        ),
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id, demand_b_id = await asyncio.gather(
        request_call(client=client_a, credentials=credentials_a, suffix="a"),
        request_call(client=client_b, credentials=credentials_b, suffix="b"),
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    assert {registration.livekit_url for registration in launcher.registrations} == {
        "wss://tenant-a.livekit.test",
        "wss://tenant-b.livekit.test",
    }
    assert (await client_a.get(demand_a_id)).status == VoiceCallDemandStatus.READY
    assert (await client_b.get(demand_b_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_two_tenants_sharing_livekit_settings_reuse_one_project_worker(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://shared.livekit.test",
        api_key="shared-key",  # pragma: allowlist secret
        credential_data="shared-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://shared.livekit.test",
        api_key="shared-key",  # pragma: allowlist secret
        credential_data="shared-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id, demand_b_id = await asyncio.gather(
        request_call(client=client_a, credentials=credentials_a, suffix="a"),
        request_call(client=client_b, credentials=credentials_b, suffix="b"),
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    assert len(launcher.registrations) == 1
    demand_a = await client_a.get(demand_a_id)
    demand_b = await client_b.get(demand_b_id)
    assert demand_a.status == VoiceCallDemandStatus.READY
    assert demand_b.status == VoiceCallDemandStatus.READY
    assert demand_a.worker_id == demand_b.worker_id
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_project_job_admission_rejects_invalid_and_replayed_routes(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    ready = await client.get(demand_id)
    assert ready.worker_id is not None
    admission = ProjectJobAdmissionController(
        options=project_launch_options(
            project_key=ready.project_key,
            settings=settings,
        ),
        session_factory=sqlite_session_factory,
    )
    admission.registered(ready.worker_id)

    invalid_decision = FakeJobDecision()
    await admission(
        job_request(
            room_name="room-a",
            metadata="{}",
            decision=invalid_decision,
            job_id="invalid-job",
        ),
    )
    assert invalid_decision.accepted == 0
    assert invalid_decision.rejected == [True]
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.READY

    metadata = signed_route_metadata(
        settings=settings,
        user_id="tenant-a",
        agent_id="agent-a",
        runtime_session_id="runtime-a",
        room_name="room-a",
    )
    accepted_decision = FakeJobDecision()
    await admission(
        job_request(
            room_name="room-a",
            metadata=metadata,
            decision=accepted_decision,
            job_id="accepted-job",
        ),
    )
    assert accepted_decision.accepted == 1
    assert accepted_decision.rejected == []
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.ACTIVE

    replay_decision = FakeJobDecision()
    await admission(
        job_request(
            room_name="room-a",
            metadata=metadata,
            decision=replay_decision,
            job_id="replayed-job",
        ),
    )
    assert replay_decision.accepted == 0
    assert replay_decision.rejected == [True]
    await client.mark_active(
        "runtime-a",
        ready.worker_id,
        agent_id="agent-a",
        room_name="room-a",
    )
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.ACTIVE
    await client.complete("runtime-a", ready.worker_id)
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_project_job_admission_rejects_route_for_another_project(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://tenant-b.livekit.test",
        api_key="tenant-b-key",  # pragma: allowlist secret
        credential_data="tenant-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id, demand_b_id = await asyncio.gather(
        request_call(client=client_a, credentials=credentials_a, suffix="a"),
        request_call(client=client_b, credentials=credentials_b, suffix="b"),
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    ready_a = await client_a.get(demand_a_id)
    ready_b = await client_b.get(demand_b_id)
    assert ready_a.worker_id is not None
    assert ready_b.worker_id is not None
    admission = ProjectJobAdmissionController(
        options=project_launch_options(
            project_key=ready_a.project_key,
            settings=settings,
        ),
        session_factory=sqlite_session_factory,
    )
    admission.registered(ready_a.worker_id)
    decision = FakeJobDecision()

    await admission(
        job_request(
            room_name="room-b",
            metadata=signed_route_metadata(
                settings=settings,
                user_id="tenant-b",
                agent_id="agent-b",
                runtime_session_id="runtime-b",
                room_name="room-b",
            ),
            decision=decision,
        ),
    )

    assert decision.accepted == 0
    assert decision.rejected == [True]
    assert (await client_b.get(demand_b_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_project_job_admission_rejects_rewritten_room_and_agent(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="original",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    ready = await client.get(demand_id)
    assert ready.worker_id is not None
    admission = ProjectJobAdmissionController(
        options=project_launch_options(
            project_key=ready.project_key,
            settings=settings,
        ),
        session_factory=sqlite_session_factory,
    )
    admission.registered(ready.worker_id)
    decision = FakeJobDecision()

    await admission(
        job_request(
            room_name="room-rewritten",
            metadata=signed_route_metadata(
                settings=settings,
                user_id="tenant-a",
                agent_id="agent-rewritten",
                runtime_session_id="runtime-original",
                room_name="room-rewritten",
            ),
            decision=decision,
        ),
    )

    assert decision.accepted == 0
    assert decision.rejected == [True]
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_two_simultaneous_calls_for_one_tenant_share_project_worker(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_ids = await asyncio.gather(
        request_call(client=client, credentials=credentials, suffix="a-1"),
        request_call(client=client, credentials=credentials, suffix="a-2"),
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    assert len(launcher.registrations) == 1
    demands = await asyncio.gather(*(client.get(demand_id) for demand_id in demand_ids))
    assert {demand.status for demand in demands} == {VoiceCallDemandStatus.READY}
    assert len({demand.worker_id for demand in demands}) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_same_project_call_capacity_admits_fifo_and_isolates_excess_call(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_calls_per_project": 2},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://shared.livekit.test",
        api_key="shared-key",  # pragma: allowlist secret
        credential_data="shared-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_ids = tuple(
        [
            await request_call(
                client=client,
                credentials=credentials,
                suffix=str(index),
            )
            for index in range(3)
        ],
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    demands = tuple([await client.get(demand_id) for demand_id in demand_ids])
    assert [demand.status for demand in demands] == [
        VoiceCallDemandStatus.READY,
        VoiceCallDemandStatus.READY,
        VoiceCallDemandStatus.FAILED,
    ]
    assert demands[2].failure_code == "project_call_capacity_exhausted"
    assert len(launcher.registrations) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_observed_active_job_holds_capacity_after_demand_expiry(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_calls_per_project": 1},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    first_demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="first",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    first = await client.get(first_demand_id)
    assert first.worker_id is not None
    await client.mark_active(
        "runtime-first",
        first.worker_id,
        agent_id="agent-first",
        room_name="room-first",
    )
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "tenant-a",
    )() as session:
        await session.exec(
            update(VoiceCallDemandRecord)
            .where(col(VoiceCallDemandRecord.id) == first_demand_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        )
        await session.commit()
    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.ACTIVE_JOBS,
            active_jobs=1,
        ),
    )
    second_demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="second",
    )

    await worker_supervisor.refresh()

    first_expired = await client.get(first_demand_id)
    second = await client.get(second_demand_id)
    assert first_expired.status == VoiceCallDemandStatus.EXPIRED
    assert second.status == VoiceCallDemandStatus.FAILED
    assert second.failure_code == "project_call_capacity_exhausted"
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_observed_active_jobs_and_ready_routes_share_project_capacity(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_calls_per_project": 2},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    first_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="first",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    first = await client.get(first_id)
    assert first.worker_id is not None
    await client.mark_active(
        "runtime-first",
        first.worker_id,
        agent_id="agent-first",
        room_name="room-first",
    )
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "tenant-a",
    )() as session:
        await session.exec(
            update(VoiceCallDemandRecord)
            .where(col(VoiceCallDemandRecord.id) == first_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1)),
        )
        await session.commit()
    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.ACTIVE_JOBS,
            active_jobs=1,
        ),
    )

    second_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="second",
    )
    await worker_supervisor.refresh()
    assert (await client.get(second_id)).status == VoiceCallDemandStatus.READY

    third_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="third",
    )
    await worker_supervisor.refresh()

    third = await client.get(third_id)
    assert third.status == VoiceCallDemandStatus.PENDING
    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.ACTIVE_JOBS,
            active_jobs=0,
        ),
    )
    await worker_supervisor.refresh()
    assert (await client.get(third_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_launching_job_does_not_fail_overlapping_ready_route(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_calls_per_project": 2},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    first_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="first",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    first = await client.get(first_id)
    assert first.status == VoiceCallDemandStatus.READY
    assert first.worker_id is not None
    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.ACTIVE_JOBS,
            active_jobs=1,
        ),
    )
    second_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="second",
    )

    await worker_supervisor.refresh()

    assert (await client.get(second_id)).status == VoiceCallDemandStatus.PENDING
    await client.mark_active(
        "runtime-first",
        first.worker_id,
        agent_id="agent-first",
        room_name="room-first",
    )
    await worker_supervisor.refresh()
    assert (await client.get(second_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_reconnect_withdraws_readiness_and_republishes_new_worker_id(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    original = await client.get(demand_id)
    assert original.worker_id is not None

    launcher.handles[0].events.append(
        ProjectWorkerEvent(kind=ProjectWorkerEventKind.NOT_READY),
    )
    await worker_supervisor.refresh()
    unavailable = await client.get(demand_id)
    assert unavailable.status == VoiceCallDemandStatus.PENDING
    assert unavailable.worker_id is None

    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.READY,
            worker_id="worker-reconnected",
        ),
    )
    await worker_supervisor.refresh()
    reconnected = await client.get(demand_id)
    assert reconnected.status == VoiceCallDemandStatus.READY
    assert reconnected.worker_id == "worker-reconnected"
    with pytest.raises(VoiceWorkerUnavailableError):
        await client.mark_active(
            "runtime-a",
            original.worker_id,
            agent_id="agent-a",
            room_name="room-a",
        )
    await client.mark_active(
        "runtime-a",
        "worker-reconnected",
        agent_id="agent-a",
        room_name="room-a",
    )
    await client.complete("runtime-a", "worker-reconnected")
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_new_worker_id_forces_republication_without_not_ready_event(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    original = await client.get(demand_id)
    assert original.worker_id is not None

    launcher.handles[0].events.append(
        ProjectWorkerEvent(
            kind=ProjectWorkerEventKind.READY,
            worker_id="worker-reconnected",
        ),
    )
    await worker_supervisor.refresh()

    republished = await client.get(demand_id)
    assert republished.status == VoiceCallDemandStatus.READY
    assert republished.worker_id == "worker-reconnected"
    with pytest.raises(VoiceWorkerUnavailableError):
        await client.mark_active(
            "runtime-a",
            original.worker_id,
            agent_id="agent-a",
            room_name="room-a",
        )
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_stopping_project_withdraws_unconsumed_ready_route(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.READY

    await worker_supervisor.stop_all()

    stopped = await client.get(demand_id)
    assert stopped.status == VoiceCallDemandStatus.PENDING
    assert stopped.worker_id is None
    assert stopped.worker_ready_expires_at is None


@pytest.mark.asyncio
async def test_ready_project_accepts_later_call_without_new_registration(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    await request_call(client=client, credentials=credentials, suffix="first")
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    later_demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="later",
    )

    await worker_supervisor.refresh()

    assert len(launcher.registrations) == 1
    assert (await client.get(later_demand_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_project_worker_stops_after_last_call_and_idle_ttl(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    ready_demand = await client.get(demand_id)
    assert ready_demand.worker_id is not None
    await client.complete("runtime-a", ready_demand.worker_id)

    await worker_supervisor.refresh()
    await worker_supervisor.refresh()

    assert launcher.handles[0].stopped is True
    assert worker_supervisor._workers == {}


@pytest.mark.asyncio
async def test_ready_event_is_reconciled_after_transient_database_failure(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    original = VoiceWorkerDemandStore.mark_project_lease_ready
    attempts = 0

    async def flaky_mark_ready(
        store: VoiceWorkerDemandStore,
        *,
        project_key: str,
        owner_id: str,
        worker_id: str,
        lease_ttl_seconds: float,
    ) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "temporary database failure"
            raise RuntimeError(message)
        return await original(
            store,
            project_key=project_key,
            owner_id=owner_id,
            worker_id=worker_id,
            lease_ttl_seconds=lease_ttl_seconds,
        )

    monkeypatch.setattr(
        VoiceWorkerDemandStore,
        "mark_project_lease_ready",
        flaky_mark_ready,
    )

    with pytest.raises(RuntimeError, match="temporary database failure"):
        await worker_supervisor.refresh()
    await worker_supervisor.refresh()

    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.READY
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_publication_failure_cancels_other_project_starts_without_duplicates(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_start_concurrency": 2},
    )
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://tenant-b.livekit.test",
        api_key="tenant-b-key",  # pragma: allowlist secret
        credential_data="tenant-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    await request_call(client=client_a, credentials=credentials_a, suffix="a")
    await request_call(client=client_b, credentials=credentials_b, suffix="b")
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    second_start_entered = asyncio.Event()
    release_second_start = asyncio.Event()
    first_start_completed = asyncio.Event()
    original_start = VoiceWorkerSupervisor._start_project
    original_resolver = VoiceWorkerSupervisor._resolve_tenant_project_credentials
    original_publish = VoiceWorkerSupervisor._publish_completed_starts
    publication_attempts = 0

    async def force_parallel_starts(_instance: VoiceWorkerSupervisor) -> int:
        return 2

    async def serialize_sqlite_claims(
        instance: VoiceWorkerSupervisor,
        *,
        project_key: str,
        demands: tuple[VoiceCallDemand, ...],
        store: VoiceWorkerDemandStore,
    ) -> None:
        if demands[0].user_id == "tenant-b":
            await first_start_completed.wait()
            await original_start(
                instance,
                project_key=project_key,
                demands=demands,
                store=store,
            )
            return
        try:
            await original_start(
                instance,
                project_key=project_key,
                demands=demands,
                store=store,
            )
        finally:
            first_start_completed.set()

    async def block_second_start(
        instance: VoiceWorkerSupervisor,
        user_id: str,
    ) -> ResolvedLiveKitCredentials:
        if user_id == "tenant-b" and not release_second_start.is_set():
            second_start_entered.set()
            await release_second_start.wait()
        return await original_resolver(instance, user_id)

    async def fail_first_publication(
        instance: VoiceWorkerSupervisor,
        store: VoiceWorkerDemandStore,
    ) -> None:
        nonlocal publication_attempts
        publication_attempts += 1
        if publication_attempts == 1:
            await second_start_entered.wait()
            message = "temporary publication failure"
            raise RuntimeError(message)
        await original_publish(instance, store)

    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_project_start_concurrency",
        force_parallel_starts,
    )
    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_start_project",
        serialize_sqlite_claims,
    )
    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_resolve_tenant_project_credentials",
        block_second_start,
    )
    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_publish_completed_starts",
        fail_first_publication,
    )

    with pytest.raises(RuntimeError, match="temporary publication failure"):
        await worker_supervisor.refresh()

    assert worker_supervisor._starting_projects == {}
    assert [registration.livekit_url for registration in launcher.registrations] == [
        "wss://tenant-a.livekit.test",
    ]
    release_second_start.set()

    await worker_supervisor.refresh()

    assert sorted(registration.livekit_url for registration in launcher.registrations) == [
        "wss://tenant-a.livekit.test",
        "wss://tenant-b.livekit.test",
    ]
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_failure_event_is_reconciled_after_transient_database_failure(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings()
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://invalid.livekit.test",
        api_key="invalid-key",  # pragma: allowlist secret
        credential_data="invalid-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    launcher = FakeProjectWorkerLauncher(
        failed_urls={"wss://invalid.livekit.test"},
    )
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    original = VoiceWorkerDemandStore.mark_project_failed
    attempts = 0

    async def flaky_mark_failed(
        store: VoiceWorkerDemandStore,
        *,
        project_key: str,
        owner_id: str,
        failure_code: str,
        failure_type: str | None,
    ) -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            message = "temporary database failure"
            raise RuntimeError(message)
        return await original(
            store,
            project_key=project_key,
            owner_id=owner_id,
            failure_code=failure_code,
            failure_type=failure_type,
        )

    monkeypatch.setattr(
        VoiceWorkerDemandStore,
        "mark_project_failed",
        flaky_mark_failed,
    )

    with pytest.raises(RuntimeError, match="temporary database failure"):
        await worker_supervisor.refresh()
    await worker_supervisor.refresh()

    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.FAILED
    assert launcher.handles[0].stopped is True


@pytest.mark.asyncio
async def test_invalid_tenant_settings_do_not_break_another_project(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://invalid.livekit.test",
        api_key="invalid-key",  # pragma: allowlist secret
        credential_data="invalid-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://healthy.livekit.test",
        api_key="healthy-key",  # pragma: allowlist secret
        credential_data="healthy-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id, demand_b_id = await asyncio.gather(
        request_call(client=client_a, credentials=credentials_a, suffix="a"),
        request_call(client=client_b, credentials=credentials_b, suffix="b"),
    )
    launcher = FakeProjectWorkerLauncher(
        failed_urls={"wss://invalid.livekit.test"},
    )
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    assert (await client_a.get(demand_a_id)).status == VoiceCallDemandStatus.FAILED
    assert (await client_b.get(demand_b_id)).status == VoiceCallDemandStatus.READY
    assert len(worker_supervisor._workers) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_hung_tenant_settings_resolution_does_not_block_other_project(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_settings_resolution_timeout_seconds": 0.05},
    )
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://tenant-b.livekit.test",
        api_key="tenant-b-key",  # pragma: allowlist secret
        credential_data="tenant-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id = await request_call(
        client=client_a,
        credentials=credentials_a,
        suffix="a",
    )
    demand_b_id = await request_call(
        client=client_b,
        credentials=credentials_b,
        suffix="b",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    hung_project_key = VoiceProjectKeyFactory(
        settings.voice_route_signing_secret,
    ).create(credentials_a)
    original_resolver = VoiceWorkerSupervisor._resolve_project_registration

    async def selectively_hung_resolver(
        instance: VoiceWorkerSupervisor,
        **kwargs: object,
    ) -> LiveKitProjectRegistration | None:
        if kwargs["project_key"] == hung_project_key:
            await asyncio.Event().wait()
        return await original_resolver(instance, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_resolve_project_registration",
        selectively_hung_resolver,
    )

    await worker_supervisor.refresh()

    demand_a = await client_a.get(demand_a_id)
    demand_b = await client_b.get(demand_b_id)
    assert demand_a.status == VoiceCallDemandStatus.FAILED
    assert demand_a.failure_code == "livekit_settings_resolution_timeout"
    assert demand_b.status == VoiceCallDemandStatus.READY
    assert [registration.livekit_url for registration in launcher.registrations] == [
        "wss://tenant-b.livekit.test",
    ]
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_hung_tenant_settings_resolution_does_not_block_shared_project(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_settings_resolution_timeout_seconds": 0.1},
    )
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://shared.livekit.test",
        api_key="shared-key",  # pragma: allowlist secret
        credential_data="shared-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://shared.livekit.test",
        api_key="shared-key",  # pragma: allowlist secret
        credential_data="shared-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id = await request_call(
        client=client_a,
        credentials=credentials_a,
        suffix="a",
    )
    demand_b_id = await request_call(
        client=client_b,
        credentials=credentials_b,
        suffix="b",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    original_resolver = VoiceWorkerSupervisor._resolve_tenant_project_credentials

    async def hang_first_tenant(
        instance: VoiceWorkerSupervisor,
        user_id: str,
    ) -> ResolvedLiveKitCredentials:
        if user_id == "tenant-a":
            await asyncio.Event().wait()
        return await original_resolver(instance, user_id)

    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_resolve_tenant_project_credentials",
        hang_first_tenant,
    )

    await worker_supervisor.refresh()

    demand_a = await client_a.get(demand_a_id)
    demand_b = await client_b.get(demand_b_id)
    assert demand_a.status == VoiceCallDemandStatus.FAILED
    assert demand_a.failure_code == "livekit_settings_resolution_timeout"
    assert demand_b.status == VoiceCallDemandStatus.READY
    assert len(launcher.registrations) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_missing_tenant_vault_secret_only_fails_that_call(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings()
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://tenant-b.livekit.test",
        api_key="tenant-b-key",  # pragma: allowlist secret
        credential_data="tenant-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id, demand_b_id = await asyncio.gather(
        request_call(client=client_a, credentials=credentials_a, suffix="a"),
        request_call(client=client_b, credentials=credentials_b, suffix="b"),
    )
    async with sqlite_session_factory() as session:
        result = await session.exec(
            select(UserSecretRecord).where(
                col(UserSecretRecord.user_id) == "tenant-a",
            ),
        )
        await session.delete(result.one())
        await session.commit()

    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()

    assert (await client_a.get(demand_a_id)).status == VoiceCallDemandStatus.FAILED
    assert (await client_b.get(demand_b_id)).status == VoiceCallDemandStatus.READY
    assert [item.livekit_url for item in launcher.registrations] == [
        "wss://tenant-b.livekit.test",
    ]
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_supervisor_uses_call_tenant_settings_without_global_livekit_env(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    settings = supervisor_settings()
    await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="first-configured-tenant",
        livekit_url="wss://first.livekit.test",
        api_key="first-key",  # pragma: allowlist secret
        credential_data="first-secret",
    )
    call_credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="calling-tenant",
        livekit_url="wss://caller.livekit.test",
        api_key="caller-key",  # pragma: allowlist secret
        credential_data="caller-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="calling-tenant",
        settings=settings,
    )
    await request_call(client=client, credentials=call_credentials, suffix="caller")
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    assert len(launcher.registrations) == 1
    registration = launcher.registrations[0]
    assert registration.livekit_url == "wss://caller.livekit.test"
    assert registration.api_key == "caller-key"  # pragma: allowlist secret
    assert registration.api_secret.get_secret_value() == "caller-secret"
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        assert name not in os.environ
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_tenant_project_capacity_rejects_rotated_credentials(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_active_projects_per_tenant": 1},
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    first_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://first.livekit.test",
        api_key="first-key",  # pragma: allowlist secret
        api_secret=SecretStr("first-secret"),  # pragma: allowlist secret
    )
    rotated_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://rotated.livekit.test",
        api_key="rotated-key",  # pragma: allowlist secret
        api_secret=SecretStr("rotated-secret"),  # pragma: allowlist secret
    )
    await request_call(client=client, credentials=first_credentials, suffix="first")

    with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
        await request_call(
            client=client,
            credentials=rotated_credentials,
            suffix="rotated",
        )

    assert exc_info.value.reason_code == "tenant_project_capacity_exhausted"


@pytest.mark.asyncio
async def test_tenant_project_capacity_counts_idle_retained_registration(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={
            "voice_worker_idle_ttl_seconds": 60.0,
            "voice_worker_max_active_projects_per_tenant": 1,
        },
    )
    original_credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://original.livekit.test",
        api_key="original-key",  # pragma: allowlist secret
        credential_data="original-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=original_credentials,
        suffix="original",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    ready = await client.get(demand_id)
    assert ready.worker_id is not None
    await client.mark_active(
        "runtime-original",
        ready.worker_id,
        agent_id="agent-original",
        room_name="room-original",
    )
    await client.complete("runtime-original", ready.worker_id)

    rotated_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://rotated.livekit.test",
        api_key="rotated-key",  # pragma: allowlist secret
        api_secret=SecretStr("rotated-secret"),  # pragma: allowlist secret
    )
    with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
        await request_call(
            client=client,
            credentials=rotated_credentials,
            suffix="rotated",
        )

    assert exc_info.value.reason_code == "tenant_project_capacity_exhausted"
    assert len(launcher.registrations) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_cancelled_ready_route_cannot_churn_idle_project_capacity(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={
            "voice_worker_idle_ttl_seconds": 60.0,
            "voice_worker_max_active_projects_per_tenant": 1,
        },
    )
    original_credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://original.livekit.test",
        api_key="original-key",  # pragma: allowlist secret
        credential_data="original-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    await request_call(
        client=client,
        credentials=original_credentials,
        suffix="original",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )
    await worker_supervisor.refresh()
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "tenant-a",
    )() as session:
        await client.cancel_in_session(
            session=session,
            runtime_session_id="runtime-original",
            failure_code="provider_call_start_failed",
        )
        await session.commit()

    rotated_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://rotated.livekit.test",
        api_key="rotated-key",  # pragma: allowlist secret
        api_secret=SecretStr("rotated-secret"),  # pragma: allowlist secret
    )
    with pytest.raises(VoiceWorkerUnavailableError) as capacity_error:
        await request_call(
            client=client,
            credentials=rotated_credentials,
            suffix="rotated",
        )
    assert capacity_error.value.reason_code == "tenant_project_capacity_exhausted"

    with pytest.raises(VoiceWorkerUnavailableError) as route_error:
        await client.request(
            credentials=rotated_credentials,
            agent_id="agent-a",
            runtime_session_id="runtime-original",
            room_name="room-original",
            source="test",
        )
    assert route_error.value.reason_code == "retained_project_route_conflict"
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_retry_cannot_replace_live_runtime_after_credentials_rotate(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_active_projects_per_tenant": 1},
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    original_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://original.livekit.test",
        api_key="original-key",  # pragma: allowlist secret
        api_secret=SecretStr("original-secret"),  # pragma: allowlist secret
    )
    rotated_credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://rotated.livekit.test",
        api_key="rotated-key",  # pragma: allowlist secret
        api_secret=SecretStr("rotated-secret"),  # pragma: allowlist secret
    )
    original = await client.request(
        credentials=original_credentials,
        agent_id="agent-a",
        runtime_session_id="runtime-a",
        room_name="room-a",
        source="test",
    )

    with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
        await client.request(
            credentials=rotated_credentials,
            agent_id="agent-a",
            runtime_session_id="runtime-a",
            room_name="room-a",
            source="test",
        )

    assert exc_info.value.reason_code == "retained_project_route_conflict"
    assert (await client.get(original.id)).project_key == original.project_key


@pytest.mark.asyncio
async def test_job_activation_returns_only_the_fenced_demand_source(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = supervisor_settings()
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    other_tenant_client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        api_secret=SecretStr("tenant-a-secret"),  # pragma: allowlist secret
    )
    demand = await client.request(
        credentials=credentials,
        agent_id="agent-a",
        runtime_session_id="runtime-a",
        room_name="room-a",
        source="telnyx",
    )
    now = datetime.now(UTC)
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "tenant-a",
    )() as session:
        await session.exec(
            update(VoiceCallDemandRecord)
            .where(col(VoiceCallDemandRecord.id) == demand.id)
            .values(
                status=VoiceCallDemandStatus.READY.value,
                worker_id="worker-a",
                worker_ready_expires_at=now + timedelta(seconds=30),
                expires_at=now + timedelta(seconds=60),
            ),
        )
        await session.commit()

    invalid_routes = (
        (client, "other-runtime", "worker-a", "agent-a", "room-a"),
        (client, "runtime-a", "other-worker", "agent-a", "room-a"),
        (client, "runtime-a", "worker-a", "other-agent", "room-a"),
        (client, "runtime-a", "worker-a", "agent-a", "other-room"),
        (other_tenant_client, "runtime-a", "worker-a", "agent-a", "room-a"),
    )
    for scoped_client, runtime_id, worker_id, agent_id, room_name in invalid_routes:
        with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
            await scoped_client.mark_active(
                runtime_id,
                worker_id,
                agent_id=agent_id,
                room_name=room_name,
            )
        assert exc_info.value.reason_code == "demand_not_ready"

    activated = await client.mark_active(
        "runtime-a",
        "worker-a",
        agent_id="agent-a",
        room_name="room-a",
    )

    assert activated.id == demand.id
    assert activated.source == "telnyx"
    assert activated.status == VoiceCallDemandStatus.ACTIVE
    assert activated.worker_id == "worker-a"


@pytest.mark.asyncio
async def test_job_activation_rejects_demand_that_is_not_ready(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = supervisor_settings()
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        api_secret=SecretStr("tenant-a-secret"),  # pragma: allowlist secret
    )
    await request_call(client=client, credentials=credentials, suffix="a")

    with pytest.raises(VoiceWorkerUnavailableError) as exc_info:
        await client.mark_active(
            "runtime-a",
            "worker-a",
            agent_id="agent-a",
            room_name="room-a",
        )

    assert exc_info.value.reason_code == "demand_not_ready"


@pytest.mark.asyncio
async def test_cluster_project_capacity_fails_only_excess_project(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_active_projects": 1},
    )
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-b",
        livekit_url="wss://tenant-b.livekit.test",
        api_key="tenant-b-key",  # pragma: allowlist secret
        credential_data="tenant-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-b",
        settings=settings,
    )
    demand_a_id = await request_call(
        client=client_a,
        credentials=credentials_a,
        suffix="a",
    )
    demand_b_id = await request_call(
        client=client_b,
        credentials=credentials_b,
        suffix="b",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    demands = await asyncio.gather(
        client_a.get(demand_a_id),
        client_b.get(demand_b_id),
    )
    assert {demand.status for demand in demands} == {
        VoiceCallDemandStatus.READY,
        VoiceCallDemandStatus.FAILED,
    }
    failed_demand = next(demand for demand in demands if demand.status == VoiceCallDemandStatus.FAILED)
    assert failed_demand.failure_code == "project_capacity_exhausted"
    assert len(launcher.registrations) == 1
    await worker_supervisor.stop_all()


@pytest.mark.asyncio
async def test_stale_supervisor_cannot_fail_new_owner_project(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    settings = supervisor_settings()
    credentials = ResolvedLiveKitCredentials(
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        api_secret=SecretStr("tenant-a-secret"),  # pragma: allowlist secret
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="a",
    )
    assert settings.voice_route_signing_secret is not None
    project_key = VoiceProjectKeyFactory(
        settings.voice_route_signing_secret,
    ).create(credentials)
    store = VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    )
    assert (
        await store.claim_project(
            project_key=project_key,
            owner_id="stale-owner",
            lease_ttl_seconds=30,
            max_active_projects=10,
        )
        == VoiceProjectClaimResult.CLAIMED
    )
    async with sqlite_session_factory() as session:
        await session.exec(
            update(VoiceProjectWorkerLeaseRecord)
            .where(
                col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
            )
            .values(
                owner_id="new-owner",
                lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
            ),
        )
        await session.commit()

    published = await store.mark_project_failed(
        project_key=project_key,
        owner_id="stale-owner",
        failure_code="stale_failure",
        failure_type=None,
    )

    assert published is False
    assert (await client.get(demand_id)).status == VoiceCallDemandStatus.PENDING


@pytest.mark.asyncio
async def test_project_lease_uses_database_time_not_supervisor_clock(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    first_store = VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    )
    second_store = VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    )

    first_result = await first_store.claim_project(
        project_key="a" * 64,
        owner_id="first-owner",
        lease_ttl_seconds=30,
        max_active_projects=10,
    )
    second_result = await second_store.claim_project(
        project_key="a" * 64,
        owner_id="second-owner",
        lease_ttl_seconds=30,
        max_active_projects=10,
    )

    assert first_result == VoiceProjectClaimResult.CLAIMED
    assert second_result == VoiceProjectClaimResult.BUSY


@pytest.mark.asyncio
async def test_starting_project_lease_is_heartbeated_during_slow_secret_resolution(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings().model_copy(
        update={
            "voice_worker_poll_interval_seconds": 0.01,
            "voice_worker_lease_ttl_seconds": 0.3,
            "voice_worker_shutdown_timeout_seconds": 0.05,
        },
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-a",
        livekit_url="wss://tenant-a.livekit.test",
        api_key="tenant-a-key",  # pragma: allowlist secret
        credential_data="tenant-a-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-a",
        settings=settings,
    )
    await request_call(client=client, credentials=credentials, suffix="a")
    first_launcher = FakeProjectWorkerLauncher()
    second_launcher = FakeProjectWorkerLauncher()
    first_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=first_launcher,
        supervisor_id="first-supervisor",
    )
    second_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=second_launcher,
        supervisor_id="second-supervisor",
    )
    original_resolver = VoiceWorkerSupervisor._resolve_project_registration
    resolution_started = asyncio.Event()

    async def slow_resolver(
        instance: VoiceWorkerSupervisor,
        **kwargs: object,
    ) -> LiveKitProjectRegistration | None:
        if instance.supervisor_id == "first-supervisor":
            resolution_started.set()
            await asyncio.sleep(0.45)
        return await original_resolver(instance, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        VoiceWorkerSupervisor,
        "_resolve_project_registration",
        slow_resolver,
    )
    first_task = asyncio.create_task(first_supervisor.run_forever())
    try:
        await asyncio.wait_for(resolution_started.wait(), timeout=1)
        await asyncio.sleep(0.35)

        await second_supervisor.refresh()
        await asyncio.wait_for(
            wait_for_registration(first_launcher),
            timeout=1,
        )

        assert len(first_launcher.registrations) == 1
        assert second_launcher.registrations == []
    finally:
        first_supervisor.stop()
        await asyncio.wait_for(first_task, timeout=1)


@pytest.mark.asyncio
async def test_contended_lease_heartbeat_does_not_block_other_projects(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = supervisor_settings().model_copy(
        update={
            "voice_worker_poll_interval_seconds": 0.01,
            "voice_worker_lease_ttl_seconds": 0.3,
            "voice_worker_shutdown_timeout_seconds": 0.05,
            "voice_worker_lease_renewal_concurrency": 2,
        },
    )
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=FakeProjectWorkerLauncher(),
    )
    loop = asyncio.get_running_loop()
    worker_supervisor._starting_projects.update(
        {
            "a" * 64: loop.time() + 0.3,
            "b" * 64: loop.time() + 0.3,
        },
    )
    blocked = asyncio.Event()
    second_renewed = asyncio.Event()

    async def renew_project_lease(
        _store: VoiceWorkerDemandStore,
        *,
        project_key: str,
        owner_id: str,
        lease_ttl_seconds: float,
        ready_worker_id: str | None = None,
    ) -> bool:
        assert owner_id == worker_supervisor.supervisor_id
        assert lease_ttl_seconds == settings.voice_worker_lease_ttl_seconds
        assert ready_worker_id is None
        if project_key == "a" * 64:
            await blocked.wait()
        second_renewed.set()
        return True

    monkeypatch.setattr(
        VoiceWorkerDemandStore,
        "renew_project_lease",
        renew_project_lease,
    )
    heartbeat_task = asyncio.create_task(
        worker_supervisor._heartbeat_project_leases(),
    )
    try:
        await asyncio.wait_for(second_renewed.wait(), timeout=0.25)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_ready_lease_renewal_does_not_churn_active_demand(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = datetime.now(UTC)
    project_key = "c" * 64
    worker_id = "worker-c"
    async with sqlite_session_factory() as session:
        session.add(
            VoiceProjectWorkerLeaseRecord(
                project_key=project_key,
                owner_id="supervisor-c",
                status="ready",
                worker_id=worker_id,
                lease_expires_at=now + timedelta(minutes=1),
            ),
        )
        session.add(
            VoiceCallDemandRecord(
                user_id="tenant-c",
                agent_id="agent-active",
                runtime_session_id="runtime-active",
                room_name="room-active",
                source="test",
                project_key=project_key,
                status=VoiceCallDemandStatus.ACTIVE.value,
                worker_id=worker_id,
                project_claimed_at=now,
                expires_at=now + timedelta(minutes=1),
                updated_at=now,
            ),
        )
        session.add(
            VoiceCallDemandRecord(
                user_id="tenant-c",
                agent_id="agent-ready",
                runtime_session_id="runtime-ready",
                room_name="room-ready",
                source="test",
                project_key=project_key,
                status=VoiceCallDemandStatus.READY.value,
                worker_id=worker_id,
                worker_ready_expires_at=now + timedelta(seconds=1),
                project_claimed_at=now,
                expires_at=now + timedelta(minutes=1),
                updated_at=now,
            ),
        )
        await session.commit()
        active_result = await session.exec(
            select(VoiceCallDemandRecord).where(
                col(VoiceCallDemandRecord.runtime_session_id) == "runtime-active",
            ),
        )
        active_updated_at = active_result.one().updated_at

    store = VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    )
    assert await store.renew_project_lease(
        project_key=project_key,
        owner_id="supervisor-c",
        lease_ttl_seconds=30,
        ready_worker_id=worker_id,
    )

    async with sqlite_session_factory() as session:
        result = await session.exec(
            select(VoiceCallDemandRecord).where(
                col(VoiceCallDemandRecord.project_key) == project_key,
            ),
        )
        records = {record.runtime_session_id: record for record in result.all()}
    assert records["runtime-active"].updated_at == active_updated_at
    assert records["runtime-ready"].worker_ready_expires_at is not None
    assert records["runtime-ready"].worker_ready_expires_at > now.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_maintenance_prunes_only_expired_project_leases(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    now = datetime.now(UTC)
    async with sqlite_session_factory() as session:
        session.add(
            VoiceProjectWorkerLeaseRecord(
                project_key="d" * 64,
                owner_id="expired-owner",
                lease_expires_at=now - timedelta(seconds=1),
            ),
        )
        session.add(
            VoiceProjectWorkerLeaseRecord(
                project_key="e" * 64,
                owner_id="live-owner",
                lease_expires_at=now + timedelta(minutes=1),
            ),
        )
        await session.commit()

    store = VoiceWorkerDemandStore(
        session_factory=voice_worker_session_factory(sqlite_session_factory),
    )
    assert await store.prune_expired_project_leases() == 1

    async with sqlite_session_factory() as session:
        result = await session.exec(select(VoiceProjectWorkerLeaseRecord.project_key))
    assert result.all() == ["e" * 64]


@pytest.mark.asyncio
async def test_per_supervisor_project_cap_distributes_projects_across_replicas(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_worker_max_active_projects_per_supervisor": 1},
    )
    credentials_a = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-local-cap-a",
        livekit_url="wss://local-cap-a.livekit.test",
        api_key="local-cap-a-key",  # pragma: allowlist secret
        credential_data="local-cap-a-secret",
    )
    credentials_b = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-local-cap-b",
        livekit_url="wss://local-cap-b.livekit.test",
        api_key="local-cap-b-key",  # pragma: allowlist secret
        credential_data="local-cap-b-secret",
    )
    client_a = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-local-cap-a",
        settings=settings,
    )
    client_b = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-local-cap-b",
        settings=settings,
    )
    demand_a = await request_call(
        client=client_a,
        credentials=credentials_a,
        suffix="local-cap-a",
    )
    demand_b = await request_call(
        client=client_b,
        credentials=credentials_b,
        suffix="local-cap-b",
    )
    launcher_a = FakeProjectWorkerLauncher()
    launcher_b = FakeProjectWorkerLauncher()
    supervisor_a = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher_a,
        supervisor_id="local-cap-supervisor-a",
    )
    supervisor_b = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher_b,
        supervisor_id="local-cap-supervisor-b",
    )

    await supervisor_a.refresh()
    await supervisor_b.refresh()

    assert len(launcher_a.registrations) == 1
    assert len(launcher_b.registrations) == 1
    assert launcher_a.registrations[0].project_key != launcher_b.registrations[0].project_key
    assert (await client_a.get(demand_a)).status == VoiceCallDemandStatus.READY
    assert (await client_b.get(demand_b)).status == VoiceCallDemandStatus.READY
    await supervisor_a.stop_all()
    await supervisor_b.stop_all()


@pytest.mark.asyncio
async def test_public_endpoint_policy_rejects_before_child_launch(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    settings = supervisor_settings().model_copy(
        update={"voice_livekit_endpoint_policy": LiveKitEndpointPolicy.PUBLIC_WSS},
    )
    credentials = await save_settings(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        user_id="tenant-private-endpoint",
        livekit_url="ws://127.0.0.1:7880",
        api_key="private-endpoint-key",  # pragma: allowlist secret
        credential_data="private-endpoint-secret",
    )
    client = demand_client(
        session_factory=sqlite_session_factory,
        user_id="tenant-private-endpoint",
        settings=settings,
    )
    demand_id = await request_call(
        client=client,
        credentials=credentials,
        suffix="private-endpoint",
    )
    launcher = FakeProjectWorkerLauncher()
    worker_supervisor = supervisor(
        settings=settings,
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        launcher=launcher,
    )

    await worker_supervisor.refresh()

    demand = await client.get(demand_id)
    assert demand.status == VoiceCallDemandStatus.FAILED
    assert demand.failure_code == "livekit_endpoint_not_allowed"
    assert launcher.registrations == []


async def wait_for_registration(launcher: FakeProjectWorkerLauncher) -> None:
    while not launcher.registrations:
        await asyncio.sleep(0.01)

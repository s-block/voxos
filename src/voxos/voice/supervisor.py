import asyncio
import logging
import math
import multiprocessing
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from multiprocessing.process import BaseProcess
from typing import Protocol, cast
from uuid import uuid4

import orjson
import sentry_sdk
from livekit import agents
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import (
    tenant_scoped_session_factory,
    voice_worker_session_factory,
)
from voxos.observability.sentry import initialize_sentry
from voxos.voice.demand import (
    VoiceCallDemand,
    VoiceCallDemandClient,
    VoiceCallDemandStatus,
    VoiceProjectClaimResult,
    VoiceProjectDemand,
    VoiceProjectKeyFactory,
    VoiceWorkerDemandStore,
    VoiceWorkerWarmRequest,
)
from voxos.voice.endpoint_policy import (
    livekit_log_target,
    validate_livekit_worker_endpoint,
)
from voxos.voice.errors import (
    DisallowedLiveKitEndpointError,
    InvalidLiveKitCredentialsError,
    InvalidVoiceRouteSignatureError,
    MissingLiveKitCredentialsError,
    MissingVoiceRouteSignerError,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import ResolvedLiveKitCredentials
from voxos.voice.route_signing import LiveKitRouteSigner, SignedVoiceRoute
from voxos.voice.settings import (
    VoiceAgentSettings,
    validate_voice_worker_lifecycle_settings,
)

logger = logging.getLogger(__name__)
LIVEKIT_CREDENTIAL_ENV_NAMES = (
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
)
PROJECT_WORKER_STOP = "stop"
VoiceEntrypoint = Callable[[agents.JobContext], Awaitable[None]]
VoiceJobPrewarm = Callable[[agents.JobProcess], None]


class ProjectLeaseLostError(RuntimeError):
    """Raised when this supervisor no longer owns a project's routing lease."""


def default_supervisor_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"


class ProjectWorkerEventKind(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    ACTIVE_JOBS = "active_jobs"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ProjectWorkerEvent:
    kind: ProjectWorkerEventKind
    worker_id: str | None = None
    active_jobs: int = 0
    failure_code: str | None = None
    failure_type: str | None = None


@dataclass(frozen=True, slots=True)
class LiveKitProjectRegistration:
    project_key: str
    livekit_url: str = field(repr=False)
    api_key: str = field(repr=False)
    api_secret: SecretStr = field(repr=False)
    registration_timeout_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class ProjectWorkerCredentialsMessage:
    livekit_url: str = field(repr=False)
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ProjectWorkerLaunchOptions:
    project_key: str
    agent_name: str
    start_timeout_seconds: float
    shutdown_timeout_seconds: float
    route_metadata_key: str
    route_signing_secret: SecretStr = field(repr=False)
    route_ttl_seconds: int
    route_clock_skew_seconds: int
    demand_ttl_seconds: int
    max_active_projects_per_tenant: int
    idle_project_ttl_seconds: float
    poll_interval_seconds: float
    idle_processes: int


@dataclass(frozen=True, slots=True)
class ProjectJobEnvironment:
    """Install one project's credentials inside its isolated job process."""

    livekit_url: str = field(repr=False)
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    prewarm_fnc: VoiceJobPrewarm | None = field(default=None, repr=False)

    def __call__(self, process: agents.JobProcess) -> None:
        os.environ["LIVEKIT_URL"] = self.livekit_url
        os.environ["LIVEKIT_API_KEY"] = self.api_key
        os.environ["LIVEKIT_API_SECRET"] = self.api_secret
        if self.prewarm_fnc is not None:
            self.prewarm_fnc(process)


@dataclass(slots=True)
class ProjectJobAdmissionController:
    """Authorize and durably reserve a route before LiveKit spawns its job."""

    options: ProjectWorkerLaunchOptions
    session_factory: AsyncSessionFactory
    worker_id: str | None = None

    def registered(self, worker_id: str) -> None:
        self.worker_id = worker_id

    async def __call__(self, request: agents.JobRequest) -> None:
        route = self._verified_route(request)
        if route is None:
            await self._reject(request, reason="invalid_or_missing_signed_route")
            return
        if self.worker_id is None:
            await self._reject(
                request,
                route=route,
                reason="worker_registration_unavailable",
            )
            return
        if request.agent_name != self.options.agent_name:
            await self._reject(
                request,
                route=route,
                reason="agent_name_mismatch",
            )
            return

        demand_client = VoiceCallDemandClient(
            session_factory=tenant_scoped_session_factory(
                session_factory=self.session_factory,
                user_id=route.user_id,
            ),
            user_id=route.user_id,
            project_key_factory=VoiceProjectKeyFactory(
                self.options.route_signing_secret,
            ),
            demand_ttl_seconds=self.options.demand_ttl_seconds,
            poll_interval_seconds=self.options.poll_interval_seconds,
            start_timeout_seconds=self.options.start_timeout_seconds,
            max_active_projects_per_tenant=(self.options.max_active_projects_per_tenant),
            idle_project_ttl_seconds=self.options.idle_project_ttl_seconds,
        )
        try:
            reserved = await demand_client.reserve_for_worker(
                runtime_session_id=route.runtime_session_id,
                agent_id=route.agent_id,
                room_name=route.room_name,
                project_key=self.options.project_key,
                worker_id=self.worker_id,
            )
        except Exception:
            logger.exception(
                "Failed to authorize LiveKit job request",
                extra=self._log_context(route, reason="admission_store_failed"),
            )
            await request.reject(terminate=True)
            return
        if not reserved:
            await self._reject(
                request,
                route=route,
                reason="durable_route_not_ready",
            )
            return

        logger.info(
            "Reserved LiveKit call route for job assignment",
            extra=self._log_context(route, reason="route_reserved"),
        )
        try:
            await request.accept()
        except Exception:
            # LiveKit can accept availability but fail to assign a job. The
            # durable ACTIVE reservation then expires by its normal demand TTL.
            logger.exception(
                "LiveKit job assignment failed after route reservation",
                extra=self._log_context(route, reason="assignment_failed"),
            )
            raise

    def _verified_route(self, request: agents.JobRequest) -> SignedVoiceRoute | None:
        room_name = request.room.name.strip()
        if not room_name:
            return None
        signer = LiveKitRouteSigner(
            secret=self.options.route_signing_secret,
            ttl_seconds=self.options.route_ttl_seconds,
            clock_skew_seconds=self.options.route_clock_skew_seconds,
        )
        for metadata in (request.job.metadata, request.room.metadata):
            try:
                payload: object = orjson.loads(metadata)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            token = payload.get(self.options.route_metadata_key)
            if not isinstance(token, str) or not token.strip():
                continue
            try:
                return signer.verify(token.strip(), room_name=room_name)
            except InvalidVoiceRouteSignatureError:
                return None
        return None

    async def _reject(
        self,
        request: agents.JobRequest,
        *,
        reason: str,
        route: SignedVoiceRoute | None = None,
    ) -> None:
        logger.warning(
            "Rejected LiveKit job request before process launch",
            extra=self._log_context(route, reason=reason),
        )
        await request.reject(terminate=True)

    def _log_context(
        self,
        route: SignedVoiceRoute | None,
        *,
        reason: str,
    ) -> dict[str, str | None]:
        return {
            "user_id": route.user_id if route is not None else None,
            "runtime_session_id": (route.runtime_session_id if route is not None else None),
            "project_key": self.options.project_key,
            "reason": reason,
        }


class ProjectWorkerHandle(Protocol):
    project_key: str
    livekit_url: str

    def poll_events(self) -> tuple[ProjectWorkerEvent, ...]:
        """Return non-blocking project-worker lifecycle events."""

    async def stop(self, timeout_seconds: float) -> None:
        """Drain, close, and stop the isolated project worker."""


class ProjectWorkerLauncher(Protocol):
    def start(self, registration: LiveKitProjectRegistration) -> ProjectWorkerHandle:
        """Start one isolated worker for the supplied LiveKit project."""


@dataclass(slots=True)
class ChildProcessProjectWorkerHandle:
    project_key: str
    livekit_url: str = field(repr=False)
    process: BaseProcess
    connection: Connection
    _terminal_reported: bool = False

    def poll_events(self) -> tuple[ProjectWorkerEvent, ...]:
        events: list[ProjectWorkerEvent] = []
        try:
            while self.connection.poll():
                message = self.connection.recv()
                if isinstance(message, ProjectWorkerEvent):
                    events.append(message)
                    if message.kind in {
                        ProjectWorkerEventKind.FAILED,
                        ProjectWorkerEventKind.STOPPED,
                    }:
                        self._terminal_reported = True
        except (EOFError, OSError):
            pass

        if not self.process.is_alive() and not self._terminal_reported:
            events.append(
                ProjectWorkerEvent(
                    kind=ProjectWorkerEventKind.FAILED,
                    failure_code="worker_process_exited",
                    failure_type="ChildProcessExit",
                ),
            )
            self._terminal_reported = True
        return tuple(events)

    async def stop(self, timeout_seconds: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        if self.process.is_alive():
            try:
                self.connection.send(PROJECT_WORKER_STOP)
            except (BrokenPipeError, EOFError, OSError):
                pass
        await asyncio.to_thread(
            self.process.join,
            max(deadline - loop.time(), 0.0),
        )
        if self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(
                self.process.join,
                max(deadline - loop.time(), 0.0),
            )
        if self.process.is_alive():
            self.process.kill()
            await asyncio.to_thread(
                self.process.join,
                max(deadline - loop.time(), 0.0),
            )
        self.connection.close()


def project_worker_idle_processes(settings: VoiceAgentSettings) -> int:
    return max(
        settings.voice_worker_idle_processes,
        int(settings.voice_warm_user_workers),
    )


@dataclass(frozen=True, slots=True)
class ChildProcessProjectWorkerLauncher:
    settings: VoiceAgentSettings
    entrypoint: VoiceEntrypoint
    prewarm_fnc: VoiceJobPrewarm | None = None
    multiprocessing_context: str = "spawn"

    def start(self, registration: LiveKitProjectRegistration) -> ProjectWorkerHandle:
        context = cast(
            SpawnContext,
            multiprocessing.get_context(self.multiprocessing_context),
        )
        parent_connection, child_connection = context.Pipe(duplex=True)
        if self.settings.voice_route_signing_secret is None:
            raise MissingVoiceRouteSignerError
        options = ProjectWorkerLaunchOptions(
            project_key=registration.project_key,
            agent_name=self.settings.voice_agent_name,
            start_timeout_seconds=registration.registration_timeout_seconds,
            shutdown_timeout_seconds=self.settings.voice_worker_shutdown_timeout_seconds,
            route_metadata_key=self.settings.voice_route_metadata_key,
            route_signing_secret=self.settings.voice_route_signing_secret,
            route_ttl_seconds=self.settings.voice_token_ttl_seconds,
            route_clock_skew_seconds=self.settings.voice_route_clock_skew_seconds,
            demand_ttl_seconds=self.settings.voice_worker_demand_ttl_seconds,
            max_active_projects_per_tenant=(self.settings.voice_worker_max_active_projects_per_tenant),
            idle_project_ttl_seconds=self.settings.voice_worker_idle_ttl_seconds,
            poll_interval_seconds=self.settings.voice_worker_poll_interval_seconds,
            idle_processes=project_worker_idle_processes(self.settings),
        )
        process = context.Process(
            target=run_project_worker_process,
            args=(child_connection, options, self.entrypoint, self.prewarm_fnc),
            name=f"voxos-livekit-{registration.project_key[:12]}",
            daemon=False,
        )
        try:
            process.start()
        except BaseException:
            child_connection.close()
            parent_connection.close()
            raise
        child_connection.close()
        try:
            parent_connection.send(
                ProjectWorkerCredentialsMessage(
                    livekit_url=registration.livekit_url,
                    api_key=registration.api_key,
                    api_secret=registration.api_secret.get_secret_value(),
                ),
            )
        except BaseException:
            if process.is_alive():
                process.terminate()
                process.join(timeout=self.settings.voice_worker_shutdown_timeout_seconds)
            parent_connection.close()
            raise
        return ChildProcessProjectWorkerHandle(
            project_key=registration.project_key,
            livekit_url=livekit_log_target(registration.livekit_url),
            process=process,
            connection=parent_connection,
        )


@dataclass(slots=True)
class RunningProjectWorker:
    handle: ProjectWorkerHandle
    lease_valid_until: float
    ready: bool = False
    ready_published: bool = False
    unready_pending: bool = False
    worker_id: str | None = None
    active_jobs: int = 0
    idle_since: datetime | None = None
    failure_code: str | None = None
    failure_type: str | None = None
    lease_lost: bool = False
    stopping: bool = False


@dataclass(slots=True)
class VoiceWorkerSupervisor:
    settings: VoiceAgentSettings
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    entrypoint: VoiceEntrypoint
    prewarm_fnc: VoiceJobPrewarm | None = None
    launcher: ProjectWorkerLauncher | None = None
    supervisor_id: str = field(default_factory=default_supervisor_id)
    clock: Callable[[], datetime] = utc_now
    _workers: dict[str, RunningProjectWorker] = field(default_factory=dict)
    _starting_projects: dict[str, float] = field(default_factory=dict)
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)
    _claim_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _last_maintenance_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_voice_supervisor_settings(self.settings)
        if self.launcher is None:
            self.launcher = ChildProcessProjectWorkerLauncher(
                settings=self.settings,
                entrypoint=self.entrypoint,
                prewarm_fnc=self.prewarm_fnc,
            )

    async def run_forever(self) -> None:
        await self._run(stop_when_idle=False)

    async def run_until_idle(self) -> None:
        """Serve current demand and exit after all retained projects become idle."""
        await self._run(stop_when_idle=True)

    async def _run(self, *, stop_when_idle: bool) -> None:
        logger.info(
            "Started demand-driven LiveKit worker supervisor",
            extra={"supervisor_id": self.supervisor_id},
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_project_leases(),
            name="livekit-project-lease-heartbeat",
        )
        try:
            while not self._stopping.is_set():
                try:
                    await self.refresh()
                except Exception:
                    logger.exception(
                        "LiveKit worker supervisor refresh failed",
                        extra={"supervisor_id": self.supervisor_id},
                    )
                    if stop_when_idle:
                        raise
                if stop_when_idle and not self._workers and not self._starting_projects:
                    return
                try:
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self.settings.voice_worker_poll_interval_seconds,
                    )
                except TimeoutError:
                    continue
        finally:
            try:
                await self.stop_all()
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)

    async def _heartbeat_project_leases(self) -> None:
        interval_seconds = self.settings.voice_worker_lease_ttl_seconds / 3
        loop = asyncio.get_running_loop()
        next_heartbeat_at = loop.time() + interval_seconds
        while True:
            await asyncio.sleep(max(next_heartbeat_at - loop.time(), 0.0))
            next_heartbeat_at += interval_seconds
            store = self._store()
            semaphore = asyncio.Semaphore(
                self.settings.voice_worker_lease_renewal_concurrency,
            )
            starting_targets = tuple(
                (project_key, lease_valid_until)
                for project_key, lease_valid_until in self._starting_projects.items()
                if project_key not in self._workers
            )
            running_targets = tuple(
                (project_key, running) for project_key, running in self._workers.items() if not running.lease_lost
            )
            await asyncio.gather(
                *(
                    self._renew_starting_project_lease(
                        store=store,
                        semaphore=semaphore,
                        project_key=project_key,
                        lease_valid_until=lease_valid_until,
                        timeout_seconds=interval_seconds,
                    )
                    for project_key, lease_valid_until in starting_targets
                ),
                *(
                    self._renew_running_project_lease(
                        store=store,
                        semaphore=semaphore,
                        project_key=project_key,
                        running=running,
                        timeout_seconds=interval_seconds,
                    )
                    for project_key, running in running_targets
                ),
            )

    async def _renew_starting_project_lease(
        self,
        *,
        store: VoiceWorkerDemandStore,
        semaphore: asyncio.Semaphore,
        project_key: str,
        lease_valid_until: float,
        timeout_seconds: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        renewal_started_at = loop.time()
        timeout = min(
            timeout_seconds,
            max(lease_valid_until - renewal_started_at, 0.0),
        )
        if timeout <= 0:
            if self._starting_projects.get(project_key) == lease_valid_until:
                self._starting_projects[project_key] = 0.0
            return
        try:
            async with asyncio.timeout(timeout):
                async with semaphore:
                    renewed = await store.renew_project_lease(
                        project_key=project_key,
                        owner_id=self.supervisor_id,
                        lease_ttl_seconds=(self.settings.voice_worker_lease_ttl_seconds),
                    )
        except TimeoutError:
            logger.warning(
                "Timed out heartbeating starting LiveKit project lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            if loop.time() >= lease_valid_until and project_key in self._starting_projects:
                self._starting_projects[project_key] = 0.0
            return
        except Exception:
            logger.exception(
                "Failed to heartbeat starting LiveKit project lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            if loop.time() >= lease_valid_until and project_key in self._starting_projects:
                self._starting_projects[project_key] = 0.0
            return
        renewed_deadline = renewal_started_at + self.settings.voice_worker_lease_ttl_seconds
        if project_key not in self._starting_projects:
            return
        if not renewed or loop.time() >= renewed_deadline:
            self._starting_projects[project_key] = 0.0
            logger.warning(
                "Lost starting LiveKit project worker lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            return
        self._starting_projects[project_key] = renewed_deadline

    async def _renew_running_project_lease(
        self,
        *,
        store: VoiceWorkerDemandStore,
        semaphore: asyncio.Semaphore,
        project_key: str,
        running: RunningProjectWorker,
        timeout_seconds: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        renewal_started_at = loop.time()
        timeout = min(
            timeout_seconds,
            max(running.lease_valid_until - renewal_started_at, 0.0),
        )
        if timeout <= 0:
            if self._workers.get(project_key) is running:
                running.lease_lost = True
            return
        ready_worker_id = (
            running.worker_id if running.ready and running.ready_published and running.worker_id is not None else None
        )
        try:
            async with asyncio.timeout(timeout):
                async with semaphore:
                    renewed = await store.renew_project_lease(
                        project_key=project_key,
                        owner_id=self.supervisor_id,
                        lease_ttl_seconds=(self.settings.voice_worker_lease_ttl_seconds),
                        ready_worker_id=ready_worker_id,
                    )
        except TimeoutError:
            logger.warning(
                "Timed out heartbeating LiveKit project worker lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            if loop.time() >= running.lease_valid_until and self._workers.get(project_key) is running:
                running.lease_lost = True
            return
        except Exception:
            logger.exception(
                "Failed to heartbeat LiveKit project worker lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            if loop.time() >= running.lease_valid_until and self._workers.get(project_key) is running:
                running.lease_lost = True
            return
        current = self._workers.get(project_key)
        renewed_deadline = renewal_started_at + self.settings.voice_worker_lease_ttl_seconds
        if current is running and (not renewed or loop.time() >= renewed_deadline):
            running.lease_lost = True
            logger.warning(
                "Lost LiveKit project worker lease",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
        elif current is running:
            running.lease_valid_until = renewed_deadline

    async def refresh(self) -> None:
        store = self._store()
        self._collect_worker_events()
        await self._reconcile_unready_workers(store)
        await self._reconcile_failed_workers(store)
        demands = await store.list_live_demands()
        demands_by_project = group_demands_by_project(demands)
        now = self.clock()
        stop_candidates: list[tuple[str, bool]] = []

        for project_key, running in tuple(self._workers.items()):
            if running.stopping:
                continue
            if running.lease_lost:
                stop_candidates.append((project_key, False))
                continue
            project_demands = demands_by_project.get(project_key, ())
            has_pending_demand = any(
                isinstance(demand, VoiceCallDemand) and demand.status == VoiceCallDemandStatus.PENDING
                for demand in project_demands
            )
            if has_pending_demand and running.ready and running.ready_published and running.worker_id is not None:
                published = await store.mark_project_ready(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    worker_id=running.worker_id,
                    max_active_calls=(self.settings.voice_worker_max_calls_per_project),
                    observed_active_jobs=running.active_jobs,
                )
                if not published:
                    await self._stop_project(
                        project_key,
                        store=store,
                        release_lease=False,
                    )
                    continue

            has_demand = bool(project_demands)
            if has_demand or running.active_jobs > 0:
                running.idle_since = None
                continue
            if running.idle_since is None:
                running.idle_since = now
                continue
            idle_seconds = (now - running.idle_since).total_seconds()
            if idle_seconds >= self.settings.voice_worker_idle_ttl_seconds:
                stop_candidates.append((project_key, True))

        if stop_candidates:
            await asyncio.gather(
                *(
                    self._stop_project(
                        project_key,
                        store=store,
                        release_lease=release_lease,
                    )
                    for project_key, release_lease in stop_candidates
                ),
            )

        lease_owners = await store.live_project_lease_owners(
            tuple(demands_by_project),
        )
        start_candidates = tuple(
            (project_key, project_demands)
            for project_key, project_demands in demands_by_project.items()
            if project_key not in self._workers
            and project_key not in self._starting_projects
            and lease_owners.get(project_key) in {None, self.supervisor_id}
        )
        owned_project_count = len(set(self._workers) | set(self._starting_projects))
        available_local_slots = max(
            self.settings.voice_worker_max_active_projects_per_supervisor - owned_project_count,
            0,
        )
        projects_to_start = start_candidates[:available_local_slots]
        if len(start_candidates) > len(projects_to_start):
            logger.info(
                "Deferred LiveKit project demand at supervisor capacity",
                extra={
                    "supervisor_id": self.supervisor_id,
                    "deferred_project_count": (len(start_candidates) - len(projects_to_start)),
                    "local_project_limit": (self.settings.voice_worker_max_active_projects_per_supervisor),
                },
            )
        if projects_to_start:
            start_concurrency = await self._project_start_concurrency()
            start_semaphore = asyncio.Semaphore(
                start_concurrency,
            )

            async def start_project(
                project_key: str,
                project_demands: tuple[VoiceProjectDemand, ...],
            ) -> None:
                try:
                    async with start_semaphore:
                        await self._start_project(
                            project_key=project_key,
                            demands=project_demands,
                            store=store,
                        )
                except asyncio.CancelledError:
                    if project_key not in self._workers:
                        self._starting_projects.pop(project_key, None)
                        try:
                            await asyncio.shield(
                                store.release_project_lease(
                                    project_key=project_key,
                                    owner_id=self.supervisor_id,
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to release cancelled project start lease",
                                extra={
                                    "project_key": project_key,
                                    "supervisor_id": self.supervisor_id,
                                },
                            )
                    raise

            start_errors: list[Exception] = []
            if start_concurrency == 1:
                for project_key, project_demands in projects_to_start:
                    try:
                        await start_project(project_key, project_demands)
                    except Exception as error:
                        start_errors.append(error)
                    await self._publish_completed_starts(store)
            else:
                pending_tasks = {
                    asyncio.create_task(
                        start_project(project_key, project_demands),
                        name=f"livekit-project-start-{project_key[:12]}",
                    )
                    for project_key, project_demands in projects_to_start
                }
                try:
                    while pending_tasks:
                        completed_tasks, pending_tasks = await asyncio.wait(
                            pending_tasks,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for task in completed_tasks:
                            try:
                                task.result()
                            except Exception as error:
                                start_errors.append(error)
                        await self._publish_completed_starts(store)
                finally:
                    for task in pending_tasks:
                        task.cancel()
                    await asyncio.gather(
                        *pending_tasks,
                        return_exceptions=True,
                    )
            for start_error in start_errors:
                logger.error(
                    "LiveKit project start reconciliation failed",
                    exc_info=(
                        type(start_error),
                        start_error,
                        start_error.__traceback__,
                    ),
                )
            if start_errors:
                raise start_errors[0]

        self._collect_worker_events()
        await self._reconcile_unready_workers(store)
        await self._reconcile_failed_workers(store)
        await self._publish_ready_workers(store)
        if (
            self._last_maintenance_at is None
            or (now - self._last_maintenance_at).total_seconds()
            >= self.settings.voice_worker_maintenance_interval_seconds
        ):
            deleted = await store.prune_terminal_demands(
                retention_seconds=self.settings.voice_worker_demand_retention_seconds,
            )
            deleted_leases = await store.prune_expired_project_leases()
            self._last_maintenance_at = now
            if deleted:
                logger.info(
                    "Pruned terminal LiveKit call demands",
                    extra={"deleted_count": deleted},
                )
            if deleted_leases:
                logger.info(
                    "Pruned expired LiveKit project leases",
                    extra={"deleted_count": deleted_leases},
                )

    async def _publish_completed_starts(
        self,
        store: VoiceWorkerDemandStore,
    ) -> None:
        self._collect_worker_events()
        await self._reconcile_unready_workers(store)
        await self._reconcile_failed_workers(store)
        await self._publish_ready_workers(store)

    async def _project_start_concurrency(self) -> int:
        async with self.session_factory() as session:
            dialect_name = session.get_bind().dialect.name
        if dialect_name == "sqlite":
            # In-memory SQLite uses one connection and cannot model independent
            # concurrent transactions. Production PostgreSQL keeps configured
            # per-project startup concurrency.
            return 1
        return self.settings.voice_worker_start_concurrency

    def stop(self) -> None:
        self._stopping.set()

    async def stop_all(self) -> None:
        store = self._store()
        results = await asyncio.gather(
            *(self._stop_project(project_key, store=store, release_lease=True) for project_key in tuple(self._workers)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to stop LiveKit project worker",
                    exc_info=(type(result), result, result.__traceback__),
                )
        logger.info(
            "Stopped demand-driven LiveKit worker supervisor",
            extra={"supervisor_id": self.supervisor_id},
        )

    async def _start_project(
        self,
        *,
        project_key: str,
        demands: tuple[VoiceProjectDemand, ...],
        store: VoiceWorkerDemandStore,
    ) -> None:
        loop = asyncio.get_running_loop()
        claim_started_at = loop.time()
        async with self._claim_lock:
            claim_result = await store.claim_project(
                project_key=project_key,
                owner_id=self.supervisor_id,
                lease_ttl_seconds=self.settings.voice_worker_lease_ttl_seconds,
                max_active_projects=self.settings.voice_worker_max_active_projects,
            )
        if claim_result == VoiceProjectClaimResult.BUSY:
            logger.info(
                "LiveKit project worker lease is owned by another supervisor",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            return
        if claim_result == VoiceProjectClaimResult.CAPACITY_EXHAUSTED:
            logger.warning(
                "Rejected LiveKit project worker at cluster capacity",
                extra={
                    "project_key": project_key,
                    "demand_count": len(demands),
                    "supervisor_id": self.supervisor_id,
                },
            )
            return
        claimed_deadline = claim_started_at + self.settings.voice_worker_lease_ttl_seconds
        if loop.time() >= claimed_deadline:
            logger.warning(
                "LiveKit project lease expired while being claimed",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            await store.release_project_lease(
                project_key=project_key,
                owner_id=self.supervisor_id,
            )
            return
        self._starting_projects[project_key] = claimed_deadline

        try:
            resolution_timeout = self.settings.voice_worker_settings_resolution_timeout_seconds
            registration = await asyncio.wait_for(
                self._resolve_project_registration(
                    project_key=project_key,
                    demands=demands,
                    store=store,
                    resolution_deadline=loop.time() + resolution_timeout,
                ),
                timeout=(resolution_timeout + self.settings.voice_worker_poll_interval_seconds),
            )
        except ProjectLeaseLostError:
            self._starting_projects.pop(project_key, None)
            return
        except TimeoutError:
            logger.warning(
                "Timed out resolving tenant LiveKit project settings",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            try:
                await store.mark_project_failed(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    failure_code="livekit_settings_resolution_timeout",
                    failure_type="TimeoutError",
                )
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            finally:
                self._starting_projects.pop(project_key, None)
            return
        except SQLAlchemyError:
            self._starting_projects.pop(project_key, None)
            raise
        except Exception as exc:
            logger.exception(
                "Failed to resolve tenant LiveKit project settings",
                extra={
                    "project_key": project_key,
                    "failure_type": type(exc).__name__,
                    "supervisor_id": self.supervisor_id,
                },
            )
            try:
                await store.mark_project_failed(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    failure_code="livekit_settings_resolution_failed",
                    failure_type=type(exc).__name__,
                )
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            finally:
                self._starting_projects.pop(project_key, None)
            return
        if registration is None:
            logger.info(
                "No valid tenant settings remained for LiveKit project demand",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            try:
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            finally:
                self._starting_projects.pop(project_key, None)
            return

        try:
            has_live_demand = await store.has_live_project_demand(project_key)
        except BaseException:
            self._starting_projects.pop(project_key, None)
            raise
        if not has_live_demand:
            logger.info(
                "LiveKit project demand ended before worker startup",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            try:
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            finally:
                self._starting_projects.pop(project_key, None)
            return

        renewal_started_at = loop.time()
        try:
            async with self._claim_lock:
                lease_renewed = await store.renew_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    lease_ttl_seconds=self.settings.voice_worker_lease_ttl_seconds,
                )
        except BaseException:
            self._starting_projects.pop(project_key, None)
            raise
        renewed_deadline = renewal_started_at + self.settings.voice_worker_lease_ttl_seconds
        if not lease_renewed or loop.time() >= renewed_deadline:
            logger.warning(
                "Lost LiveKit project lease before worker startup",
                extra={
                    "project_key": project_key,
                    "supervisor_id": self.supervisor_id,
                },
            )
            self._starting_projects.pop(project_key, None)
            return

        try:
            launcher = self._required_launcher()
            registration_budget = (
                claim_started_at
                + self.settings.voice_worker_start_timeout_seconds
                - self.settings.voice_worker_poll_interval_seconds
                - loop.time()
            )
            if registration_budget <= 0:
                await store.mark_project_failed(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    failure_code="worker_start_timeout",
                    failure_type="TimeoutError",
                )
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
                self._starting_projects.pop(project_key, None)
                return
            registration = replace(
                registration,
                registration_timeout_seconds=registration_budget,
            )
            handle = launcher.start(registration)
        except Exception as exc:
            logger.exception(
                "Failed to start isolated LiveKit project worker",
                extra={
                    "project_key": project_key,
                    "livekit_project": livekit_log_target(registration.livekit_url),
                    "failure_type": type(exc).__name__,
                },
            )
            try:
                await store.mark_project_failed(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    failure_code="worker_process_start_failed",
                    failure_type=type(exc).__name__,
                )
                await store.release_project_lease(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            finally:
                self._starting_projects.pop(project_key, None)
            return

        self._workers[project_key] = RunningProjectWorker(
            handle=handle,
            lease_valid_until=renewed_deadline,
        )
        self._starting_projects.pop(project_key, None)
        logger.info(
            "Assigned LiveKit project worker",
            extra={
                "project_key": project_key,
                "livekit_project": livekit_log_target(registration.livekit_url),
                "demand_count": len(demands),
                "supervisor_id": self.supervisor_id,
            },
        )

    async def _resolve_project_registration(
        self,
        *,
        project_key: str,
        demands: tuple[VoiceProjectDemand, ...],
        store: VoiceWorkerDemandStore,
        resolution_deadline: float,
    ) -> LiveKitProjectRegistration | None:
        key_factory = self._project_key_factory()
        demands_by_user: dict[str, list[VoiceProjectDemand]] = {}
        for demand in demands:
            demands_by_user.setdefault(demand.user_id, []).append(demand)
        tenant_demands = tuple(demands_by_user.items())
        loop = asyncio.get_running_loop()
        for index, (user_id, user_demands) in enumerate(tenant_demands):
            remaining_tenant_count = len(tenant_demands) - index
            remaining_time = max(resolution_deadline - loop.time(), 0.0)
            tenant_timeout = remaining_time / remaining_tenant_count
            representative = user_demands[0]
            if tenant_timeout <= 0:
                await self._fail_tenant_demands(
                    demands=user_demands,
                    project_key=project_key,
                    store=store,
                    failure_code="livekit_settings_resolution_timeout",
                    failure_type="TimeoutError",
                )
                continue
            try:
                credentials = await asyncio.wait_for(
                    self._resolve_and_validate_tenant_project_credentials(user_id),
                    timeout=tenant_timeout,
                )
            except TimeoutError:
                logger.warning(
                    "Timed out resolving one tenant's LiveKit project settings",
                    extra={
                        "user_id": user_id,
                        "runtime_session_id": demand_runtime_session_id(representative),
                        "project_key": project_key,
                        "failure_type": "TimeoutError",
                    },
                )
                await self._fail_tenant_demands(
                    demands=user_demands,
                    project_key=project_key,
                    store=store,
                    failure_code="livekit_settings_resolution_timeout",
                    failure_type="TimeoutError",
                )
                continue
            except DisallowedLiveKitEndpointError as exc:
                logger.warning(
                    "Rejected LiveKit call demand with disallowed endpoint",
                    extra={
                        "user_id": user_id,
                        "runtime_session_id": demand_runtime_session_id(representative),
                        "project_key": project_key,
                        "failure_type": type(exc).__name__,
                    },
                )
                await self._fail_tenant_demands(
                    demands=user_demands,
                    project_key=project_key,
                    store=store,
                    failure_code="livekit_endpoint_not_allowed",
                    failure_type=type(exc).__name__,
                )
                continue
            except (
                MissingLiveKitCredentialsError,
                InvalidLiveKitCredentialsError,
            ) as exc:
                logger.warning(
                    "Rejected LiveKit call demand with invalid tenant settings",
                    extra={
                        "user_id": user_id,
                        "runtime_session_id": demand_runtime_session_id(representative),
                        "project_key": project_key,
                        "failure_type": type(exc).__name__,
                    },
                )
                await self._fail_tenant_demands(
                    demands=user_demands,
                    project_key=project_key,
                    store=store,
                    failure_code="invalid_livekit_settings",
                    failure_type=type(exc).__name__,
                )
                continue

            if key_factory.create(credentials) != project_key:
                await self._fail_tenant_demands(
                    demands=user_demands,
                    project_key=project_key,
                    store=store,
                    failure_code="livekit_settings_changed",
                    failure_type=None,
                )
                continue
            return LiveKitProjectRegistration(
                project_key=project_key,
                livekit_url=credentials.livekit_url,
                api_key=credentials.api_key,
                api_secret=credentials.api_secret,
                registration_timeout_seconds=(self.settings.voice_worker_start_timeout_seconds),
            )
        return None

    async def _resolve_tenant_project_credentials(
        self,
        user_id: str,
    ) -> ResolvedLiveKitCredentials:
        scoped_factory = tenant_scoped_session_factory(
            session_factory=self.session_factory,
            user_id=user_id,
        )
        async with scoped_factory() as session:
            return await UserLiveKitSettingsService(
                session=session,
                secret_vault=self.secret_vault,
            ).resolve_settings(user_id)

    async def _resolve_and_validate_tenant_project_credentials(
        self,
        user_id: str,
    ) -> ResolvedLiveKitCredentials:
        credentials = await self._resolve_tenant_project_credentials(user_id)
        await validate_livekit_worker_endpoint(
            credentials.livekit_url,
            policy=self.settings.voice_livekit_endpoint_policy,
        )
        return credentials

    async def _fail_tenant_demands(
        self,
        *,
        demands: list[VoiceProjectDemand],
        project_key: str,
        store: VoiceWorkerDemandStore,
        failure_code: str,
        failure_type: str | None,
    ) -> None:
        for demand in demands:
            if isinstance(demand, VoiceWorkerWarmRequest):
                published = await store.remove_warm_request(
                    user_id=demand.user_id,
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            else:
                published = await store.mark_demand_failed(
                    demand_id=demand.id,
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                    failure_code=failure_code,
                    failure_type=failure_type,
                )
            if not published:
                raise ProjectLeaseLostError

    def _collect_worker_events(self) -> None:
        for project_key, running in tuple(self._workers.items()):
            for event in running.handle.poll_events():
                if event.kind == ProjectWorkerEventKind.READY:
                    if event.worker_id is None:
                        running.failure_code = "registration_missing_worker_id"
                        break
                    if running.worker_id is not None and running.worker_id != event.worker_id:
                        running.ready_published = False
                        running.unready_pending = True
                    running.ready = True
                    running.worker_id = event.worker_id
                    logger.info(
                        "LiveKit project worker registered",
                        extra={
                            "project_key": project_key,
                            "worker_id": event.worker_id,
                            "livekit_project": livekit_log_target(
                                running.handle.livekit_url,
                            ),
                        },
                    )
                elif event.kind == ProjectWorkerEventKind.NOT_READY:
                    running.ready = False
                    running.ready_published = False
                    running.unready_pending = True
                    running.worker_id = None
                    logger.warning(
                        "LiveKit project worker registration unavailable",
                        extra={"project_key": project_key},
                    )
                elif event.kind == ProjectWorkerEventKind.ACTIVE_JOBS:
                    running.active_jobs = max(event.active_jobs, 0)
                elif event.kind == ProjectWorkerEventKind.FAILED:
                    running.failure_code = event.failure_code or "registration_failed"
                    running.failure_type = event.failure_type
                    break
                elif event.kind == ProjectWorkerEventKind.STOPPED:
                    running.failure_code = "worker_stopped_unexpectedly"
                    break

    async def _publish_ready_workers(self, store: VoiceWorkerDemandStore) -> None:
        for project_key, running in tuple(self._workers.items()):
            if (
                not running.ready
                or running.ready_published
                or running.worker_id is None
                or running.failure_code is not None
            ):
                continue
            loop = asyncio.get_running_loop()
            publication_started_at = loop.time()
            lease_ready = await store.mark_project_lease_ready(
                project_key=project_key,
                owner_id=self.supervisor_id,
                worker_id=running.worker_id,
                lease_ttl_seconds=self.settings.voice_worker_lease_ttl_seconds,
            )
            published_deadline = publication_started_at + self.settings.voice_worker_lease_ttl_seconds
            if not lease_ready or loop.time() >= published_deadline:
                await self._stop_project(
                    project_key,
                    store=store,
                    release_lease=False,
                )
                continue
            running.lease_valid_until = published_deadline
            published = await store.mark_project_ready(
                project_key=project_key,
                owner_id=self.supervisor_id,
                worker_id=running.worker_id,
                max_active_calls=self.settings.voice_worker_max_calls_per_project,
                observed_active_jobs=running.active_jobs,
            )
            if not published:
                await self._stop_project(
                    project_key,
                    store=store,
                    release_lease=False,
                )
                continue
            running.ready_published = True

    async def _reconcile_unready_workers(self, store: VoiceWorkerDemandStore) -> None:
        for project_key, running in tuple(self._workers.items()):
            if not running.unready_pending:
                continue
            published = await store.mark_project_unready(
                project_key=project_key,
                owner_id=self.supervisor_id,
            )
            if not published:
                running.lease_lost = True
                continue
            running.unready_pending = False

    async def _reconcile_failed_workers(self, store: VoiceWorkerDemandStore) -> None:
        for project_key, running in tuple(self._workers.items()):
            if running.failure_code is None:
                continue
            await self._fail_project_worker(
                project_key=project_key,
                failure_code=running.failure_code,
                failure_type=running.failure_type,
                store=store,
            )

    async def _fail_project_worker(
        self,
        *,
        project_key: str,
        failure_code: str,
        failure_type: str | None,
        store: VoiceWorkerDemandStore,
    ) -> None:
        running = self._workers.get(project_key)
        if running is None:
            return
        logger.warning(
            "LiveKit project worker failed",
            extra={
                "project_key": project_key,
                "livekit_project": livekit_log_target(running.handle.livekit_url),
                "failure_code": failure_code,
                "failure_type": failure_type,
            },
        )
        published = await store.mark_project_failed(
            project_key=project_key,
            owner_id=self.supervisor_id,
            failure_code=failure_code,
            failure_type=failure_type,
        )
        if not published:
            await self._stop_project(
                project_key,
                store=store,
                release_lease=False,
            )
            return
        await self._stop_project(project_key, store=store, release_lease=True)

    async def _stop_project(
        self,
        project_key: str,
        *,
        store: VoiceWorkerDemandStore,
        release_lease: bool,
    ) -> None:
        running = self._workers.get(project_key)
        if running is None or running.stopping:
            return
        running.stopping = True
        running.ready = False
        running.ready_published = False
        running.lease_lost = True
        try:
            try:
                await store.mark_project_unready(
                    project_key=project_key,
                    owner_id=self.supervisor_id,
                )
            except Exception:
                logger.exception(
                    "Failed to withdraw LiveKit project worker readiness",
                    extra={
                        "project_key": project_key,
                        "supervisor_id": self.supervisor_id,
                    },
                )
            await running.handle.stop(
                self.settings.voice_worker_shutdown_timeout_seconds,
            )
        finally:
            self._workers.pop(project_key, None)
            try:
                if release_lease:
                    await store.release_project_lease(
                        project_key=project_key,
                        owner_id=self.supervisor_id,
                    )
            finally:
                running.stopping = False
        logger.info(
            "Stopped LiveKit project worker",
            extra={
                "project_key": project_key,
                "livekit_project": livekit_log_target(running.handle.livekit_url),
                "worker_id": running.worker_id,
                "released_lease": release_lease,
            },
        )

    def _store(self) -> VoiceWorkerDemandStore:
        return VoiceWorkerDemandStore(
            session_factory=voice_worker_session_factory(self.session_factory),
            warm_requests_enabled=self.settings.voice_warm_user_workers,
        )

    def _project_key_factory(self) -> VoiceProjectKeyFactory:
        if self.settings.voice_route_signing_secret is None:
            raise MissingVoiceRouteSignerError
        return VoiceProjectKeyFactory(self.settings.voice_route_signing_secret)

    def _required_launcher(self) -> ProjectWorkerLauncher:
        if self.launcher is None:
            message = "LiveKit project worker launcher is not configured"
            raise RuntimeError(message)
        return self.launcher


def run_project_worker_process(
    connection: Connection,
    options: ProjectWorkerLaunchOptions,
    entrypoint: VoiceEntrypoint,
    prewarm_fnc: VoiceJobPrewarm | None = None,
) -> None:
    initialize_sentry("voice-registration")
    clear_livekit_credential_environment()
    try:
        message = connection.recv()
        if not isinstance(message, ProjectWorkerCredentialsMessage):
            error_message = "invalid project worker credential message"
            raise TypeError(error_message)
        if not message.livekit_url or not message.api_key or not message.api_secret:
            error_message = "incomplete project worker credentials"
            raise ValueError(error_message)
        asyncio.run(
            run_project_worker_server(
                connection=connection,
                credentials=message,
                options=options,
                entrypoint=entrypoint,
                prewarm_fnc=prewarm_fnc,
            ),
        )
    except BaseException as exc:
        sentry_sdk.capture_exception(exc)
        sentry_sdk.flush(timeout=2)
        safe_send_project_event(
            connection,
            ProjectWorkerEvent(
                kind=ProjectWorkerEventKind.FAILED,
                failure_code="project_worker_failed",
                failure_type=type(exc).__name__,
            ),
        )
    finally:
        connection.close()
        clear_livekit_credential_environment()


async def run_project_worker_server(
    *,
    connection: Connection,
    credentials: ProjectWorkerCredentialsMessage,
    options: ProjectWorkerLaunchOptions,
    entrypoint: VoiceEntrypoint,
    prewarm_fnc: VoiceJobPrewarm | None = None,
) -> None:
    database_engine = create_database_engine(DatabaseSettings())
    admission = ProjectJobAdmissionController(
        options=options,
        session_factory=create_session_factory(database_engine),
    )
    worker_options = project_worker_options(
        credentials=credentials,
        options=options,
        entrypoint=entrypoint,
        request_fnc=admission,
        prewarm_fnc=prewarm_fnc,
    )
    server = agents.AgentServer.from_server_options(worker_options)
    ready = False
    ever_registered = False
    reconnect_reported = False

    def on_registered(worker_id: str, _server_info: object) -> None:
        nonlocal ever_registered, ready, reconnect_reported
        admission.registered(worker_id)
        ready = True
        ever_registered = True
        reconnect_reported = False
        clear_livekit_credential_environment()
        safe_send_project_event(
            connection,
            ProjectWorkerEvent(
                kind=ProjectWorkerEventKind.READY,
                worker_id=worker_id,
            ),
        )

    server.on("worker_registered", on_registered)
    run_task = asyncio.create_task(server.run(), name="livekit-project-agent-server")
    loop = asyncio.get_running_loop()
    registration_deadline = loop.time() + options.start_timeout_seconds
    last_active_jobs = -1
    stop_requested = False
    failure_sent = False
    try:
        while True:
            if connection.poll():
                command = connection.recv()
                if command == PROJECT_WORKER_STOP:
                    stop_requested = True
                    break
            if run_task.done():
                try:
                    run_task.result()
                except BaseException as exc:
                    safe_send_project_event(
                        connection,
                        ProjectWorkerEvent(
                            kind=ProjectWorkerEventKind.FAILED,
                            failure_code="agent_server_stopped",
                            failure_type=type(exc).__name__,
                        ),
                    )
                    failure_sent = True
                break
            if not ever_registered and loop.time() >= registration_deadline:
                safe_send_project_event(
                    connection,
                    ProjectWorkerEvent(
                        kind=ProjectWorkerEventKind.FAILED,
                        failure_code="registration_timeout",
                        failure_type="TimeoutError",
                    ),
                )
                failure_sent = True
                break
            if ready and bool(getattr(server, "_connecting", False)) and not reconnect_reported:
                ready = False
                reconnect_reported = True
                safe_send_project_event(
                    connection,
                    ProjectWorkerEvent(kind=ProjectWorkerEventKind.NOT_READY),
                )
            if bool(getattr(server, "_connection_failed", False)):
                safe_send_project_event(
                    connection,
                    ProjectWorkerEvent(
                        kind=ProjectWorkerEventKind.FAILED,
                        failure_code="registration_connection_failed",
                        failure_type="ConnectionError",
                    ),
                )
                failure_sent = True
                break
            if ready:
                active_jobs = len(server.active_jobs)
                if active_jobs != last_active_jobs:
                    safe_send_project_event(
                        connection,
                        ProjectWorkerEvent(
                            kind=ProjectWorkerEventKind.ACTIVE_JOBS,
                            active_jobs=active_jobs,
                        ),
                    )
                    last_active_jobs = active_jobs
            await asyncio.sleep(0.1)
    finally:
        internal_shutdown_timeout = options.shutdown_timeout_seconds * 0.8
        shutdown_deadline = loop.time() + internal_shutdown_timeout
        if stop_requested and ever_registered:
            drain_budget = internal_shutdown_timeout * 0.6
            try:
                await asyncio.wait_for(
                    server.drain(timeout=max(1, math.ceil(drain_budget))),
                    timeout=drain_budget,
                )
            except TimeoutError:
                logger.warning("Timed out draining LiveKit project worker")
        close_budget = max(shutdown_deadline - loop.time(), 0.0)
        if close_budget > 0:
            try:
                await asyncio.wait_for(server.aclose(), timeout=close_budget)
            except TimeoutError:
                logger.warning("Timed out closing LiveKit project worker")
        if not run_task.done():
            run_task.cancel()
        join_budget = max(shutdown_deadline - loop.time(), 0.0)
        if join_budget > 0:
            done, _pending = await asyncio.wait({run_task}, timeout=join_budget)
            if done:
                await asyncio.gather(run_task, return_exceptions=True)
        if not run_task.done():
            logger.warning("Timed out joining LiveKit project worker server task")
        if stop_requested:
            safe_send_project_event(
                connection,
                ProjectWorkerEvent(kind=ProjectWorkerEventKind.STOPPED),
            )
        elif not failure_sent:
            safe_send_project_event(
                connection,
                ProjectWorkerEvent(
                    kind=ProjectWorkerEventKind.FAILED,
                    failure_code="agent_server_closed",
                    failure_type=None,
                ),
            )
        clear_livekit_credential_environment()
        await database_engine.dispose()


def project_worker_options(
    *,
    credentials: ProjectWorkerCredentialsMessage,
    options: ProjectWorkerLaunchOptions,
    entrypoint: VoiceEntrypoint,
    request_fnc: Callable[[agents.JobRequest], Awaitable[None]] | None = None,
    prewarm_fnc: VoiceJobPrewarm | None = None,
) -> agents.WorkerOptions:
    async def reject_unconfigured_request(request: agents.JobRequest) -> None:
        await request.reject(terminate=True)

    return agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        request_fnc=request_fnc or reject_unconfigured_request,
        agent_name=options.agent_name,
        ws_url=credentials.livekit_url,
        api_key=credentials.api_key,
        api_secret=credentials.api_secret,
        prewarm_fnc=ProjectJobEnvironment(
            livekit_url=credentials.livekit_url,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            prewarm_fnc=prewarm_fnc,
        ),
        host="127.0.0.1",
        port=0,
        num_idle_processes=options.idle_processes,
        load_threshold=math.inf,
        max_retry=4,
        shutdown_process_timeout=max(options.shutdown_timeout_seconds * 0.15, 0.01),
        job_executor_type=agents.JobExecutorType.PROCESS,
    )


def safe_send_project_event(connection: Connection, event: ProjectWorkerEvent) -> None:
    try:
        connection.send(event)
    except (BrokenPipeError, EOFError, OSError):
        return


def clear_livekit_credential_environment() -> None:
    for variable_name in LIVEKIT_CREDENTIAL_ENV_NAMES:
        os.environ.pop(variable_name, None)


def group_demands_by_project(
    demands: tuple[VoiceProjectDemand, ...],
) -> dict[str, tuple[VoiceProjectDemand, ...]]:
    grouped: dict[str, list[VoiceProjectDemand]] = {}
    for demand in demands:
        grouped.setdefault(demand.project_key, []).append(demand)
    return {key: tuple(project_demands) for key, project_demands in grouped.items()}


def demand_runtime_session_id(demand: VoiceProjectDemand) -> str | None:
    if isinstance(demand, VoiceCallDemand):
        return demand.runtime_session_id
    return None


def validate_voice_supervisor_settings(settings: VoiceAgentSettings) -> None:
    if settings.voice_route_signing_secret is None:
        raise MissingVoiceRouteSignerError
    validate_voice_worker_lifecycle_settings(settings)


def install_supervisor_signal_handlers(supervisor: VoiceWorkerSupervisor) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, supervisor.stop)

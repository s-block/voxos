import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.whatsapp.bridge import (
    BridgeEventFrame,
    BridgeStartResult,
    WhatsAppBridgeClient,
    WhatsAppBridgeError,
)
from voxos.whatsapp.crypto import WhatsAppSessionCipher
from voxos.whatsapp.errors import (
    WhatsAppInboundRetryableError,
    WhatsAppInboundUnavailableError,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppLinkMethod,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.runtime import (
    WhatsAppInboundText,
    WhatsAppReportedIdentity,
    WhatsAppRuntimeService,
)
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.store import (
    CLAIMABLE_SESSION_STATES,
    MAX_AUTH_WRITES,
    RECOVERABLE_SESSION_FAILURE_CODES,
    WhatsAppAuthWrite,
    WhatsAppLeaseFence,
    WhatsAppSessionDataStore,
    WhatsAppSessionDemand,
    WhatsAppWorkerStore,
)

logger = logging.getLogger(__name__)
AUTH_CREDENTIALS_CATEGORY = "credentials"
AUTH_CREDENTIALS_LOGICAL_ID = "credentials"
MAX_BRIDGE_RESTART_ATTEMPTS = 3
_SAFE_SESSION_STATUS_CLASSIFICATIONS = frozenset(
    {
        "needs_relink",
        "restart_required",
        "terminal_local_failure",
        "transient_reconnect",
        "reconnect_exhausted",
    },
)


def _log_sanitized_failure(
    message: str,
    error: BaseException,
    *,
    event: str | None = None,
) -> None:
    failure_type = type(error).__name__
    failure_code = error.code if isinstance(error, WhatsAppBridgeError) else None
    extra = {
        "failure_type": failure_type,
        "failure_code": failure_code,
    }
    if event is not None:
        extra["event"] = event
    if failure_code is None:
        logger.error("%s [failure_type=%s]", message, failure_type, extra=extra)
        return
    logger.error(
        "%s [failure_type=%s failure_code=%s]",
        message,
        failure_type,
        failure_code,
        extra=extra,
    )


class AuthGetEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=64)
    logical_ids: tuple[str, ...] = Field(max_length=MAX_AUTH_WRITES)


class AuthSetWritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: str = Field(min_length=1, max_length=64)
    logical_id: str = Field(min_length=1, max_length=512)
    value: object | None = None


class AuthSetEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    writes: tuple[AuthSetWritePayload, ...] = Field(
        max_length=MAX_AUTH_WRITES,
    )


class LinkChallengeEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    method: WhatsAppLinkMethod
    requested_method: WhatsAppLinkMethod
    request_revision: int = Field(ge=1)
    value: str = Field(min_length=1, max_length=4096)
    expires_at: int = Field(gt=0)


class SessionStatusEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str = Field(min_length=1, max_length=32)
    registered: bool | None = None
    linked: bool | None = None
    reconnect_attempts: int | None = Field(default=None, ge=0)
    identity: WhatsAppReportedIdentity | None = None
    classification: str | None = Field(default=None, max_length=64)
    failure_code: str | None = Field(default=None, max_length=64)


@dataclass(slots=True)
class ActiveWhatsAppSession:
    demand: WhatsAppSessionDemand
    fence: WhatsAppLeaseFence
    cipher: WhatsAppSessionCipher
    data_store: WhatsAppSessionDataStore
    next_renew_at: float
    auth_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    control_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    inbound_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connected: bool = False
    bridge_session_started: bool = False
    last_challenge_request_revision: int = 0
    reset_retry_at: float = 0.0
    identity_cleanup_pending: bool = False


class WhatsAppWorkerSupervisor:
    def __init__(
        self,
        *,
        base_session_factory: AsyncSessionFactory,
        worker_session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
        settings: WhatsAppWorkerSettings,
        worker_id: str | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_session_factory = base_session_factory
        self._worker_session_factory = worker_session_factory
        self._secret_vault = secret_vault
        self._settings = settings
        self._worker_id = worker_id or f"whatsapp-worker-{uuid.uuid4()}"
        self._monotonic_clock = monotonic_clock
        self._worker_store = WhatsAppWorkerStore(worker_session_factory)
        self._runtime = WhatsAppRuntimeService(
            worker_store=self._worker_store,
            base_session_factory=base_session_factory,
            secret_vault=secret_vault,
        )
        self._bridge: WhatsAppBridgeClient | None = None
        self._active: dict[str, ActiveWhatsAppSession] = {}
        self._stop_event = asyncio.Event()
        self._lease_renewal_stop_event = asyncio.Event()
        self._draining = False
        self._last_successful_poll_at: datetime | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def active_session_count(self) -> int:
        return len(self._active)

    @property
    def bridge_connected(self) -> bool:
        return self._bridge is not None and self._bridge.running

    @property
    def last_successful_poll_at(self) -> datetime | None:
        return self._last_successful_poll_at

    def request_stop(self) -> None:
        self._draining = True
        self._stop_event.set()

    async def run(self) -> None:
        if not self._settings.whatsapp_enabled:
            return
        await self._start_bridge()
        last_successful_iteration_at = self._monotonic_clock()
        lease_renewal_task = asyncio.create_task(
            self._lease_renewal_loop(),
            name=f"{self._worker_id}-lease-renewal",
        )
        try:
            while not self._stop_event.is_set():
                try:
                    async with asyncio.timeout(
                        self._settings.whatsapp_worker_restart_after_seconds,
                    ):
                        await self._run_iteration()
                except Exception as error:
                    _log_sanitized_failure(
                        "WhatsApp worker iteration failed",
                        error,
                    )
                    if (
                        self._monotonic_clock() - last_successful_iteration_at
                        >= self._settings.whatsapp_worker_restart_after_seconds
                    ):
                        message = (
                            "WhatsApp worker polling remained unhealthy beyond "
                            "WHATSAPP_WORKER_RESTART_AFTER_SECONDS; exiting for restart"
                        )
                        raise RuntimeError(message) from None
                else:
                    last_successful_iteration_at = self._monotonic_clock()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=(self._settings.whatsapp_worker_poll_interval_seconds),
                    )
                except TimeoutError:
                    pass
        finally:
            self._lease_renewal_stop_event.set()
            await lease_renewal_task
            await self._drain()

    async def _run_iteration(self) -> None:
        if not self.bridge_connected:
            await self._recover_bridge()
        demands = await self._worker_store.list_session_demands(
            owner_id=self._worker_id,
            limit=max(
                self._settings.whatsapp_max_sessions_per_worker * 4,
                100,
            ),
        )
        await self._reconcile(demands)
        await self._dispatch_outbox()
        self._last_successful_poll_at = datetime.now(tz=UTC)

    async def _lease_renewal_loop(self) -> None:
        while not self._lease_renewal_stop_event.is_set():
            try:
                await self._renew_due_leases()
            except Exception as error:
                _log_sanitized_failure(
                    "WhatsApp lease renewal failed",
                    error,
                )
            try:
                await asyncio.wait_for(
                    self._lease_renewal_stop_event.wait(),
                    timeout=(self._settings.whatsapp_worker_poll_interval_seconds),
                )
            except TimeoutError:
                pass

    async def _start_bridge(self) -> None:
        bridge = WhatsAppBridgeClient(
            settings=self._settings,
            event_handler=self._handle_bridge_event,
        )
        await bridge.start()
        if bridge.handshake.max_sessions < self._settings.whatsapp_max_sessions_per_worker:
            await bridge.close()
            message = "configured WhatsApp session capacity exceeds bridge capacity"
            raise RuntimeError(message)
        self._bridge = bridge

    async def _recover_bridge(self) -> None:
        previous = self._bridge
        if previous is not None:
            await previous.close()
        lost = tuple(self._active.values())
        self._active.clear()
        for active in lost:
            await self._worker_store.release_session(
                fence=active.fence,
            )
        last_error: Exception | None = None
        for attempt in range(MAX_BRIDGE_RESTART_ATTEMPTS):
            if self._stop_event.is_set():
                return
            if attempt > 0:
                await asyncio.sleep(
                    self._settings.whatsapp_bridge_restart_grace_seconds,
                )
            try:
                await self._start_bridge()
                return
            except Exception as error:
                last_error = error
                _log_sanitized_failure(
                    "WhatsApp bridge restart failed",
                    error,
                )
        message = "WhatsApp bridge could not be restarted"
        raise RuntimeError(message) from last_error

    async def _renew_due_leases(self) -> None:
        now = self._monotonic_clock()
        due = tuple(active for active in self._active.values() if active.next_renew_at <= now)
        if not due:
            return
        results = await asyncio.gather(
            *(
                self._worker_store.renew_session(
                    fence=active.fence,
                    lease_ttl_seconds=(self._settings.whatsapp_worker_lease_ttl_seconds),
                )
                for active in due
            ),
            return_exceptions=True,
        )
        for active, result in zip(due, results, strict=True):
            if isinstance(result, WhatsAppLeaseFence):
                active.fence = result
                active.next_renew_at = self._monotonic_clock() + self._settings.whatsapp_worker_lease_ttl_seconds / 3
                continue
            await self._stop_after_lease_loss(active)

    async def _stop_after_lease_loss(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        bridge = self._bridge
        if active.bridge_session_started and bridge is not None and bridge.running:
            try:
                await bridge.stop_session(
                    session_handle=active.demand.integration_id,
                    generation=active.fence.generation,
                )
            except WhatsAppBridgeError:
                await bridge.close()
        active.bridge_session_started = False
        active.connected = False
        self._active.pop(active.demand.integration_id, None)

    async def _reconcile(
        self,
        demands: tuple[WhatsAppSessionDemand, ...],
    ) -> None:
        by_id = {demand.integration_id: demand for demand in demands}
        for integration_id, active in tuple(self._active.items()):
            demand = by_id.get(integration_id)
            if demand is None:
                await self._stop_and_release(active)
                continue
            active.demand = demand
            if demand.state == WhatsAppSessionState.DISCONNECTING:
                if not active.identity_cleanup_pending:
                    await self._process_reset(active)
                continue
            if not active.connected and demand.challenge_request_revision > active.last_challenge_request_revision:
                await self._request_challenge(active)

        if self._draining:
            return
        for demand in demands:
            if demand.integration_id in self._active:
                continue
            if not (
                demand.state == WhatsAppSessionState.DISCONNECTING
                or (
                    demand.desired_state == WhatsAppDesiredState.CONNECTED
                    and (
                        demand.state in CLAIMABLE_SESSION_STATES
                        or (
                            demand.state == WhatsAppSessionState.FAILED
                            and demand.failure_code in RECOVERABLE_SESSION_FAILURE_CODES
                        )
                    )
                )
            ):
                continue
            if len(self._active) >= self._settings.whatsapp_max_sessions_per_worker:
                break
            await self._claim_and_start(demand)

    async def _claim_and_start(
        self,
        demand: WhatsAppSessionDemand,
    ) -> None:
        fence = await self._worker_store.claim_session(
            demand=demand,
            owner_id=self._worker_id,
            lease_ttl_seconds=(self._settings.whatsapp_worker_lease_ttl_seconds),
        )
        if fence is None:
            return
        data_store = WhatsAppSessionDataStore(
            session_factory=self._worker_session_factory,
            vault=self._secret_vault,
            user_id=demand.user_id,
        )
        try:
            cipher = await data_store.resolve_cipher(
                credential_id=demand.credential_id,
                fence=fence,
            )
            credentials = (
                await data_store.get_auth_values(
                    integration_id=demand.integration_id,
                    cipher=cipher,
                    category=AUTH_CREDENTIALS_CATEGORY,
                    logical_ids=(AUTH_CREDENTIALS_LOGICAL_ID,),
                    fence=fence,
                )
            )[AUTH_CREDENTIALS_LOGICAL_ID]
            active = ActiveWhatsAppSession(
                demand=demand,
                fence=fence,
                cipher=cipher,
                data_store=data_store,
                next_renew_at=(self._monotonic_clock() + self._settings.whatsapp_worker_lease_ttl_seconds / 3),
            )
            self._active[demand.integration_id] = active
            result = await self._start_bridge_session(
                active=active,
                credentials=credentials,
            )
            await self._worker_store.mark_stale_submissions_ambiguous(
                fence=fence,
            )
            if demand.state == WhatsAppSessionState.DISCONNECTING:
                await self._process_reset(active)
            elif result.linked:
                active.last_challenge_request_revision = demand.challenge_request_revision
            else:
                await self._request_challenge(active)
        except Exception as error:
            _log_sanitized_failure(
                "WhatsApp session failed to start",
                error,
            )
            await self._cleanup_failed_start(
                integration_id=demand.integration_id,
                fence=fence,
            )

    async def _start_bridge_session(
        self,
        *,
        active: ActiveWhatsAppSession,
        credentials: object | None,
    ) -> BridgeStartResult:
        bridge = self._require_bridge()
        try:
            result = await bridge.start_session(
                session_handle=active.demand.integration_id,
                generation=active.fence.generation,
                credentials=credentials,
                requested_method=active.demand.link_method,
                request_revision=active.demand.challenge_request_revision,
            )
        except Exception:
            active.bridge_session_started = True
            raise
        active.bridge_session_started = True
        # A bridge snapshot cannot prove that the linked identity belongs to
        # the tenant's configured number, so only the identity-bearing status
        # event may open the delivery gate.
        return result

    async def _ensure_bridge_session(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        if active.bridge_session_started:
            return
        credentials = (
            await active.data_store.get_auth_values(
                integration_id=active.demand.integration_id,
                cipher=active.cipher,
                category=AUTH_CREDENTIALS_CATEGORY,
                logical_ids=(AUTH_CREDENTIALS_LOGICAL_ID,),
                fence=active.fence,
            )
        )[AUTH_CREDENTIALS_LOGICAL_ID]
        await self._start_bridge_session(
            active=active,
            credentials=credentials,
        )

    async def _cleanup_failed_start(
        self,
        *,
        integration_id: str,
        fence: WhatsAppLeaseFence,
    ) -> None:
        stopped = False
        bridge = self._bridge
        if bridge is not None and bridge.running:
            try:
                await bridge.stop_session(
                    session_handle=integration_id,
                    generation=fence.generation,
                )
                stopped = True
            except WhatsAppBridgeError:
                await bridge.close()
                stopped = True
        self._active.pop(integration_id, None)
        if stopped:
            await self._worker_store.release_session(fence=fence)

    async def _request_challenge(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        bridge = self._require_bridge()
        phone_number = None
        if active.demand.link_method == WhatsAppLinkMethod.PAIRING_CODE:
            phone_number = await self._runtime.source_phone_number(
                fence=active.fence,
            )
        try:
            await bridge.request_challenge(
                session_handle=active.demand.integration_id,
                generation=active.fence.generation,
                method=active.demand.link_method,
                phone_number=phone_number,
                regenerate=active.last_challenge_request_revision > 0,
                request_revision=active.demand.challenge_request_revision,
            )
        except WhatsAppBridgeError as error:
            if error.retryable:
                return
            await self._runtime.mark_state(
                fence=active.fence,
                state=WhatsAppSessionState.FAILED,
                failure_code=error.code,
            )
            return
        active.last_challenge_request_revision = active.demand.challenge_request_revision
        await self._runtime.mark_awaiting_link(fence=active.fence)

    async def _process_reset(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        async with active.control_lock:
            if self._monotonic_clock() < active.reset_retry_at:
                return
            await self._process_reset_locked(active)

    async def _process_reset_locked(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        bridge = self._require_bridge()
        try:
            await self._ensure_bridge_session(active)
            await bridge.stop_session(
                session_handle=active.demand.integration_id,
                generation=active.fence.generation,
                logout=True,
            )
        except WhatsAppBridgeError as error:
            if error.code == "logout_uncertain":
                active.bridge_session_started = False
                active.connected = False
                if active.demand.desired_state == WhatsAppDesiredState.STOPPED:
                    _log_sanitized_failure(
                        "WhatsApp session reset failed",
                        error,
                    )
                    await bridge.close()
                    active.reset_retry_at = self._monotonic_clock() + min(
                        self._settings.whatsapp_bridge_restart_grace_seconds,
                        5.0,
                    )
                    return
                # The bridge has already stopped the local session before it
                # reports logout_uncertain. A relink must clear that local
                # state and create a fresh device session; retrying the same
                # remote logout can otherwise leave it disconnecting forever.
            else:
                _log_sanitized_failure(
                    "WhatsApp session reset failed",
                    error,
                )
                if error.code in {"bridge_unavailable", "session_not_found"}:
                    await bridge.close()
                active.reset_retry_at = self._monotonic_clock() + self._settings.whatsapp_bridge_restart_grace_seconds
                return
        active.bridge_session_started = False
        await active.data_store.clear_auth_state(
            integration_id=active.demand.integration_id,
            fence=active.fence,
        )
        active.connected = False
        active.reset_retry_at = 0.0
        if active.demand.desired_state == WhatsAppDesiredState.STOPPED:
            await self._runtime.finalize_disconnect(
                fence=active.fence,
                credential_id=active.demand.credential_id,
            )
            self._active.pop(active.demand.integration_id, None)
            await self._worker_store.release_session(fence=active.fence)
            return
        if active.demand.failure_code is not None:
            completed = await self._runtime.complete_identity_rejection(
                fence=active.fence,
                failure_code=active.demand.failure_code,
            )
            if not completed:
                await self._runtime.finalize_disconnect(
                    fence=active.fence,
                    credential_id=active.demand.credential_id,
                )
            self._active.pop(active.demand.integration_id, None)
            await self._worker_store.release_session(fence=active.fence)
            return
        await self._runtime.mark_awaiting_link(
            fence=active.fence,
            reset_validation=True,
            allow_disconnecting=True,
        )
        active.fence = await self._worker_store.advance_session_generation(
            fence=active.fence,
        )
        try:
            await self._ensure_bridge_session(active)
        except Exception as error:
            _log_sanitized_failure(
                "WhatsApp session failed to restart after reset",
                error,
            )
            await self._cleanup_failed_start(
                integration_id=active.demand.integration_id,
                fence=active.fence,
            )
            return
        active.last_challenge_request_revision = 0
        await self._request_challenge(active)

    async def _dispatch_outbox(self) -> None:
        if self._draining:
            return
        active_sessions = tuple(active for active in self._active.values() if active.connected)
        if not active_sessions:
            return
        await asyncio.gather(
            *(self._dispatch_one(active) for active in active_sessions),
        )

    async def _dispatch_one(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        outbox = await self._worker_store.claim_outbox(
            fence=active.fence,
        )
        if outbox is None:
            return
        bridge = self._require_bridge()
        try:
            result = await bridge.send_text(
                session_handle=active.demand.integration_id,
                generation=active.fence.generation,
                target_jid=outbox.target_jid,
                body=outbox.body,
            )
        except WhatsAppBridgeError as error:
            if error.ambiguous:
                status = WhatsAppOutboxStatus.AMBIGUOUS
            elif error.retryable:
                status = WhatsAppOutboxStatus.QUEUED
            else:
                status = WhatsAppOutboxStatus.FAILED
            await self._complete_outbox(
                fence=active.fence,
                outbox_id=outbox.id,
                status=status,
                failure_code=error.code,
            )
            return
        await self._complete_outbox(
            fence=active.fence,
            outbox_id=outbox.id,
            status=WhatsAppOutboxStatus.SENT,
            external_message_id=result.provider_message_id,
        )

    async def _complete_outbox(
        self,
        *,
        fence: WhatsAppLeaseFence,
        outbox_id: str,
        status: WhatsAppOutboxStatus,
        external_message_id: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        try:
            await self._worker_store.complete_outbox(
                fence=fence,
                outbox_id=outbox_id,
                status=status,
                external_message_id=external_message_id,
                failure_code=failure_code,
            )
        except Exception as error:
            _log_sanitized_failure(
                "WhatsApp outbox completion failed",
                error,
            )
            if status == WhatsAppOutboxStatus.AMBIGUOUS:
                return
            try:
                await self._worker_store.complete_outbox(
                    fence=fence,
                    outbox_id=outbox_id,
                    status=WhatsAppOutboxStatus.AMBIGUOUS,
                    failure_code="outbox_completion_failed",
                )
            except Exception as error:
                _log_sanitized_failure(
                    "WhatsApp outbox ambiguity persistence failed",
                    error,
                )

    async def _handle_bridge_event(self, event: BridgeEventFrame) -> None:
        active = self._active.get(event.session_handle)
        if active is None or active.fence.generation != event.generation:
            if active is not None and event.generation < active.fence.generation:
                return
            if event.event in {"link_challenge", "session_status", "inbound_text"}:
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="session_unavailable",
                )
            return
        event_fence = active.fence
        if event.event in {"auth_get", "auth_set"}:
            async with active.auth_lock:
                if not self._event_is_current(
                    active=active,
                    event_fence=event_fence,
                ):
                    if event_fence.generation < active.fence.generation:
                        return
                    await self._require_bridge().respond_to_rpc(
                        event=event,
                        error_code="session_unavailable",
                    )
                    return
                try:
                    await self._handle_active_event(
                        active=active,
                        event=event,
                        event_fence=event_fence,
                    )
                except (ValidationError, ValueError) as error:
                    _log_sanitized_failure(
                        "WhatsApp bridge event validation failed",
                        error,
                        event=event.event,
                    )
            return
        if event.event in {"link_challenge", "session_status"}:
            event_lock = active.control_lock
        elif event.event == "inbound_text":
            event_lock = active.inbound_lock
        else:
            return
        rejection_failure_code: str | None = None
        async with event_lock:
            if not self._event_is_current(
                active=active,
                event_fence=event_fence,
            ):
                if event_fence.generation < active.fence.generation:
                    return
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="session_unavailable",
                )
                return
            try:
                rejection_failure_code = await self._handle_active_event(
                    active=active,
                    event=event,
                    event_fence=event_fence,
                )
            except WhatsAppInboundUnavailableError:
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="session_unavailable",
                )
                return
            except WhatsAppInboundRetryableError:
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="persistence_failed",
                )
                return
            except (ValidationError, ValueError) as error:
                _log_sanitized_failure(
                    "WhatsApp durable bridge event validation failed",
                    error,
                    event=event.event,
                )
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="invalid_event",
                )
                return
            except Exception as error:
                _log_sanitized_failure(
                    "WhatsApp durable bridge event handling failed",
                    error,
                    event=event.event,
                )
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    error_code="persistence_failed",
                )
                return
            try:
                await self._require_bridge().respond_to_rpc(
                    event=event,
                    result={},
                )
            except WhatsAppBridgeError:
                if rejection_failure_code is not None:
                    active.identity_cleanup_pending = False
                raise
        if rejection_failure_code is not None:
            try:
                await self._reject_identity(
                    active=active,
                    fence=event_fence,
                    failure_code=rejection_failure_code,
                )
            except Exception as error:
                active.identity_cleanup_pending = False
                _log_sanitized_failure(
                    "WhatsApp rejected identity cleanup failed",
                    error,
                    event=event.event,
                )

    async def _handle_active_event(
        self,
        *,
        active: ActiveWhatsAppSession,
        event: BridgeEventFrame,
        event_fence: WhatsAppLeaseFence,
    ) -> str | None:
        if event.event == "auth_get":
            await self._handle_auth_get(
                active=active,
                event=event,
                event_fence=event_fence,
            )
            return None
        if event.event == "auth_set":
            await self._handle_auth_set(
                active=active,
                event=event,
                event_fence=event_fence,
            )
            return None
        if event.event == "link_challenge":
            payload = LinkChallengeEventPayload.model_validate(event.payload)
            expires_at = datetime.fromtimestamp(
                payload.expires_at / 1000,
                tz=UTC,
            )
            now = datetime.now(tz=UTC)
            if expires_at <= now or (expires_at - now).total_seconds() > self._settings.whatsapp_challenge_ttl_seconds:
                message = "WhatsApp link challenge expiry is invalid"
                raise ValueError(message)
            await active.data_store.save_challenge(
                integration_id=active.demand.integration_id,
                cipher=active.cipher,
                method=payload.method,
                requested_method=payload.requested_method,
                request_revision=payload.request_revision,
                value=payload.value,
                expires_at=expires_at,
                fence=event_fence,
            )
            return None
        if event.event == "session_status":
            return await self._handle_session_status(
                active=active,
                fence=event_fence,
                payload=SessionStatusEventPayload.model_validate(
                    event.payload,
                ),
            )
        if event.event == "inbound_text":
            await self._runtime.accept_inbound_text(
                fence=event_fence,
                inbound=WhatsAppInboundText.model_validate(event.payload),
            )
        return None

    async def _handle_auth_get(
        self,
        *,
        active: ActiveWhatsAppSession,
        event: BridgeEventFrame,
        event_fence: WhatsAppLeaseFence,
    ) -> None:
        bridge = self._require_bridge()
        try:
            payload = AuthGetEventPayload.model_validate(event.payload)
        except (ValidationError, ValueError):
            await bridge.respond_to_rpc(
                event=event,
                error_code="invalid_event",
            )
            return
        try:
            values = await active.data_store.get_auth_values(
                integration_id=active.demand.integration_id,
                cipher=active.cipher,
                category=payload.category,
                logical_ids=payload.logical_ids,
                fence=event_fence,
            )
        except Exception:
            await bridge.respond_to_rpc(
                event=event,
                error_code="persistence_failed",
            )
            return
        await bridge.respond_to_rpc(
            event=event,
            result={"values": values},
        )

    async def _handle_auth_set(
        self,
        *,
        active: ActiveWhatsAppSession,
        event: BridgeEventFrame,
        event_fence: WhatsAppLeaseFence,
    ) -> None:
        bridge = self._require_bridge()
        try:
            payload = AuthSetEventPayload.model_validate(event.payload)
        except (ValidationError, ValueError):
            await bridge.respond_to_rpc(
                event=event,
                error_code="invalid_event",
            )
            return
        try:
            await active.data_store.set_auth_values(
                integration_id=active.demand.integration_id,
                cipher=active.cipher,
                writes=tuple(
                    WhatsAppAuthWrite(
                        category=write.category,
                        logical_id=write.logical_id,
                        value=write.value,
                    )
                    for write in payload.writes
                ),
                fence=event_fence,
            )
        except Exception:
            await bridge.respond_to_rpc(
                event=event,
                error_code="persistence_failed",
            )
            return
        await bridge.respond_to_rpc(event=event, result={})

    async def _handle_session_status(
        self,
        *,
        active: ActiveWhatsAppSession,
        fence: WhatsAppLeaseFence,
        payload: SessionStatusEventPayload,
    ) -> str | None:
        if payload.state == "connected":
            if payload.identity is None:
                active.identity_cleanup_pending = True
                try:
                    await self._runtime.mark_identity_rejection_pending(
                        fence=fence,
                        failure_code="pn_identity_unavailable",
                    )
                except Exception:
                    active.identity_cleanup_pending = False
                    raise
                return "pn_identity_unavailable"
            validation = await self._runtime.validate_and_mark_connected(
                fence=fence,
                cipher=active.cipher,
                identity=payload.identity,
            )
            if validation.valid:
                active.connected = True
                return None
            if validation.failure_code == "disconnect_requested":
                return None
            failure_code = validation.failure_code or "pn_identity_unavailable"
            active.identity_cleanup_pending = True
            try:
                await self._runtime.mark_identity_rejection_pending(
                    fence=fence,
                    failure_code=failure_code,
                )
            except Exception:
                active.identity_cleanup_pending = False
                raise
            return failure_code
        active.connected = False
        if payload.state == "stopped":
            active.bridge_session_started = False
            return None
        try:
            state = WhatsAppSessionState(payload.state)
        except ValueError:
            return None
        bridge_failure_code = payload.failure_code
        if bridge_failure_code is None and payload.classification in _SAFE_SESSION_STATUS_CLASSIFICATIONS:
            bridge_failure_code = payload.classification
        await self._runtime.mark_state(
            fence=fence,
            state=state,
            failure_code=bridge_failure_code,
        )
        return None

    async def _reject_identity(
        self,
        *,
        active: ActiveWhatsAppSession,
        fence: WhatsAppLeaseFence,
        failure_code: str,
    ) -> None:
        bridge = self._require_bridge()
        try:
            await bridge.stop_session(
                session_handle=active.demand.integration_id,
                generation=fence.generation,
                logout=True,
            )
        except WhatsAppBridgeError:
            await bridge.close()
            active.bridge_session_started = False
            active.connected = False
            active.identity_cleanup_pending = False
            return
        active.bridge_session_started = False
        await active.data_store.clear_auth_state(
            integration_id=active.demand.integration_id,
            fence=fence,
        )
        completed = await self._runtime.complete_identity_rejection(
            fence=fence,
            failure_code=failure_code,
        )
        if not completed:
            await self._runtime.finalize_disconnect(
                fence=fence,
                credential_id=active.demand.credential_id,
            )
        active.connected = False
        active.identity_cleanup_pending = False
        if self._event_is_current(active=active, event_fence=fence):
            self._active.pop(active.demand.integration_id, None)
        await self._worker_store.release_session(fence=fence)

    async def _stop_and_release(
        self,
        active: ActiveWhatsAppSession,
    ) -> None:
        bridge = self._bridge
        stopped = not active.bridge_session_started
        if active.bridge_session_started and bridge is not None and bridge.running:
            try:
                await bridge.stop_session(
                    session_handle=active.demand.integration_id,
                    generation=active.fence.generation,
                )
                stopped = True
            except WhatsAppBridgeError:
                pass
        if stopped:
            active.bridge_session_started = False
            active.connected = False
            self._active.pop(active.demand.integration_id, None)
            await self._worker_store.release_session(fence=active.fence)

    async def _drain(self) -> None:
        self._draining = True
        bridge = self._bridge
        self._bridge = None
        if bridge is not None:
            await bridge.close()
        active_sessions = tuple(self._active.values())
        self._active.clear()
        for active in active_sessions:
            await self._worker_store.release_session(fence=active.fence)

    def _event_is_current(
        self,
        *,
        active: ActiveWhatsAppSession,
        event_fence: WhatsAppLeaseFence,
    ) -> bool:
        current = self._active.get(event_fence.integration_id)
        return current is active and active.fence.generation == event_fence.generation

    def _require_bridge(self) -> WhatsAppBridgeClient:
        bridge = self._bridge
        if bridge is None or not bridge.running:
            code = "bridge_unavailable"
            raise WhatsAppBridgeError(
                code,
                retryable=True,
            )
        return bridge

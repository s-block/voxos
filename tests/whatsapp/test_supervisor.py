import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pytest import MonkeyPatch

import voxos.whatsapp.supervisor as supervisor_module
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
    WhatsAppSessionState,
)
from voxos.whatsapp.runtime import (
    WhatsAppIdentityValidation,
    WhatsAppInboundText,
    WhatsAppReportedIdentity,
    WhatsAppRuntimeService,
)
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.store import (
    WhatsAppLeaseFence,
    WhatsAppSessionDataStore,
    WhatsAppSessionDemand,
    WhatsAppWorkerStore,
)
from voxos.whatsapp.supervisor import (
    AUTH_CREDENTIALS_CATEGORY,
    AUTH_CREDENTIALS_LOGICAL_ID,
    ActiveWhatsAppSession,
    WhatsAppWorkerSupervisor,
)

USER_ID = "user-123"
INTEGRATION_ID = "integration-123"
CREDENTIAL_ID = "credential-123"
RESOURCE_ID = "resource-123"
SOURCE_RESOURCE_ID = "source-resource-123"


def test_supervisor_failure_log_includes_type_without_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redacted_detail = "sensitive supervisor detail"
    caplog.set_level(logging.ERROR, logger="voxos.whatsapp.supervisor")

    supervisor_module._log_sanitized_failure(
        "WhatsApp worker iteration failed",
        RuntimeError(redacted_detail),
        event="session_status",
    )

    assert "WhatsApp worker iteration failed [failure_type=RuntimeError]" in caplog.text
    assert redacted_detail not in caplog.text


@dataclass(frozen=True, slots=True)
class StartCall:
    session_handle: str
    generation: int
    credentials: object | None
    requested_method: WhatsAppLinkMethod
    request_revision: int


@dataclass(frozen=True, slots=True)
class StopCall:
    session_handle: str
    generation: int
    logout: bool


@dataclass(frozen=True, slots=True)
class ChallengeCall:
    session_handle: str
    generation: int
    method: WhatsAppLinkMethod
    phone_number: str | None
    regenerate: bool
    request_revision: int


@dataclass(frozen=True, slots=True)
class RpcResponse:
    request_id: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class SavedChallenge:
    integration_id: str
    method: WhatsAppLinkMethod
    requested_method: WhatsAppLinkMethod
    request_revision: int
    value: str
    expires_at: datetime


@dataclass(slots=True)
class FakeBridge:
    running: bool = True
    start_result: BridgeStartResult = field(
        default_factory=lambda: BridgeStartResult(
            state="connecting",
            registered=False,
            linked=False,
            reconnect_attempts=0,
            resumed=False,
        ),
    )
    start_error: WhatsAppBridgeError | None = None
    stop_error: WhatsAppBridgeError | None = None
    start_calls: list[StartCall] = field(default_factory=list)
    stop_calls: list[StopCall] = field(default_factory=list)
    challenge_calls: list[ChallengeCall] = field(default_factory=list)
    rpc_responses: list[RpcResponse] = field(default_factory=list)
    close_calls: int = 0
    event_log: list[str] | None = None

    async def start_session(
        self,
        *,
        session_handle: str,
        generation: int,
        credentials: object | None,
        requested_method: WhatsAppLinkMethod,
        request_revision: int,
    ) -> BridgeStartResult:
        self.start_calls.append(
            StartCall(
                session_handle=session_handle,
                generation=generation,
                credentials=credentials,
                requested_method=requested_method,
                request_revision=request_revision,
            ),
        )
        if self.start_error is not None:
            raise self.start_error
        return self.start_result

    async def stop_session(
        self,
        *,
        session_handle: str,
        generation: int,
        logout: bool = False,
    ) -> None:
        if self.event_log is not None:
            self.event_log.append("stop")
        self.stop_calls.append(
            StopCall(
                session_handle=session_handle,
                generation=generation,
                logout=logout,
            ),
        )
        if self.stop_error is not None:
            raise self.stop_error

    async def close(self) -> None:
        self.close_calls += 1
        self.running = False

    async def respond_to_rpc(
        self,
        *,
        event: BridgeEventFrame,
        result: object | None = None,
        error_code: str | None = None,
    ) -> None:
        del result
        if self.event_log is not None:
            self.event_log.append("ack")
        self.rpc_responses.append(
            RpcResponse(
                request_id=event.request_id,
                error_code=error_code,
            ),
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
        self.challenge_calls.append(
            ChallengeCall(
                session_handle=session_handle,
                generation=generation,
                method=method,
                phone_number=phone_number,
                regenerate=regenerate,
                request_revision=request_revision,
            ),
        )
        return {}


@dataclass(slots=True)
class FakeDataStore:
    cipher: WhatsAppSessionCipher
    credentials: object | None = None
    clear_calls: list[str] = field(default_factory=list)
    saved_challenges: list[SavedChallenge] = field(default_factory=list)

    async def resolve_cipher(
        self,
        *,
        credential_id: str,
        fence: WhatsAppLeaseFence | None = None,
    ) -> WhatsAppSessionCipher:
        assert credential_id == CREDENTIAL_ID
        assert fence is not None
        return self.cipher

    async def get_auth_values(
        self,
        *,
        integration_id: str,
        cipher: WhatsAppSessionCipher,
        category: str,
        logical_ids: tuple[str, ...],
        fence: WhatsAppLeaseFence | None = None,
    ) -> dict[str, object | None]:
        assert integration_id == INTEGRATION_ID
        assert cipher is self.cipher
        assert category == AUTH_CREDENTIALS_CATEGORY
        assert logical_ids == (AUTH_CREDENTIALS_LOGICAL_ID,)
        assert fence is not None
        return {AUTH_CREDENTIALS_LOGICAL_ID: self.credentials}

    async def clear_auth_state(
        self,
        *,
        integration_id: str,
        fence: WhatsAppLeaseFence | None = None,
    ) -> None:
        assert fence is not None
        self.clear_calls.append(integration_id)
        self.credentials = None

    async def save_challenge(
        self,
        *,
        integration_id: str,
        cipher: WhatsAppSessionCipher,
        method: WhatsAppLinkMethod,
        requested_method: WhatsAppLinkMethod,
        request_revision: int,
        value: str,
        expires_at: datetime,
        fence: WhatsAppLeaseFence | None = None,
    ) -> None:
        assert cipher is self.cipher
        assert fence is not None
        self.saved_challenges.append(
            SavedChallenge(
                integration_id=integration_id,
                method=method,
                requested_method=requested_method,
                request_revision=request_revision,
                value=value,
                expires_at=expires_at,
            ),
        )


@dataclass(slots=True)
class FakeWorkerStore:
    claim_result: WhatsAppLeaseFence | None = None
    claim_calls: list[WhatsAppSessionDemand] = field(default_factory=list)
    stale_submission_calls: list[WhatsAppLeaseFence] = field(
        default_factory=list,
    )
    release_calls: list[WhatsAppLeaseFence] = field(default_factory=list)
    advance_calls: list[WhatsAppLeaseFence] = field(default_factory=list)
    renew_calls: list[WhatsAppLeaseFence] = field(default_factory=list)
    renew_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def claim_session(
        self,
        *,
        demand: WhatsAppSessionDemand,
        owner_id: str,
        lease_ttl_seconds: float,
    ) -> WhatsAppLeaseFence | None:
        assert owner_id
        assert lease_ttl_seconds > 0
        self.claim_calls.append(demand)
        return self.claim_result

    async def mark_stale_submissions_ambiguous(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> int:
        self.stale_submission_calls.append(fence)
        return 0

    async def release_session(self, *, fence: WhatsAppLeaseFence) -> bool:
        self.release_calls.append(fence)
        return True

    async def renew_session(
        self,
        *,
        fence: WhatsAppLeaseFence,
        lease_ttl_seconds: float,
    ) -> WhatsAppLeaseFence | None:
        self.renew_calls.append(fence)
        self.renew_event.set()
        return WhatsAppLeaseFence(
            user_id=fence.user_id,
            integration_id=fence.integration_id,
            owner_id=fence.owner_id,
            generation=fence.generation,
            expires_at=datetime.now(tz=UTC) + timedelta(seconds=lease_ttl_seconds),
        )

    async def advance_session_generation(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> WhatsAppLeaseFence:
        self.advance_calls.append(fence)
        return WhatsAppLeaseFence(
            user_id=fence.user_id,
            integration_id=fence.integration_id,
            owner_id=fence.owner_id,
            generation=fence.generation + 1,
            expires_at=fence.expires_at,
        )


@dataclass(slots=True)
class FakeRuntime:
    awaiting_link_calls: list[tuple[WhatsAppLeaseFence, bool, bool]] = field(
        default_factory=list,
    )
    identity_rejection_calls: list[tuple[WhatsAppLeaseFence, str]] = field(
        default_factory=list,
    )
    finalize_calls: list[tuple[WhatsAppLeaseFence, str]] = field(
        default_factory=list,
    )
    inbound_calls: list[WhatsAppInboundText] = field(default_factory=list)
    inbound_error: Exception | None = None
    identity_validation: WhatsAppIdentityValidation = field(
        default_factory=lambda: WhatsAppIdentityValidation(valid=True),
    )
    identity_rejection_pending_calls: list[tuple[WhatsAppLeaseFence, str]] = field(default_factory=list)
    state_calls: list[tuple[WhatsAppLeaseFence, WhatsAppSessionState, str | None]] = field(default_factory=list)
    event_log: list[str] | None = None

    async def mark_awaiting_link(
        self,
        *,
        fence: WhatsAppLeaseFence,
        reset_validation: bool = False,
        allow_disconnecting: bool = False,
    ) -> None:
        self.awaiting_link_calls.append(
            (fence, reset_validation, allow_disconnecting),
        )

    async def mark_state(
        self,
        *,
        fence: WhatsAppLeaseFence,
        state: WhatsAppSessionState,
        failure_code: str | None = None,
    ) -> None:
        self.state_calls.append((fence, state, failure_code))

    async def complete_identity_rejection(
        self,
        *,
        fence: WhatsAppLeaseFence,
        failure_code: str,
    ) -> bool:
        self.identity_rejection_calls.append((fence, failure_code))
        if self.event_log is not None:
            self.event_log.append("complete")
        return True

    async def validate_and_mark_connected(
        self,
        *,
        fence: WhatsAppLeaseFence,
        cipher: WhatsAppSessionCipher,
        identity: WhatsAppReportedIdentity,
    ) -> WhatsAppIdentityValidation:
        del fence, cipher, identity
        return self.identity_validation

    async def mark_identity_rejection_pending(
        self,
        *,
        fence: WhatsAppLeaseFence,
        failure_code: str,
    ) -> None:
        self.identity_rejection_pending_calls.append(
            (fence, failure_code),
        )
        if self.event_log is not None:
            self.event_log.append("pending")

    async def finalize_disconnect(
        self,
        *,
        fence: WhatsAppLeaseFence,
        credential_id: str,
    ) -> None:
        self.finalize_calls.append((fence, credential_id))

    async def accept_inbound_text(
        self,
        *,
        fence: WhatsAppLeaseFence,
        inbound: WhatsAppInboundText,
    ) -> str | None:
        assert fence.integration_id == INTEGRATION_ID
        self.inbound_calls.append(inbound)
        if self.inbound_error is not None:
            raise self.inbound_error
        return "run-one"


def settings() -> WhatsAppWorkerSettings:
    return WhatsAppWorkerSettings(
        whatsapp_worker_poll_interval_seconds=0.5,
        whatsapp_worker_lease_ttl_seconds=30,
        whatsapp_worker_shutdown_timeout_seconds=10,
        whatsapp_bridge_request_timeout_seconds=20,
        whatsapp_bridge_restart_grace_seconds=1,
        whatsapp_max_sessions_per_worker=10,
        whatsapp_challenge_ttl_seconds=120,
    )


def supervisor(
    *,
    session_factory: AsyncSessionFactory,
    vault: UserSecretVaultService,
    worker_settings: WhatsAppWorkerSettings | None = None,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> WhatsAppWorkerSupervisor:
    return WhatsAppWorkerSupervisor(
        base_session_factory=session_factory,
        worker_session_factory=session_factory,
        secret_vault=vault,
        settings=worker_settings or settings(),
        worker_id="worker-one",
        monotonic_clock=monotonic_clock,
    )


def demand(
    *,
    state: WhatsAppSessionState = WhatsAppSessionState.AWAITING_LINK,
    desired_state: WhatsAppDesiredState = WhatsAppDesiredState.CONNECTED,
    integration_id: str = INTEGRATION_ID,
    failure_code: str | None = None,
) -> WhatsAppSessionDemand:
    return WhatsAppSessionDemand(
        user_id=USER_ID,
        integration_id=integration_id,
        resource_id=RESOURCE_ID,
        source_resource_id=SOURCE_RESOURCE_ID,
        credential_id=CREDENTIAL_ID,
        desired_state=desired_state,
        state=state,
        link_method=WhatsAppLinkMethod.QR,
        challenge_request_revision=1,
        failure_code=failure_code,
    )


def fence() -> WhatsAppLeaseFence:
    return WhatsAppLeaseFence(
        user_id=USER_ID,
        integration_id=INTEGRATION_ID,
        owner_id="worker-one",
        generation=3,
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=30),
    )


def active_session(
    *,
    session_demand: WhatsAppSessionDemand,
    data_store: FakeDataStore,
    connected: bool = False,
    bridge_session_started: bool = False,
) -> ActiveWhatsAppSession:
    return ActiveWhatsAppSession(
        demand=session_demand,
        fence=fence(),
        cipher=data_store.cipher,
        data_store=cast(WhatsAppSessionDataStore, data_store),
        next_renew_at=0,
        connected=connected,
        bridge_session_started=bridge_session_started,
    )


def link_challenge_event(
    *,
    expires_at: datetime,
) -> BridgeEventFrame:
    return BridgeEventFrame(
        protocol_version=2,
        frame_type="event",
        request_id="request-one",
        session_handle=INTEGRATION_ID,
        generation=3,
        event="link_challenge",
        payload={
            "method": WhatsAppLinkMethod.QR.value,
            "requested_method": WhatsAppLinkMethod.QR.value,
            "request_revision": 1,
            "value": "private-qr-payload",
            "expires_at": int(expires_at.timestamp() * 1000),
        },
    )


def inbound_text_event(*, request_id: str = "inbound-request-one") -> BridgeEventFrame:
    return BridgeEventFrame(
        protocol_version=2,
        frame_type="event",
        request_id=request_id,
        session_handle=INTEGRATION_ID,
        generation=3,
        event="inbound_text",
        payload={
            "provider_message_id": "provider-message-one",
            "chat_jid": "447700900123@s.whatsapp.net",
            "chat_kind": "direct",
            "sender_jid": "447700900123@s.whatsapp.net",
            "sender_pn_jid": "447700900123@s.whatsapp.net",
            "sender_lid_jid": None,
            "identity_proof": "pn",
            "body": "Hello",
            "timestamp": "2026-07-20T12:00:00Z",
        },
    )


def connected_status_event() -> BridgeEventFrame:
    return BridgeEventFrame(
        protocol_version=2,
        frame_type="event",
        request_id="connected-status-request",
        session_handle=INTEGRATION_ID,
        generation=3,
        event="session_status",
        payload={
            "state": "connected",
            "registered": True,
            "linked": True,
            "reconnect_attempts": 0,
            "identity": {
                "pn_jid": "447700900123@s.whatsapp.net",
                "lid_jid": "123456789@lid",
                "proof": "pn_and_lid",
            },
            "classification": None,
            "failure_code": None,
        },
    )


@pytest.mark.asyncio
async def test_session_status_retains_safe_bridge_failure_classification(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
    )
    worker._runtime = cast(WhatsAppRuntimeService, runtime)

    await worker._handle_session_status(
        active=active,
        fence=active.fence,
        payload=supervisor_module.SessionStatusEventPayload(
            state=WhatsAppSessionState.FAILED.value,
            classification="terminal_local_failure",
        ),
    )

    assert runtime.state_calls == [
        (
            active.fence,
            WhatsAppSessionState.FAILED,
            "terminal_local_failure",
        ),
    ]


@pytest.mark.asyncio
async def test_session_status_prefers_specific_bridge_failure_code(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
    )
    worker._runtime = cast(WhatsAppRuntimeService, runtime)

    await worker._handle_session_status(
        active=active,
        fence=active.fence,
        payload=supervisor_module.SessionStatusEventPayload(
            state=WhatsAppSessionState.RECONNECTING.value,
            classification="transient_reconnect",
            failure_code="connection_replaced",
        ),
    )

    assert runtime.state_calls == [
        (
            active.fence,
            WhatsAppSessionState.RECONNECTING,
            "connection_replaced",
        ),
    ]


@pytest.mark.asyncio
async def test_reconcile_claims_legacy_disconnect_but_not_terminal_or_stopped_demands(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: MonkeyPatch,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    claimed: list[WhatsAppSessionDemand] = []

    async def record_claim(session_demand: WhatsAppSessionDemand) -> None:
        claimed.append(session_demand)

    monkeypatch.setattr(worker, "_claim_and_start", record_claim)
    eligible = demand(integration_id="eligible")
    legacy_disconnect = demand(
        integration_id="legacy-disconnect",
        state=WhatsAppSessionState.FAILED,
        failure_code="terminal_local_failure",
    )
    disconnecting = demand(
        integration_id="disconnecting",
        state=WhatsAppSessionState.DISCONNECTING,
        desired_state=WhatsAppDesiredState.STOPPED,
    )

    await worker._reconcile(
        (
            demand(
                integration_id="needs-relink",
                state=WhatsAppSessionState.NEEDS_RELINK,
            ),
            demand(
                integration_id="failed",
                state=WhatsAppSessionState.FAILED,
                failure_code="session_runtime_failure",
            ),
            demand(
                integration_id="disabled",
                state=WhatsAppSessionState.DISABLED,
            ),
            demand(
                integration_id="stopped",
                desired_state=WhatsAppDesiredState.STOPPED,
            ),
            eligible,
            legacy_disconnect,
            disconnecting,
        ),
    )

    assert claimed == [eligible, legacy_disconnect, disconnecting]


@pytest.mark.asyncio
async def test_run_exits_after_the_poll_restart_window(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: MonkeyPatch,
) -> None:
    timestamps = [0.0, 61.0]

    def monotonic_clock() -> float:
        return timestamps.pop(0) if timestamps else 61.0

    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
        monotonic_clock=monotonic_clock,
    )
    bridge = FakeBridge()

    async def start_bridge() -> None:
        worker._bridge = cast(WhatsAppBridgeClient, bridge)

    async def failed_iteration() -> None:
        raise ConnectionError

    monkeypatch.setattr(worker, "_start_bridge", start_bridge)
    monkeypatch.setattr(worker, "_run_iteration", failed_iteration)

    with pytest.raises(RuntimeError, match="exiting for restart"):
        await worker.run()

    assert bridge.close_calls == 1


@pytest.mark.asyncio
async def test_run_renews_active_leases_while_reconciliation_is_blocked(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: MonkeyPatch,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    worker_store = FakeWorkerStore()
    active = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
    )
    active.next_renew_at = 0
    original_fence = active.fence
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._active[INTEGRATION_ID] = active
    iteration_started = asyncio.Event()
    release_iteration = asyncio.Event()

    async def start_bridge() -> None:
        worker._bridge = cast(WhatsAppBridgeClient, bridge)

    async def blocked_iteration() -> None:
        iteration_started.set()
        await release_iteration.wait()

    monkeypatch.setattr(worker, "_start_bridge", start_bridge)
    monkeypatch.setattr(worker, "_run_iteration", blocked_iteration)

    run_task = asyncio.create_task(worker.run())
    await asyncio.wait_for(iteration_started.wait(), timeout=1)
    await asyncio.wait_for(worker_store.renew_event.wait(), timeout=1)
    worker.request_stop()
    release_iteration.set()
    await asyncio.wait_for(run_task, timeout=1)

    assert worker_store.renew_calls == [original_fence]
    assert active.fence.expires_at > original_fence.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_seconds", [-1, 125])
async def test_link_challenge_rejects_expired_or_excessive_expiry(
    offset_seconds: int,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    active = active_session(
        session_demand=demand(),
        data_store=data_store,
    )

    with pytest.raises(ValueError, match="expiry is invalid"):
        await worker._handle_active_event(
            active=active,
            event=link_challenge_event(
                expires_at=datetime.now(tz=UTC) + timedelta(seconds=offset_seconds),
            ),
            event_fence=active.fence,
        )

    assert data_store.saved_challenges == []


@pytest.mark.asyncio
async def test_link_challenge_persists_valid_expiry(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    active = active_session(
        session_demand=demand(),
        data_store=data_store,
    )
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=60)

    await worker._handle_active_event(
        active=active,
        event=link_challenge_event(expires_at=expires_at),
        event_fence=active.fence,
    )

    assert len(data_store.saved_challenges) == 1
    saved = data_store.saved_challenges[0]
    assert saved.integration_id == INTEGRATION_ID
    assert saved.method == WhatsAppLinkMethod.QR
    assert saved.value == "private-qr-payload"
    assert abs(saved.expires_at - expires_at) <= timedelta(
        milliseconds=1,
    )


@pytest.mark.asyncio
async def test_event_waiting_on_lock_cannot_cross_generation_boundary(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    active = active_session(
        session_demand=demand(),
        data_store=data_store,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._active[INTEGRATION_ID] = active
    await active.control_lock.acquire()
    event = link_challenge_event(
        expires_at=datetime.now(tz=UTC) + timedelta(seconds=60),
    )
    handling = asyncio.create_task(worker._handle_bridge_event(event))
    await asyncio.sleep(0)
    active.fence = WhatsAppLeaseFence(
        user_id=active.fence.user_id,
        integration_id=active.fence.integration_id,
        owner_id=active.fence.owner_id,
        generation=active.fence.generation + 1,
        expires_at=active.fence.expires_at,
    )
    active.control_lock.release()

    await handling

    assert data_store.saved_challenges == []
    assert bridge.rpc_responses == []


@pytest.mark.asyncio
async def test_inbound_event_is_acknowledged_only_after_runtime_accepts_it(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active
    event = inbound_text_event()

    await worker._handle_bridge_event(event)

    assert [inbound.provider_message_id for inbound in runtime.inbound_calls] == [
        "provider-message-one",
    ]
    assert bridge.rpc_responses == [
        RpcResponse(request_id=event.request_id, error_code=None),
    ]


@pytest.mark.asyncio
async def test_inbound_event_persistence_failure_is_negatively_acknowledged(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    runtime = FakeRuntime(inbound_error=RuntimeError("database unavailable"))
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
        bridge_session_started=True,
    )
    event = inbound_text_event(request_id="inbound-request-two")

    await worker._handle_bridge_event(event)

    assert bridge.rpc_responses == [
        RpcResponse(
            request_id=event.request_id,
            error_code="persistence_failed",
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_error", "expected_error_code"),
    (
        (WhatsAppInboundRetryableError(), "persistence_failed"),
        (WhatsAppInboundUnavailableError(), "session_unavailable"),
    ),
)
async def test_inbound_session_state_controls_bridge_acknowledgement(
    runtime_error: Exception,
    expected_error_code: str,
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    runtime = FakeRuntime(inbound_error=runtime_error)
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
        bridge_session_started=True,
    )
    event = inbound_text_event(request_id="inbound-state-request")

    await worker._handle_bridge_event(event)

    assert bridge.rpc_responses == [
        RpcResponse(
            request_id=event.request_id,
            error_code=expected_error_code,
        ),
    ]


@pytest.mark.asyncio
async def test_rejected_identity_is_durably_claimable_before_status_ack(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    event_log: list[str] = []
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge(
        stop_error=WhatsAppBridgeError(
            "logout_uncertain",
            retryable=True,
        ),
        event_log=event_log,
    )
    runtime = FakeRuntime(
        identity_validation=WhatsAppIdentityValidation(
            valid=False,
            failure_code="number_mismatch",
        ),
        event_log=event_log,
    )
    active = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active
    event = connected_status_event()

    await worker._handle_bridge_event(event)

    assert event_log == ["pending", "ack", "stop"]
    assert runtime.identity_rejection_pending_calls == [
        (active.fence, "number_mismatch"),
    ]
    assert runtime.identity_rejection_calls == []
    assert bridge.rpc_responses == [
        RpcResponse(request_id=event.request_id, error_code=None),
    ]
    assert worker._active[INTEGRATION_ID] is active
    assert active.identity_cleanup_pending is False


@pytest.mark.asyncio
async def test_disconnect_reset_always_lets_bridge_decide_whether_to_logout(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    worker_store = FakeWorkerStore()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(
            state=WhatsAppSessionState.DISCONNECTING,
            desired_state=WhatsAppDesiredState.STOPPED,
        ),
        data_store=data_store,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active

    await worker._process_reset(active)

    assert bridge.stop_calls == [
        StopCall(
            session_handle=INTEGRATION_ID,
            generation=3,
            logout=True,
        ),
    ]
    assert data_store.clear_calls == [INTEGRATION_ID]
    assert runtime.finalize_calls == [(active.fence, CREDENTIAL_ID)]
    assert worker_store.release_calls == [active.fence]
    assert INTEGRATION_ID not in worker._active


@pytest.mark.asyncio
async def test_uncertain_logout_keeps_auth_and_lease_for_retry(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge(
        stop_error=WhatsAppBridgeError(
            "logout_uncertain",
            retryable=True,
        ),
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    worker_store = FakeWorkerStore()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(
            state=WhatsAppSessionState.DISCONNECTING,
            desired_state=WhatsAppDesiredState.STOPPED,
        ),
        data_store=data_store,
        connected=True,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active

    await worker._process_reset(active)

    assert bridge.stop_calls[0].logout is True
    assert data_store.clear_calls == []
    assert runtime.finalize_calls == []
    assert worker_store.release_calls == []
    assert worker._active[INTEGRATION_ID] is active
    assert active.bridge_session_started is False
    assert active.connected is False
    assert bridge.close_calls == 1
    assert active.reset_retry_at > 0


@pytest.mark.asyncio
async def test_uncertain_logout_starts_a_fresh_relink_session(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge(
        stop_error=WhatsAppBridgeError(
            "logout_uncertain",
            retryable=True,
        ),
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    worker_store = FakeWorkerStore()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(
            state=WhatsAppSessionState.DISCONNECTING,
            desired_state=WhatsAppDesiredState.CONNECTED,
        ),
        data_store=data_store,
        connected=True,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active

    await worker._process_reset(active)

    assert bridge.close_calls == 0
    assert data_store.clear_calls == [INTEGRATION_ID]
    assert len(worker_store.advance_calls) == 1
    assert worker_store.advance_calls[0].generation == 3
    assert bridge.start_calls[0].generation == 4
    assert bridge.challenge_calls[0].generation == 4
    assert active.reset_retry_at == 0.0


@pytest.mark.asyncio
async def test_restart_resumes_durable_identity_rejection_cleanup(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge()
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    worker_store = FakeWorkerStore()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(
            state=WhatsAppSessionState.DISCONNECTING,
            failure_code="number_mismatch",
        ),
        data_store=data_store,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active

    await worker._process_reset(active)

    assert bridge.stop_calls == [
        StopCall(
            session_handle=INTEGRATION_ID,
            generation=3,
            logout=True,
        ),
    ]
    assert data_store.clear_calls == [INTEGRATION_ID]
    assert runtime.identity_rejection_calls == [
        (active.fence, "number_mismatch"),
    ]
    assert runtime.awaiting_link_calls == []
    assert worker_store.advance_calls == []
    assert worker_store.release_calls == [active.fence]
    assert INTEGRATION_ID not in worker._active


@pytest.mark.asyncio
async def test_relink_restart_failure_stops_and_releases_the_reset_session(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge(
        start_error=WhatsAppBridgeError(
            "restart_failed",
            retryable=True,
        ),
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    worker_store = FakeWorkerStore()
    runtime = FakeRuntime()
    active = active_session(
        session_demand=demand(
            state=WhatsAppSessionState.DISCONNECTING,
            desired_state=WhatsAppDesiredState.CONNECTED,
        ),
        data_store=data_store,
        connected=True,
        bridge_session_started=True,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)
    worker._active[INTEGRATION_ID] = active

    await worker._process_reset(active)

    assert len(bridge.start_calls) == 1
    assert len(bridge.stop_calls) == 2
    assert bridge.start_calls[0].generation == 4
    assert runtime.awaiting_link_calls == [
        (worker_store.advance_calls[0], True, True),
    ]
    assert len(worker_store.advance_calls) == 1
    assert worker_store.advance_calls[0].generation == 3
    assert worker_store.release_calls == [active.fence]
    assert worker.active_session_count == 0


@pytest.mark.asyncio
async def test_bridge_start_snapshot_cannot_bypass_identity_validation(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    bridge = FakeBridge(
        start_result=BridgeStartResult(
            state="connected",
            registered=True,
            linked=True,
            reconnect_attempts=0,
            resumed=True,
        ),
    )
    data_store = FakeDataStore(WhatsAppSessionCipher(b"k" * 32))
    active = active_session(
        session_demand=demand(),
        data_store=data_store,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)

    result = await worker._start_bridge_session(
        active=active,
        credentials={"registered": True},
    )

    assert result.state == "connected"
    assert active.bridge_session_started is True
    assert active.connected is False


@pytest.mark.asyncio
async def test_linked_restart_does_not_request_a_new_challenge_while_connecting(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: MonkeyPatch,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    session_fence = fence()
    bridge = FakeBridge(
        start_result=BridgeStartResult(
            state="connecting",
            registered=True,
            linked=True,
            reconnect_attempts=0,
            resumed=True,
        ),
    )
    data_store = FakeDataStore(
        WhatsAppSessionCipher(b"k" * 32),
        credentials={"registered": True},
    )
    worker_store = FakeWorkerStore(claim_result=session_fence)
    runtime = FakeRuntime()

    def build_data_store(
        *,
        session_factory: AsyncSessionFactory,
        vault: UserSecretVaultService,
        user_id: str,
    ) -> WhatsAppSessionDataStore:
        assert session_factory is sqlite_session_factory
        assert vault is user_secret_vault
        assert user_id == USER_ID
        return cast(WhatsAppSessionDataStore, data_store)

    monkeypatch.setattr(
        supervisor_module,
        "WhatsAppSessionDataStore",
        build_data_store,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._runtime = cast(WhatsAppRuntimeService, runtime)

    await worker._claim_and_start(
        demand(state=WhatsAppSessionState.CONNECTED),
    )
    await worker._reconcile(
        (demand(state=WhatsAppSessionState.CONNECTING),),
    )

    assert bridge.challenge_calls == []
    assert runtime.awaiting_link_calls == []
    assert worker.active_session_count == 1


@pytest.mark.asyncio
async def test_failed_start_stops_bridge_before_releasing_lease(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: MonkeyPatch,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    session_fence = fence()
    bridge = FakeBridge(
        start_error=WhatsAppBridgeError(
            "start_failed",
            retryable=True,
        ),
    )
    data_store = FakeDataStore(
        WhatsAppSessionCipher(b"k" * 32),
        credentials={"registered": False},
    )
    worker_store = FakeWorkerStore(claim_result=session_fence)

    def build_data_store(
        *,
        session_factory: AsyncSessionFactory,
        vault: UserSecretVaultService,
        user_id: str,
    ) -> WhatsAppSessionDataStore:
        assert session_factory is sqlite_session_factory
        assert vault is user_secret_vault
        assert user_id == USER_ID
        return cast(WhatsAppSessionDataStore, data_store)

    monkeypatch.setattr(
        supervisor_module,
        "WhatsAppSessionDataStore",
        build_data_store,
    )
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)

    await worker._claim_and_start(demand())

    assert len(bridge.start_calls) == 1
    assert bridge.stop_calls == [
        StopCall(
            session_handle=INTEGRATION_ID,
            generation=3,
            logout=False,
        ),
    ]
    assert worker_store.release_calls == [session_fence]
    assert worker.active_session_count == 0


@pytest.mark.asyncio
async def test_failed_start_kills_bridge_before_releasing_uncertain_session(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    worker = supervisor(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    session_fence = fence()
    bridge = FakeBridge(
        stop_error=WhatsAppBridgeError(
            "bridge_unavailable",
            retryable=True,
        ),
    )
    worker_store = FakeWorkerStore()
    worker._bridge = cast(WhatsAppBridgeClient, bridge)
    worker._worker_store = cast(WhatsAppWorkerStore, worker_store)
    worker._active[INTEGRATION_ID] = active_session(
        session_demand=demand(),
        data_store=FakeDataStore(WhatsAppSessionCipher(b"k" * 32)),
        bridge_session_started=True,
    )

    await worker._cleanup_failed_start(
        integration_id=INTEGRATION_ID,
        fence=session_fence,
    )

    assert len(bridge.stop_calls) == 1
    assert bridge.close_calls == 1
    assert worker_store.release_calls == [session_fence]
    assert worker.active_session_count == 0

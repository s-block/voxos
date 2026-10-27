from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest

from voxos.agent_core.integration_tools import IntegrationAgentToolProvider
from voxos.agent_core.internal_tools import InternalAgentToolProvider
from voxos.agent_core.mcp_tools import CompositeAgentToolProvider
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_execution import AgentRunExecutionResult, TextAgentRunExecutor
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_settings import AgentRunWorkerSettings
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.run_worker import AgentRunWorker
from voxos.api.chats import build_text_chat_service
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory


@dataclass(slots=True)
class FakeExecutor:
    calls: list[str]
    fail: bool = False

    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        self.calls.append(run.id)
        if self.fail:
            message = "boom"
            raise RuntimeError(message)
        return AgentRunExecutionResult(result_json={"ok": True})


def empty_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code=404, request=request)


@pytest.mark.asyncio
async def test_shared_text_service_wires_contact_and_phone_tools_for_durable_runs(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    try:
        service = build_text_chat_service(
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            http_client=http_client,
            public_base_url="https://api.example.test",
        )
    finally:
        await http_client.aclose()

    assert isinstance(service.tool_provider, CompositeAgentToolProvider)
    internal_provider = next(
        provider for provider in service.tool_provider.providers if isinstance(provider, InternalAgentToolProvider)
    )
    assert internal_provider.contact_session_factory is sqlite_session_factory
    assert any(isinstance(provider, IntegrationAgentToolProvider) for provider in service.tool_provider.providers)


@pytest.mark.asyncio
async def test_run_worker_claims_executes_and_completes(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Do work"},
        created_by_user_id="user-123",
    )
    executor = FakeExecutor(calls=[])
    worker = AgentRunWorker(
        run_store=store,
        executor=executor,
        claimable_kinds=(AgentRunKind.MANUAL_AGENT_TASK,),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="worker-1",
            AGENT_RUN_QUEUES="agent-default",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
            AGENT_RUN_HEARTBEAT_INTERVAL_SECONDS=1,
            AGENT_RUN_LEASE_TIMEOUT_SECONDS=60,
            AGENT_RUN_POLL_INTERVAL_SECONDS=1,
            AGENT_RUN_RETRY_DELAY_SECONDS=1,
            AGENT_RUN_SHUTDOWN_GRACE_SECONDS=1,
        ),
    )

    claimed_count = await worker.run_once()
    await worker.wait_for_idle()

    completed = await store.get_run(user_id="user-123", run_id=run.id)
    assert claimed_count == 1
    assert executor.calls == [run.id]
    assert completed is not None
    assert completed.status == AgentRunStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_run_worker_does_not_claim_inline_text_chat_turns(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    inline_run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.TEXT_CHAT_TURN,
        payload_json={"message_characters": 5},
        created_by_user_id="user-123",
    )
    durable_run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Do work"},
        created_by_user_id="user-123",
    )
    executor = FakeExecutor(calls=[])
    worker = AgentRunWorker(
        run_store=store,
        executor=executor,
        claimable_kinds=(AgentRunKind.MANUAL_AGENT_TASK,),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="worker-1",
            AGENT_RUN_QUEUES="agent-default",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
        ),
    )

    claimed_count = await worker.run_once()
    await worker.wait_for_idle()

    untouched = await store.get_run(user_id="user-123", run_id=inline_run.id)
    completed = await store.get_run(user_id="user-123", run_id=durable_run.id)
    assert claimed_count == 1
    assert executor.calls == [durable_run.id]
    assert untouched is not None
    assert untouched.status == AgentRunStatus.QUEUED.value
    assert untouched.lease_owner is None
    assert completed is not None
    assert completed.status == AgentRunStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_run_worker_drain_processes_queue_until_empty(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    runs = [
        await store.enqueue_run(
            user_id="user-123",
            agent_id=agent.id,
            kind=AgentRunKind.MANUAL_AGENT_TASK,
            payload_json={"text": f"Task {index}"},
            created_by_user_id="user-123",
        )
        for index in range(2)
    ]
    executor = FakeExecutor(calls=[])
    worker = AgentRunWorker(
        run_store=store,
        executor=executor,
        claimable_kinds=(AgentRunKind.MANUAL_AGENT_TASK,),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="worker-1",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
        ),
    )

    processed = await worker.drain()

    assert processed == 2
    assert executor.calls == [run.id for run in runs]
    for run in runs:
        completed = await store.get_run(user_id="user-123", run_id=run.id)
        assert completed is not None
        assert completed.status == AgentRunStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_worker_work_check_finds_due_retry_and_stale_lease(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    now = utc_now()
    await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Retry later"},
        created_by_user_id="user-123",
        queue="retry-queue",
        next_attempt_at=now + timedelta(minutes=5),
    )

    assert not await store.has_worker_work(queues=("retry-queue",), now=now)
    assert await store.has_worker_work(
        queues=("retry-queue",),
        now=now + timedelta(minutes=6),
    )

    await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Lease expires"},
        created_by_user_id="user-123",
        queue="stale-queue",
    )
    claimed = await store.claim_runs(
        worker_id="worker-1",
        queues=("stale-queue",),
        batch_size=1,
        lease_seconds=60,
    )

    assert len(claimed) == 1
    assert not await store.has_worker_work(queues=("stale-queue",), now=now)
    assert await store.has_worker_work(
        queues=("stale-queue",),
        now=now + timedelta(minutes=2),
    )


@pytest.mark.asyncio
async def test_run_worker_requeues_retryable_failures(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.MANUAL_AGENT_TASK,
        payload_json={"text": "Fail once"},
        created_by_user_id="user-123",
        max_attempts=2,
    )
    worker = AgentRunWorker(
        run_store=store,
        executor=FakeExecutor(calls=[], fail=True),
        claimable_kinds=(AgentRunKind.MANUAL_AGENT_TASK,),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="worker-1",
            AGENT_RUN_QUEUES="agent-default",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
            AGENT_RUN_HEARTBEAT_INTERVAL_SECONDS=1,
            AGENT_RUN_LEASE_TIMEOUT_SECONDS=60,
            AGENT_RUN_POLL_INTERVAL_SECONDS=1,
            AGENT_RUN_RETRY_DELAY_SECONDS=1,
            AGENT_RUN_SHUTDOWN_GRACE_SECONDS=1,
        ),
    )

    await worker.run_once()
    await worker.wait_for_idle()

    failed = await store.get_run(user_id="user-123", run_id=run.id)
    assert failed is not None
    assert failed.status == AgentRunStatus.QUEUED.value
    assert failed.error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_text_run_executor_finalizes_provider_setup_failures(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent("user-123")
    store = SqlModelAgentRunStore(session_factory=sqlite_session_factory)
    run = await store.enqueue_run(
        user_id="user-123",
        agent_id=agent.id,
        kind=AgentRunKind.SCHEDULED_AGENT_TURN,
        payload_json={"text": "Summarize my day"},
        created_by_user_id="user-123",
        max_attempts=1,
    )
    started = await store.start_run(
        run_id=run.id,
        user_id="user-123",
        lease_owner="worker-1",
        lease_expires_at=run.created_at,
    )
    assert started is not None
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_response))
    text_service = build_text_chat_service(
        session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
        http_client=http_client,
    )
    executor = TextAgentRunExecutor(
        text_service=text_service,
        run_store=store,
        run_chat_service=text_service.run_chat_service,
        fallback_executor=FakeExecutor(calls=[]),
    )

    try:
        result = await executor.execute(started)
    finally:
        await http_client.aclose()

    failed = await store.get_run(user_id="user-123", run_id=run.id)
    assert result.finalized is True
    assert failed is not None
    assert failed.status == AgentRunStatus.FAILED.value

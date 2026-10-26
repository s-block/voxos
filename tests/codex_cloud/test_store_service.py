from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import cast

import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_settings import AgentRunWorkerSettings
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.run_worker import AgentRunWorker
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.codex_cloud.execution import CodexCloudRunExecutor
from voxos.codex_cloud.models import (
    CodexCloudSubmissionState,
    CodexCloudTaskCreateRequest,
    CodexCloudTaskRunPayload,
)
from voxos.codex_cloud.records import CodexAccountRecord
from voxos.codex_cloud.sandbox import (
    CodexLoginResult,
    CodexSandboxError,
    CodexSandboxResult,
    DockerCodexSandboxRunner,
)
from voxos.codex_cloud.secrets import CodexAccountSecretStore, OpaqueCodexAuth
from voxos.codex_cloud.service import CodexCloudService
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.codex_cloud.store import (
    CodexCloudConflictError,
    CodexCloudNotFoundError,
    CodexCloudStore,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService, RunChatValidationError


@dataclass(slots=True)
class FakeCodexSandboxRunner:
    outcomes: list[CodexSandboxResult | CodexSandboxError]
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def run(
        self,
        *,
        arguments: Sequence[str],
        auth: OpaqueCodexAuth,
        timeout_seconds: float | None = None,
        ambiguous_on_failure: bool = False,
    ) -> CodexSandboxResult:
        del auth, timeout_seconds, ambiguous_on_failure
        self.calls.append(tuple(arguments))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, CodexSandboxError):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_two_tenants_store_distinct_encrypted_codex_accounts(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    store = _store(sqlite_session_factory, user_secret_vault)

    account_a = await _connect(store, user_id="tenant-a", email="a@example.com")
    account_b = await _connect(store, user_id="tenant-b", email="b@example.com")

    assert account_a.id != account_b.id
    assert (await store.account_response(user_id="tenant-a")).email == "a@example.com"
    assert (await store.account_response(user_id="tenant-b")).email == "b@example.com"
    auth_a = await store.load_account_auth(user_id="tenant-a", account_id=account_a.id)
    assert auth_a.value.get_secret_value() == '{"auth_mode":"chatgpt"}'
    with pytest.raises(CodexCloudNotFoundError):
        await store.load_account_auth(user_id="tenant-a", account_id=account_b.id)


@pytest.mark.asyncio
async def test_account_operations_are_serialized_and_fenced(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    store = _store(sqlite_session_factory, user_secret_vault)
    account = await _connect(store, user_id="tenant-a", email="a@example.com")
    generation = await store.acquire_account_lease(
        user_id="tenant-a",
        account_id=account.id,
        worker_id="worker-a",
        lease_seconds=60,
    )

    with pytest.raises(CodexCloudConflictError):
        await store.acquire_account_lease(
            user_id="tenant-a",
            account_id=account.id,
            worker_id="worker-b",
            lease_seconds=60,
        )

    await store.release_account_lease(
        user_id="tenant-a",
        account_id=account.id,
        worker_id="worker-a",
        generation=generation,
    )
    next_generation = await store.acquire_account_lease(
        user_id="tenant-a",
        account_id=account.id,
        worker_id="worker-b",
        lease_seconds=60,
    )
    assert next_generation > generation
    with pytest.raises(CodexCloudConflictError):
        await store.persist_refreshed_auth(
            user_id="tenant-a",
            account_id=account.id,
            auth=OpaqueCodexAuth(SecretStr('{"auth_mode":"chatgpt"}')),
            worker_id="worker-a",
            generation=generation,
        )


@pytest.mark.asyncio
async def test_service_enqueues_a_codex_cloud_run_and_task_mapping(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    account = await _connect(store, user_id=user_id, email="a@example.com")
    await store.update_environment(
        user_id=user_id,
        environment_id="environment-123",
        environment_label="Demo",
    )
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    service = CodexCloudService(
        store=store,
        run_store=run_store,
        run_chat_service=_run_chat_service(sqlite_session_factory, run_store),
        settings=CodexCloudSettings(),
    )

    task = await service.submit_task(
        user_id=user_id,
        request=CodexCloudTaskCreateRequest(
            agent_id=agent.id,
            prompt="Fix the failing test",
            branch="main",
            attempts=2,
            idempotency_key="request-1",
        ),
    )

    assert task.run.kind.value == "codex_cloud_task"
    assert task.run.queue == "codex-cloud"
    assert task.run.chat_id is not None
    assert task.run.session_id == task.run.chat_id
    assert task.environment_id == "environment-123"
    assert task.attempts == 2
    assert task.run.payload_json == {
        "account_id": account.id,
        "attempts": 2,
        "branch": "main",
        "environment_id": "environment-123",
        "prompt": "Fix the failing test",
    }


@pytest.mark.asyncio
async def test_codex_task_and_run_are_committed_atomically(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    account = await _connect(store, user_id=user_id, email="a@example.com")
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    invalid_payload = CodexCloudTaskRunPayload.model_construct(
        account_id=account.id,
        environment_id="environment-123",
        prompt="This transaction must roll back",
        branch=None,
        attempts=5,
    )

    with pytest.raises(IntegrityError):
        await store.create_task_run(
            run_store=run_store,
            run_chat_service=_run_chat_service(sqlite_session_factory, run_store),
            user_id=user_id,
            agent_id=agent.id,
            chat_id=None,
            payload=invalid_payload,
            idempotency_key=None,
        )

    assert await run_store.list_runs(user_id=user_id) == ()


@pytest.mark.asyncio
async def test_service_reuses_only_a_same_tenant_selected_chat(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    await _connect(store, user_id=user_id, email="a@example.com")
    await store.update_environment(
        user_id=user_id,
        environment_id="environment-123",
        environment_label=None,
    )
    identity_service = AgentIdentityService(session_factory=sqlite_session_factory)
    agent = await identity_service.get_or_create_default_agent(user_id)
    other_agent = await identity_service.get_or_create_default_agent("tenant-b")
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    run_chat_service = _run_chat_service(sqlite_session_factory, run_store)
    selected_chat = await run_chat_service.create_chat(
        user_id=user_id,
        agent_id=agent.id,
        title="Existing task chat",
    )
    other_chat = await run_chat_service.create_chat(
        user_id="tenant-b",
        agent_id=other_agent.id,
        title="Other tenant chat",
    )
    service = CodexCloudService(
        store=store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        settings=CodexCloudSettings(),
    )

    task = await service.submit_task(
        user_id=user_id,
        request=CodexCloudTaskCreateRequest(
            agent_id=agent.id,
            chat_id=selected_chat.id,
            prompt="Use the selected chat",
        ),
    )

    assert task.run.chat_id == selected_chat.id
    with pytest.raises(RunChatValidationError):
        await service.submit_task(
            user_id=user_id,
            request=CodexCloudTaskCreateRequest(
                agent_id=agent.id,
                chat_id=other_chat.id,
                prompt="Do not use another tenant's chat",
            ),
        )


@pytest.mark.asyncio
async def test_executor_does_not_duplicate_a_submission_after_an_ambiguous_restart(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    await _connect(store, user_id=user_id, email="a@example.com")
    await store.update_environment(
        user_id=user_id,
        environment_id="environment-123",
        environment_label=None,
    )
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    settings = CodexCloudSettings()
    service = CodexCloudService(
        store=store,
        run_store=run_store,
        run_chat_service=_run_chat_service(sqlite_session_factory, run_store),
        settings=settings,
    )
    task = await service.submit_task(
        user_id=user_id,
        request=CodexCloudTaskCreateRequest(
            agent_id=agent.id,
            prompt="Do not submit twice",
        ),
    )
    await store.update_task_submission(
        user_id=user_id,
        task_id=task.id,
        submission_state=CodexCloudSubmissionState.SUBMITTING,
    )
    (claimed_run,) = await run_store.claim_runs(
        worker_id="codex-worker",
        queues=("codex-cloud",),
        batch_size=1,
        lease_seconds=180,
    )

    result = await CodexCloudRunExecutor(
        store=store,
        run_store=run_store,
        run_chat_service=_run_chat_service(sqlite_session_factory, run_store),
        sandbox=DockerCodexSandboxRunner(settings),
        settings=settings,
    ).execute(claimed_run)

    updated_task = await store.get_task(user_id=user_id, task_id=task.id)
    updated_run = await run_store.get_run(user_id=user_id, run_id=claimed_run.id)
    assert task.run.chat_id is not None
    transcript = await SqlModelAgentRuntimeStore(
        sqlite_session_factory,
    ).list_transcript_items(
        session_id=task.run.chat_id,
        user_id=user_id,
        agent_id=agent.id,
    )
    assert result.finalized is True
    assert updated_task is not None
    assert updated_task.submission_state == CodexCloudSubmissionState.SUBMISSION_UNCERTAIN.value
    assert updated_run is not None
    assert updated_run.status == "failed"
    assert updated_run.error_type == "CodexCloudSubmissionUncertain"
    assert [item.text for item in transcript] == [
        "The Codex Cloud submission outcome is uncertain. Check Codex Cloud before submitting another task.",
    ]
    assert transcript[0].client_turn_id == f"run:{claimed_run.id}:uncertain"


@pytest.mark.asyncio
async def test_submitted_task_tracking_survives_chat_redaction(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    await _connect(store, user_id=user_id, email="a@example.com")
    await store.update_environment(
        user_id=user_id,
        environment_id="environment-123",
        environment_label=None,
    )
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    run_chat_service = _run_chat_service(sqlite_session_factory, run_store)
    task = await CodexCloudService(
        store=store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        settings=CodexCloudSettings(),
    ).submit_task(
        user_id=user_id,
        request=CodexCloudTaskCreateRequest(
            agent_id=agent.id,
            prompt="Track this task after its chat is deleted",
        ),
    )
    remote_task_id = "remote-task-1"
    remote_task_url = f"https://chatgpt.com/codex/tasks/{remote_task_id}"
    await store.update_task_submission(
        user_id=user_id,
        task_id=task.id,
        submission_state=CodexCloudSubmissionState.SUBMITTED,
        remote_task_id=remote_task_id,
        remote_task_url=remote_task_url,
    )
    assert task.run.chat_id is not None
    await SqlModelAgentRuntimeStore(sqlite_session_factory).soft_delete_session(
        session_id=task.run.chat_id,
        user_id=user_id,
        agent_id=agent.id,
    )
    await run_store.redact_run(run_id=task.run.id, user_id=user_id)
    (claimed_run,) = await run_store.claim_runs(
        worker_id="codex-worker",
        queues=("codex-cloud",),
        batch_size=1,
        lease_seconds=180,
    )
    sandbox = FakeCodexSandboxRunner(
        outcomes=[
            CodexSandboxResult(
                stdout=orjson.dumps(
                    {
                        "tasks": [
                            {
                                "environment_id": "environment-123",
                                "id": remote_task_id,
                                "status": "READY",
                                "summary": "Tracking completed",
                                "url": remote_task_url,
                            },
                        ],
                    },
                ).decode(),
                stderr="",
            ),
        ],
    )

    result = await CodexCloudRunExecutor(
        store=store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        sandbox=cast(DockerCodexSandboxRunner, sandbox),
        settings=CodexCloudSettings(),
    ).execute(claimed_run)
    completed = await run_store.complete_run(
        run_id=claimed_run.id,
        user_id=user_id,
        result_json=result.result_json,
    )

    assert completed is not None
    assert completed.status == "succeeded"
    assert result.result_json["remote_status"] == "ready"
    assert sandbox.calls[0][0:3] == ("cloud", "list", "--env")


@pytest.mark.asyncio
async def test_final_codex_worker_failure_posts_one_chat_update(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "tenant-a"
    store = _store(sqlite_session_factory, user_secret_vault)
    await _connect(store, user_id=user_id, email="a@example.com")
    await store.update_environment(
        user_id=user_id,
        environment_id="environment-123",
        environment_label=None,
    )
    agent = await AgentIdentityService(
        session_factory=sqlite_session_factory,
    ).get_or_create_default_agent(user_id)
    run_store = SqlModelAgentRunStore(sqlite_session_factory)
    run_chat_service = _run_chat_service(sqlite_session_factory, run_store)
    task = await CodexCloudService(
        store=store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        settings=CodexCloudSettings(),
    ).submit_task(
        user_id=user_id,
        request=CodexCloudTaskCreateRequest(
            agent_id=agent.id,
            prompt="Fail safely",
        ),
    )
    async with sqlite_session_factory() as session:
        run = await session.get(AgentRunRecord, task.run.id)
        assert run is not None
        run.max_attempts = 1
        session.add(run)
        await session.commit()
    sandbox = FakeCodexSandboxRunner(
        outcomes=[CodexSandboxError("codex_authentication_failed")],
    )
    executor = CodexCloudRunExecutor(
        store=store,
        run_store=run_store,
        run_chat_service=run_chat_service,
        sandbox=cast(DockerCodexSandboxRunner, sandbox),
        settings=CodexCloudSettings(),
    )
    worker = AgentRunWorker(
        run_store=run_store,
        executor=executor,
        claimable_kinds=(AgentRunKind.CODEX_CLOUD_TASK,),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="codex-worker",
            AGENT_RUN_QUEUES="codex-cloud",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
        ),
    )

    await worker.run_once()
    await worker.wait_for_idle()
    failed = await run_store.get_run(user_id=user_id, run_id=task.run.id)
    assert task.run.chat_id is not None
    transcript = await SqlModelAgentRuntimeStore(
        sqlite_session_factory,
    ).list_transcript_items(
        session_id=task.run.chat_id,
        user_id=user_id,
        agent_id=agent.id,
    )

    assert failed is not None
    assert failed.status == "failed"
    assert [item.text for item in transcript] == [
        "Codex Cloud task failed. Check Operations for details.",
    ]
    assert transcript[0].client_turn_id == f"run:{task.run.id}:failed"


def _store(
    session_factory: AsyncSessionFactory,
    vault: UserSecretVaultService,
) -> CodexCloudStore:
    return CodexCloudStore(
        session_factory=session_factory,
        secret_store=CodexAccountSecretStore(vault),
    )


def _run_chat_service(
    session_factory: AsyncSessionFactory,
    run_store: SqlModelAgentRunStore,
) -> RunChatService:
    return RunChatService(
        session_factory=session_factory,
        runtime_store=SqlModelAgentRuntimeStore(session_factory),
        run_store=run_store,
    )


async def _connect(
    store: CodexCloudStore,
    *,
    user_id: str,
    email: str,
) -> CodexAccountRecord:
    auth_session = await store.create_auth_session(
        user_id=user_id,
        expires_at=utc_now() + timedelta(minutes=15),
    )
    claimed = await store.claim_auth_sessions(
        worker_id="login-worker",
        batch_size=10,
        lease_seconds=960,
    )
    assert auth_session.id in {record.id for record in claimed}
    return await store.complete_auth_session(
        user_id=user_id,
        auth_session_id=auth_session.id,
        worker_id="login-worker",
        result=CodexLoginResult(
            auth_json=SecretStr('{"auth_mode":"chatgpt"}'),
            email=email,
            plan_type="plus",
        ),
    )

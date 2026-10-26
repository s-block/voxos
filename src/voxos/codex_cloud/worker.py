import asyncio
import logging
import signal
from dataclasses import dataclass, field

from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_settings import AgentRunWorkerSettings
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.run_worker import AgentRunWorker
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.auth.settings import FirebaseAuthSettings
from voxos.codex_cloud.execution import CodexCloudRunExecutor
from voxos.codex_cloud.models import CODEX_CLOUD_QUEUE
from voxos.codex_cloud.sandbox import CodexSandboxError, DockerCodexSandboxRunner
from voxos.codex_cloud.secrets import CodexAccountSecretStore
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.codex_cloud.store import CodexCloudStore
from voxos.credentials.services import CredentialsServiceFactory
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import (
    agent_worker_session_factory,
    context_tenant_session_factory,
    tenant_user_context,
)
from voxos.notifications.factory import create_push_delivery_worker
from voxos.notifications.settings import PushNotificationSettings
from voxos.observability.sentry import initialize_sentry
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.text.run_chat_service import RunChatService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CodexAuthSessionWorker:
    store: CodexCloudStore
    sandbox: DockerCodexSandboxRunner
    settings: CodexCloudSettings
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def run_once(self) -> int:
        self._collect_finished_tasks()
        capacity = self.settings.max_concurrency - len(self._tasks)
        if capacity <= 0:
            return 0
        sessions = await self.store.claim_auth_sessions(
            worker_id=self.settings.worker_id,
            batch_size=min(capacity, self.settings.claim_batch_size),
            lease_seconds=self.settings.login_timeout_seconds + 60,
        )
        for auth_session in sessions:
            task = asyncio.create_task(self._execute(auth_session.id, auth_session.user_id))
            self._tasks.add(task)
        return len(sessions)

    async def wait_for_idle(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks)
        self._collect_finished_tasks()

    async def _execute(self, auth_session_id: str, user_id: str) -> None:
        with tenant_user_context(user_id):
            try:
                result = await self.sandbox.login_with_device_code(
                    on_challenge=lambda challenge: self.store.set_auth_challenge(
                        user_id=user_id,
                        auth_session_id=auth_session_id,
                        worker_id=self.settings.worker_id,
                        login_id=challenge.login_id,
                        verification_url=str(challenge.verification_url),
                        user_code=challenge.user_code,
                    ),
                    is_cancelled=lambda: self.store.is_auth_cancelled(
                        user_id=user_id,
                        auth_session_id=auth_session_id,
                    ),
                )
                await self.store.complete_auth_session(
                    user_id=user_id,
                    auth_session_id=auth_session_id,
                    worker_id=self.settings.worker_id,
                    result=result,
                )
            except CodexSandboxError as exc:
                await self.store.fail_auth_session(
                    user_id=user_id,
                    auth_session_id=auth_session_id,
                    error_message=exc.code,
                )
            except Exception as exc:
                logger.exception(
                    "Codex login session failed",
                    extra={"auth_session_id": auth_session_id, "user_id": user_id},
                )
                await self.store.fail_auth_session(
                    user_id=user_id,
                    auth_session_id=auth_session_id,
                    error_message=type(exc).__name__,
                )

    def _collect_finished_tasks(self) -> None:
        finished = {task for task in self._tasks if task.done()}
        self._tasks.difference_update(finished)
        for task in finished:
            task.result()


@dataclass(slots=True)
class CodexCloudWorker:
    run_worker: AgentRunWorker
    auth_worker: CodexAuthSessionWorker
    settings: CodexCloudSettings
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)

    async def run_forever(self) -> None:
        queue_store = self.run_worker.queue_run_store or self.run_worker.run_store
        await queue_store.reconcile_stale_leases(
            queues=(CODEX_CLOUD_QUEUE,),
            kinds=(AgentRunKind.CODEX_CLOUD_TASK,),
            retry_delay_seconds=self.settings.retry_delay_seconds,
        )
        while not self._stopping.is_set():
            await self.auth_worker.run_once()
            await self.run_worker.run_once()
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.settings.poll_interval_seconds,
                )
            except TimeoutError:
                continue
        await asyncio.gather(
            self.auth_worker.wait_for_idle(),
            self.run_worker.wait_for_idle(),
        )

    def stop(self) -> None:
        self._stopping.set()
        self.run_worker.stop()


async def run_worker_from_settings(settings: CodexCloudSettings | None = None) -> None:
    initialize_sentry("codex-worker")
    resolved_settings = settings or CodexCloudSettings()
    if not resolved_settings.enabled:
        message = "Codex Cloud integration is disabled"
        raise RuntimeError(message)
    engine = create_database_engine(DatabaseSettings())
    base_session_factory = create_session_factory(engine)
    tenant_session_factory = context_tenant_session_factory(base_session_factory)
    queue_session_factory = agent_worker_session_factory(base_session_factory)
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    store = CodexCloudStore(
        session_factory=tenant_session_factory,
        queue_session_factory=queue_session_factory,
        secret_store=CodexAccountSecretStore(secret_vault),
    )
    push_settings = PushNotificationSettings()
    run_store = SqlModelAgentRunStore(
        session_factory=tenant_session_factory,
        terminal_notifications_enabled=push_settings.enabled,
        terminal_notification_max_attempts=push_settings.max_attempts,
    )
    queue_run_store = SqlModelAgentRunStore(session_factory=queue_session_factory)
    sandbox = DockerCodexSandboxRunner(resolved_settings)
    run_worker_settings = AgentRunWorkerSettings(
        AGENT_RUN_WORKER_ID=resolved_settings.worker_id,
        AGENT_RUN_QUEUES=CODEX_CLOUD_QUEUE,
        AGENT_RUN_MAX_CONCURRENCY=resolved_settings.max_concurrency,
        AGENT_RUN_CLAIM_BATCH_SIZE=resolved_settings.claim_batch_size,
        AGENT_RUN_HEARTBEAT_INTERVAL_SECONDS=resolved_settings.heartbeat_interval_seconds,
        AGENT_RUN_LEASE_TIMEOUT_SECONDS=resolved_settings.lease_timeout_seconds,
        AGENT_RUN_POLL_INTERVAL_SECONDS=resolved_settings.poll_interval_seconds,
        AGENT_RUN_RETRY_DELAY_SECONDS=resolved_settings.retry_delay_seconds,
        AGENT_RUN_SHUTDOWN_GRACE_SECONDS=resolved_settings.shutdown_grace_seconds,
    )
    worker = CodexCloudWorker(
        run_worker=AgentRunWorker(
            run_store=run_store,
            queue_run_store=queue_run_store,
            executor=CodexCloudRunExecutor(
                store=store,
                run_store=run_store,
                run_chat_service=RunChatService(
                    session_factory=tenant_session_factory,
                    runtime_store=SqlModelAgentRuntimeStore(
                        tenant_session_factory,
                        chat_notifications_enabled=push_settings.enabled,
                        chat_notification_max_attempts=push_settings.max_attempts,
                    ),
                    run_store=run_store,
                ),
                sandbox=sandbox,
                settings=resolved_settings,
            ),
            claimable_kinds=(AgentRunKind.CODEX_CLOUD_TASK,),
            push_delivery_worker=create_push_delivery_worker(
                session_factory=base_session_factory,
                worker_id=f"{resolved_settings.worker_id}-push",
                settings=push_settings,
                firebase_settings=FirebaseAuthSettings(),
            ),
            settings=run_worker_settings,
        ),
        auth_worker=CodexAuthSessionWorker(
            store=store,
            sandbox=sandbox,
            settings=resolved_settings,
        ),
        settings=resolved_settings,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    try:
        await worker.run_forever()
    finally:
        await engine.dispose()


def main() -> None:
    initialize_sentry("codex-worker")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker_from_settings())


if __name__ == "__main__":
    main()

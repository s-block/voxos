import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass, field
from datetime import timedelta

import httpx

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_memory.services import AgentMemoryService
from voxos.agent_runtime.history import TenantHistoryService
from voxos.agent_runtime.improvements import AgentImprovementService
from voxos.agent_runtime.learning_model import TextLearningModel
from voxos.agent_runtime.reflection import ReflectionService
from voxos.agent_runtime.run_execution import (
    TEXT_AGENT_WORKER_RUN_KINDS,
    AgentRunExecutor,
    RecordedAgentRunExecutor,
    TextAgentRunExecutor,
    failure_category_for_exception,
)
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_settings import AgentRunWorkerSettings
from voxos.agent_runtime.run_store import (
    AgentRunCancelledError,
    SqlModelAgentRunStore,
)
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_skills.services import AgentSkillService
from voxos.api.chats import build_text_chat_service
from voxos.api.settings import ApiSettings
from voxos.attachments.service import AttachmentDeletionWorker, AttachmentService
from voxos.attachments.settings import AttachmentSettings
from voxos.attachments.storage import attachment_object_store
from voxos.auth.settings import FirebaseAuthSettings
from voxos.credentials.services import CredentialsServiceFactory
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.models import utc_now
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import (
    agent_worker_session_factory,
    attachment_worker_session_factory,
    context_tenant_session_factory,
    tenant_user_context,
)
from voxos.integrations.reply_dispatch import CommunicationReplyDispatcher
from voxos.notifications.factory import create_push_delivery_worker
from voxos.notifications.settings import PushNotificationSettings
from voxos.notifications.worker import PushNotificationDeliveryWorker
from voxos.observability.sentry import initialize_sentry
from voxos.security.encryption.factory import create_secret_encryption_service
from voxos.security.encryption.settings import EncryptionSettings
from voxos.security.outbound_endpoints import OutboundEndpointPolicy
from voxos.text.providers import OpenAICompatibleTextProvider
from voxos.voice.settings import VoiceAgentSettings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentRunWorker:
    run_store: SqlModelAgentRunStore
    executor: AgentRunExecutor
    settings: AgentRunWorkerSettings
    claimable_kinds: tuple[AgentRunKind, ...]
    queue_run_store: SqlModelAgentRunStore | None = None
    attachment_deletion_worker: AttachmentDeletionWorker | None = None
    push_delivery_worker: PushNotificationDeliveryWorker | None = None
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def run_forever(self) -> None:
        await self._queue_store.reconcile_stale_leases(
            queues=self.settings.queues,
            kinds=self.claimable_kinds,
            retry_delay_seconds=self.settings.retry_delay_seconds,
        )
        while not self._stopping.is_set():
            await self.run_once()
            await asyncio.sleep(self.settings.poll_interval_seconds)
        if self._tasks:
            await asyncio.wait(
                self._tasks,
                timeout=self.settings.shutdown_grace_seconds,
            )

    async def drain(self) -> int:
        """Process all currently runnable work, including newly spawned children."""
        await self._queue_store.reconcile_stale_leases(
            queues=self.settings.queues,
            kinds=self.claimable_kinds,
            retry_delay_seconds=self.settings.retry_delay_seconds,
        )
        processed = 0
        while not self._stopping.is_set():
            claimed = await self.run_once()
            processed += claimed
            if self._tasks:
                await self.wait_for_idle()
                continue
            if claimed == 0:
                break
        return processed

    async def run_once(self) -> int:
        if self.attachment_deletion_worker is not None:
            try:
                await self.attachment_deletion_worker.run_if_due()
            except Exception:
                logger.exception("Attachment deletion maintenance failed")
        await self._collect_finished_tasks()
        capacity = self.settings.max_concurrency - len(self._tasks)
        if capacity <= 0:
            return 0
        batch_size = min(capacity, self.settings.claim_batch_size)
        runs = await self._queue_store.claim_runs(
            worker_id=self.settings.worker_id,
            queues=self.settings.queues,
            kinds=self.claimable_kinds,
            batch_size=batch_size,
            lease_seconds=self.settings.lease_timeout_seconds,
        )
        for run in runs:
            task = asyncio.create_task(self._execute_run(run.id, run.user_id))
            self._tasks.add(task)
        return len(runs)

    def stop(self) -> None:
        self._stopping.set()

    @property
    def _queue_store(self) -> SqlModelAgentRunStore:
        return self.queue_run_store or self.run_store

    async def drain_finished_tasks(self) -> None:
        await self._collect_finished_tasks()

    async def wait_for_idle(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*self._tasks)
        await self._collect_finished_tasks()

    async def _execute_run(self, run_id: str, user_id: str) -> None:
        with tenant_user_context(user_id):
            heartbeat_stop = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(
                    run_id=run_id,
                    user_id=user_id,
                    stop_event=heartbeat_stop,
                ),
            )
            try:
                run = await self.run_store.get_run(user_id=user_id, run_id=run_id)
                if run is None:
                    return
                await self.run_store.raise_if_cancel_requested(
                    run_id=run_id,
                    user_id=user_id,
                )
                result = await self.executor.execute(run)
                await self.run_store.raise_if_cancel_requested(
                    run_id=run_id,
                    user_id=user_id,
                )
                if not result.finalized:
                    await self.run_store.complete_run(
                        run_id=run_id,
                        user_id=user_id,
                        result_json=result.result_json,
                    )
            except AgentRunCancelledError:
                await self.run_store.mark_cancelled(
                    run_id=run_id,
                    user_id=user_id,
                    reason="cancel_requested",
                )
            except Exception as exc:
                logger.exception(
                    "Agent run failed",
                    extra={"run_id": run_id, "user_id": user_id},
                )
                await self.run_store.fail_run(
                    run_id=run_id,
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    failure_category=failure_category_for_exception(exc),
                    retry=True,
                    retry_delay_seconds=self.settings.retry_delay_seconds,
                )
            finally:
                heartbeat_stop.set()
                await heartbeat
                if self.push_delivery_worker is not None:
                    try:
                        await self.push_delivery_worker.run_once()
                    except Exception:
                        logger.exception("Push notification delivery failed")

    async def _heartbeat_loop(
        self,
        *,
        run_id: str,
        user_id: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            await self.run_store.heartbeat_run(
                run_id=run_id,
                user_id=user_id,
                lease_owner=self.settings.worker_id,
                lease_expires_at=utc_now() + timedelta(seconds=self.settings.lease_timeout_seconds),
            )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.settings.heartbeat_interval_seconds,
                )
            except TimeoutError:
                continue

    async def _collect_finished_tasks(self) -> None:
        finished = {task for task in self._tasks if task.done()}
        self._tasks.difference_update(finished)
        for task in finished:
            task.result()


async def run_worker_from_settings(
    settings: AgentRunWorkerSettings | None = None,
    *,
    drain: bool = False,
) -> int | None:
    initialize_sentry("agent-run-worker")
    resolved_settings = settings or AgentRunWorkerSettings()
    database_engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(database_engine)
    tenant_session_factory = context_tenant_session_factory(session_factory)
    queue_session_factory = agent_worker_session_factory(session_factory)
    queue_run_store = SqlModelAgentRunStore(session_factory=queue_session_factory)
    push_settings = PushNotificationSettings()
    run_store = SqlModelAgentRunStore(
        session_factory=tenant_session_factory,
        terminal_notifications_enabled=push_settings.enabled,
        terminal_notification_max_attempts=push_settings.max_attempts,
    )
    identity_service = AgentIdentityService(session_factory=tenant_session_factory)
    http_client = httpx.AsyncClient()
    api_settings = ApiSettings()
    attachment_settings = AttachmentSettings()
    object_store = attachment_object_store(attachment_settings)
    attachment_service = AttachmentService(
        session_factory=tenant_session_factory,
        object_store=object_store,
        settings=attachment_settings,
        chat_notifications_enabled=push_settings.enabled,
        chat_notification_max_attempts=push_settings.max_attempts,
    )
    secret_vault = CredentialsServiceFactory(
        create_secret_encryption_service(EncryptionSettings()),
    ).user_secret_vault()
    fallback_executor = RecordedAgentRunExecutor(
        run_store=run_store,
        identity_service=identity_service,
    )
    text_service = build_text_chat_service(
        session_factory=tenant_session_factory,
        secret_vault=secret_vault,
        http_client=http_client,
        public_base_url=api_settings.api_public_base_url,
        voice_settings=VoiceAgentSettings(),
        attachment_service=attachment_service,
        push_settings=push_settings,
        text_provider_client=OpenAICompatibleTextProvider(
            http_client=http_client,
            outbound_endpoint_policy=OutboundEndpointPolicy.from_settings(),
        ),
    )
    learning_model = TextLearningModel(tenant_session_factory, secret_vault, text_service.provider_client)
    improvement_service = AgentImprovementService(
        tenant_session_factory,
        TenantHistoryService(tenant_session_factory, SqlModelToolAuthorizationService(tenant_session_factory)),
        AgentMemoryService(tenant_session_factory),
        AgentSkillService(tenant_session_factory),
        learning_model,
    )
    worker = AgentRunWorker(
        run_store=run_store,
        queue_run_store=queue_run_store,
        attachment_deletion_worker=AttachmentDeletionWorker(
            session_factory=attachment_worker_session_factory(session_factory),
            object_store=object_store,
            settings=attachment_settings,
        ),
        push_delivery_worker=create_push_delivery_worker(
            session_factory=session_factory,
            worker_id=f"{resolved_settings.worker_id}-push",
            settings=push_settings,
            firebase_settings=FirebaseAuthSettings(),
        ),
        executor=TextAgentRunExecutor(
            text_service=text_service,
            run_store=run_store,
            run_chat_service=text_service.run_chat_service,
            fallback_executor=fallback_executor,
            reflection_executor=ReflectionService(improvement_service, learning_model, run_store),
            reply_dispatcher=CommunicationReplyDispatcher(
                session_factory=tenant_session_factory,
                secret_vault=secret_vault,
                public_base_url=api_settings.api_public_base_url,
            ),
        ),
        settings=resolved_settings,
        claimable_kinds=TEXT_AGENT_WORKER_RUN_KINDS,
    )

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    try:
        if drain:
            return await worker.drain()
        await worker.run_forever()
        return None
    finally:
        await http_client.aclose()
        await database_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the durable agent queue worker")
    parser.add_argument(
        "--drain",
        action="store_true",
        help="Process runnable queue entries and exit when the queue is empty",
    )
    arguments = parser.parse_args()
    initialize_sentry("agent-run-worker")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker_from_settings(drain=arguments.drain))


if __name__ == "__main__":
    main()

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass, field

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.run_execution import TEXT_AGENT_WORKER_RUN_KINDS
from voxos.agent_runtime.run_settings import AgentScheduleWorkerSettings
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.schedule_store import AgentScheduleStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.auth.settings import FirebaseAuthSettings
from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.session import AsyncSessionFactory
from voxos.db.settings import DatabaseSettings
from voxos.db.tenant import (
    agent_worker_session_factory,
    context_tenant_session_factory,
)
from voxos.notifications.factory import create_push_delivery_worker
from voxos.notifications.settings import PushNotificationSettings
from voxos.notifications.worker import PushNotificationDeliveryWorker
from voxos.observability.sentry import initialize_sentry
from voxos.text.run_chat_service import RunChatService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AgentScheduleCycleResult:
    enqueued_runs: int
    worker_work_available: bool


@dataclass(slots=True)
class AgentScheduleWorker:
    schedule_store: AgentScheduleStore
    run_store: SqlModelAgentRunStore
    settings: AgentScheduleWorkerSettings
    push_delivery_worker: PushNotificationDeliveryWorker | None = None
    _stopping: asyncio.Event = field(default_factory=asyncio.Event)

    async def run_forever(self) -> None:
        while not self._stopping.is_set():
            await self.run_once()
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def run_once(self) -> int:
        schedules = await self.schedule_store.enqueue_due_schedules(
            limit=self.settings.batch_size,
        )
        if schedules:
            logger.info("Enqueued due schedules", extra={"count": len(schedules)})
        if self.push_delivery_worker is not None:
            try:
                await self.push_delivery_worker.run_once()
            except Exception:
                logger.exception("Scheduled push notification retry failed")
        return len(schedules)

    async def run_cycle(self) -> AgentScheduleCycleResult:
        enqueued_runs = await self.run_once()
        worker_work_available = await self.run_store.has_worker_work(
            queues=self.settings.queues,
            kinds=TEXT_AGENT_WORKER_RUN_KINDS,
        )
        return AgentScheduleCycleResult(
            enqueued_runs=enqueued_runs,
            worker_work_available=worker_work_available,
        )

    def stop(self) -> None:
        self._stopping.set()


def build_schedule_worker(
    *,
    settings: AgentScheduleWorkerSettings,
    session_factory: AsyncSessionFactory,
) -> AgentScheduleWorker:
    queue_session_factory = agent_worker_session_factory(session_factory)
    tenant_session_factory = context_tenant_session_factory(session_factory)
    push_settings = PushNotificationSettings()
    run_store = SqlModelAgentRunStore(session_factory=queue_session_factory)
    tenant_run_store = SqlModelAgentRunStore(session_factory=tenant_session_factory)
    return AgentScheduleWorker(
        schedule_store=AgentScheduleStore(
            session_factory=queue_session_factory,
            run_store=run_store,
            run_chat_service=RunChatService(
                session_factory=tenant_session_factory,
                runtime_store=SqlModelAgentRuntimeStore(tenant_session_factory),
                run_store=tenant_run_store,
            ),
            identity_service=AgentIdentityService(session_factory=queue_session_factory),
            tenant_session_factory=tenant_session_factory,
            tenant_run_store=tenant_run_store,
            policy_session_factory=tenant_session_factory,
        ),
        run_store=run_store,
        settings=settings,
        push_delivery_worker=create_push_delivery_worker(
            session_factory=session_factory,
            worker_id="scheduler-push",
            settings=push_settings,
            firebase_settings=FirebaseAuthSettings(),
        ),
    )


async def run_scheduler_from_settings(
    settings: AgentScheduleWorkerSettings | None = None,
    *,
    once: bool = False,
) -> int | None:
    initialize_sentry("scheduler")
    resolved_settings = settings or AgentScheduleWorkerSettings()
    database_engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(database_engine)
    worker = build_schedule_worker(
        settings=resolved_settings,
        session_factory=session_factory,
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, worker.stop)
    try:
        if once:
            return await worker.run_once()
        await worker.run_forever()
        return None
    finally:
        await database_engine.dispose()


async def run_scheduler_cycle_from_settings(
    settings: AgentScheduleWorkerSettings | None = None,
) -> AgentScheduleCycleResult:
    resolved_settings = settings or AgentScheduleWorkerSettings()
    database_engine = create_database_engine(DatabaseSettings())
    session_factory = create_session_factory(database_engine)
    worker = build_schedule_worker(
        settings=resolved_settings,
        session_factory=session_factory,
    )
    try:
        return await worker.run_cycle()
    finally:
        await database_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the durable schedule worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Enqueue due schedules once and exit",
    )
    arguments = parser.parse_args()
    initialize_sentry("scheduler")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_scheduler_from_settings(once=arguments.once))


if __name__ == "__main__":
    main()

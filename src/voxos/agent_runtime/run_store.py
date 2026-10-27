from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.effective_tool_policy import effective_tool_policy_id
from voxos.agent_runtime.improvements import withdraw_session_learning
from voxos.agent_runtime.learning_lock import lock_learning_settings
from voxos.agent_runtime.learning_triggers import enqueue_error_reflection
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.records import (
    AgentRunEventRecord,
    AgentRunRecord,
    AgentScheduleRecord,
    AgentSessionRecord,
    AgentWebhookRecord,
)
from voxos.agent_runtime.run_models import (
    AgentRunCancelledError,
    AgentRunDeliveryState,
    AgentRunDetail,
    AgentRunEvent,
    AgentRunEventKind,
    AgentRunFailureCategory,
    AgentRunKind,
    AgentRunStatus,
    AgentRunSummary,
)
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.notifications.outbox import enqueue_terminal_run_notification

DEFAULT_RUN_QUEUE = "agent-default"
ACTIVE_RUN_STATUSES = (
    AgentRunStatus.QUEUED,
    AgentRunStatus.RUNNING,
    AgentRunStatus.CANCELLING,
)
TERMINAL_RUN_STATUSES = (
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
    AgentRunStatus.LOST,
)
SENSITIVE_PAYLOAD_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "encrypted",
    "key",
    "nonce",
    "password",
    "private",
    "secret",
    "token",
)


class AgentRunTransitionError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class AgentRunRelationshipError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class AgentRunStore(Protocol):
    async def enqueue_run(
        self,
        *,
        user_id: str,
        agent_id: str,
        kind: AgentRunKind,
        payload_json: JsonObject,
        created_by_user_id: str,
        queue: str = DEFAULT_RUN_QUEUE,
        priority: int = 0,
        max_attempts: int = 3,
        session_id: str | None = None,
        chat_id: str | None = None,
        parent_run_id: str | None = None,
        parent_session_id: str | None = None,
        root_run_id: str | None = None,
        schedule_id: str | None = None,
        webhook_id: str | None = None,
        browser_account_id: str | None = None,
        task_plan_id: str | None = None,
        effective_tool_policy_json: JsonObject | None = None,
        idempotency_key: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> AgentRunRecord:
        """Create a queued durable run record."""

    async def bind_run_to_chat(
        self,
        *,
        run_id: str,
        user_id: str,
        chat_id: str,
    ) -> AgentRunRecord | None:
        """Bind an unscoped durable run to a tenant-owned chat."""


@dataclass(frozen=True, slots=True)
class SqlModelAgentRunStore:
    session_factory: AsyncSessionFactory
    terminal_notifications_enabled: bool = False
    terminal_notification_max_attempts: int = 5

    async def enqueue_run(
        self,
        *,
        user_id: str,
        agent_id: str,
        kind: AgentRunKind,
        payload_json: JsonObject,
        created_by_user_id: str,
        queue: str = DEFAULT_RUN_QUEUE,
        priority: int = 0,
        max_attempts: int = 3,
        session_id: str | None = None,
        chat_id: str | None = None,
        parent_run_id: str | None = None,
        parent_session_id: str | None = None,
        root_run_id: str | None = None,
        schedule_id: str | None = None,
        webhook_id: str | None = None,
        browser_account_id: str | None = None,
        task_plan_id: str | None = None,
        effective_tool_policy_json: JsonObject | None = None,
        idempotency_key: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> AgentRunRecord:
        try:
            async with self.session_factory() as session:
                record = await self.enqueue_run_in_session(
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    kind=kind,
                    payload_json=payload_json,
                    created_by_user_id=created_by_user_id,
                    queue=queue,
                    priority=priority,
                    max_attempts=max_attempts,
                    session_id=session_id,
                    chat_id=chat_id,
                    parent_run_id=parent_run_id,
                    parent_session_id=parent_session_id,
                    root_run_id=root_run_id,
                    schedule_id=schedule_id,
                    webhook_id=webhook_id,
                    browser_account_id=browser_account_id,
                    task_plan_id=task_plan_id,
                    effective_tool_policy_json=effective_tool_policy_json,
                    idempotency_key=idempotency_key,
                    next_attempt_at=next_attempt_at,
                )
                await session.commit()
                return record
        except IntegrityError:
            if idempotency_key is None:
                raise
            async with self.session_factory() as session:
                existing = await self._run_by_idempotency_key(
                    session=session,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                )
                if existing is None:
                    raise
                return existing

    async def enqueue_run_in_session(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        kind: AgentRunKind,
        payload_json: JsonObject,
        created_by_user_id: str,
        queue: str = DEFAULT_RUN_QUEUE,
        priority: int = 0,
        max_attempts: int = 3,
        session_id: str | None = None,
        chat_id: str | None = None,
        parent_run_id: str | None = None,
        parent_session_id: str | None = None,
        root_run_id: str | None = None,
        schedule_id: str | None = None,
        webhook_id: str | None = None,
        browser_account_id: str | None = None,
        task_plan_id: str | None = None,
        effective_tool_policy_json: JsonObject | None = None,
        idempotency_key: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> AgentRunRecord:
        """Add a queued run to an existing transaction without committing it."""
        now = utc_now()
        await self._validate_run_relationships(
            session=session,
            user_id=user_id,
            agent_id=agent_id,
            created_by_user_id=created_by_user_id,
            session_id=session_id,
            chat_id=chat_id,
            parent_run_id=parent_run_id,
            parent_session_id=parent_session_id,
            root_run_id=root_run_id,
            schedule_id=schedule_id,
            webhook_id=webhook_id,
        )
        if idempotency_key is not None:
            existing = await self._run_by_idempotency_key(
                session=session,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing

        record = AgentRunRecord(
            user_id=user_id,
            agent_id=agent_id,
            kind=kind.value,
            status=AgentRunStatus.QUEUED.value,
            queue=queue,
            priority=priority,
            payload_json=safe_payload(payload_json),
            effective_tool_policy_json=effective_tool_policy_json or {},
            created_by_user_id=created_by_user_id,
            max_attempts=max_attempts,
            session_id=session_id,
            chat_id=chat_id,
            parent_run_id=parent_run_id,
            parent_session_id=parent_session_id,
            root_run_id=root_run_id,
            schedule_id=schedule_id,
            webhook_id=webhook_id,
            browser_account_id=browser_account_id,
            task_plan_id=task_plan_id,
            idempotency_key=idempotency_key,
            next_attempt_at=next_attempt_at or now,
            created_at=now,
            updated_at=now,
        )
        if record.root_run_id is None:
            record.root_run_id = record.id
        session.add(record)
        await session.flush()
        self._add_event(
            session=session,
            run=record,
            kind=AgentRunEventKind.RUN_ENQUEUED,
            payload_json={"queue": queue, "kind": kind.value},
            created_at=now,
        )
        return record

    def start_run_in_session(
        self,
        *,
        session: AsyncSession,
        record: AgentRunRecord,
        user_id: str,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AgentRunRecord:
        """Start a newly enqueued run inside the caller's transaction."""

        if record.user_id != user_id or record.deleted_at is not None:
            message = "Run must belong to the authenticated user"
            raise AgentRunRelationshipError(message)
        self._mark_running(
            session=session,
            record=record,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            now=utc_now(),
        )
        return record

    async def get_run(
        self,
        *,
        user_id: str,
        run_id: str,
        include_deleted: bool = False,
    ) -> AgentRunRecord | None:
        async with self.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None or record.user_id != user_id:
                return None
            if not include_deleted and record.deleted_at is not None:
                return None
            return record

    async def bind_run_to_chat(
        self,
        *,
        run_id: str,
        user_id: str,
        chat_id: str,
    ) -> AgentRunRecord | None:
        async with self.session_factory() as session:
            record = await self._scoped_run(
                session=session,
                user_id=user_id,
                run_id=run_id,
            )
            if record is None:
                return None
            if record.session_id not in {None, chat_id} or record.chat_id not in {
                None,
                chat_id,
            }:
                message = "Run is already bound to a different chat"
                raise AgentRunRelationshipError(message)
            await self._validate_session_link(
                session=session,
                user_id=user_id,
                agent_id=record.agent_id,
                session_id=chat_id,
                link_name="Chat",
            )
            record.session_id = chat_id
            record.chat_id = chat_id
            record.updated_at = utc_now()
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_run_detail(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AgentRunDetail | None:
        async with self.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None or record.user_id != user_id or record.deleted_at is not None:
                return None
            child_count = await self._child_count(session=session, run_id=record.id)
            return run_detail_from_record(record, child_count=child_count)

    async def list_runs(
        self,
        *,
        user_id: str,
        statuses: Sequence[AgentRunStatus] = (),
        kinds: Sequence[AgentRunKind] = (),
        agent_id: str | None = None,
        schedule_id: str | None = None,
        webhook_id: str | None = None,
        limit: int = 100,
    ) -> tuple[AgentRunSummary, ...]:
        statement = select(AgentRunRecord).where(
            col(AgentRunRecord.user_id) == user_id,
            col(AgentRunRecord.deleted_at).is_(None),
        )
        if statuses:
            statement = statement.where(
                col(AgentRunRecord.status).in_([status.value for status in statuses]),
            )
        if kinds:
            statement = statement.where(
                col(AgentRunRecord.kind).in_([kind.value for kind in kinds]),
            )
        if agent_id is not None:
            statement = statement.where(col(AgentRunRecord.agent_id) == agent_id)
        if schedule_id is not None:
            statement = statement.where(col(AgentRunRecord.schedule_id) == schedule_id)
        if webhook_id is not None:
            statement = statement.where(col(AgentRunRecord.webhook_id) == webhook_id)
        statement = statement.order_by(col(AgentRunRecord.created_at).desc()).limit(limit)

        async with self.session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
            child_counts = await self._child_counts(
                session=session,
                run_ids=tuple(record.id for record in records),
            )
            return tuple(
                run_summary_from_record(
                    record,
                    child_count=child_counts.get(record.id, 0),
                )
                for record in records
            )

    async def list_subagent_runs_for_parent_session(
        self,
        *,
        user_id: str,
        parent_session_id: str,
        limit: int = 20,
    ) -> tuple[AgentRunRecord, ...]:
        statement = (
            select(AgentRunRecord)
            .where(
                col(AgentRunRecord.user_id) == user_id,
                col(AgentRunRecord.parent_session_id) == parent_session_id,
                col(AgentRunRecord.kind) == AgentRunKind.SUBAGENT.value,
                col(AgentRunRecord.deleted_at).is_(None),
            )
            .order_by(col(AgentRunRecord.created_at).desc())
            .limit(limit)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(result.all())

    async def list_active_runs(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> tuple[AgentRunSummary, ...]:
        return await self.list_runs(
            user_id=user_id,
            statuses=ACTIVE_RUN_STATUSES,
            limit=limit,
        )

    async def list_events(
        self,
        *,
        user_id: str,
        run_id: str | None = None,
        since_event_id: str | None = None,
        limit: int = 200,
    ) -> tuple[AgentRunEvent, ...]:
        async with self.session_factory() as session:
            cursor_created_at: datetime | None = None
            if since_event_id is not None:
                cursor = await session.get(AgentRunEventRecord, since_event_id)
                if cursor is not None and cursor.user_id == user_id:
                    cursor_created_at = cursor.created_at

            statement = select(AgentRunEventRecord).where(
                col(AgentRunEventRecord.user_id) == user_id,
            )
            if run_id is not None:
                statement = statement.where(col(AgentRunEventRecord.run_id) == run_id)
            if cursor_created_at is not None:
                statement = statement.where(
                    or_(
                        col(AgentRunEventRecord.created_at) > cursor_created_at,
                        and_(
                            col(AgentRunEventRecord.created_at) == cursor_created_at,
                            col(AgentRunEventRecord.id) > since_event_id,
                        ),
                    )
                )
            statement = statement.order_by(
                col(AgentRunEventRecord.created_at).asc(), col(AgentRunEventRecord.id).asc()
            ).limit(limit)
            result = await session.exec(statement)
            return tuple(run_event_from_record(record) for record in result.all())

    async def append_event(
        self,
        *,
        run_id: str,
        user_id: str,
        kind: AgentRunEventKind,
        payload_json: JsonObject,
        created_at: datetime | None = None,
    ) -> AgentRunEventRecord | None:
        async with self.session_factory() as session:
            record = await session.get(AgentRunRecord, run_id)
            if record is None or record.user_id != user_id or record.deleted_at is not None:
                return None
            event = self._add_event(
                session=session,
                run=record,
                kind=kind,
                payload_json=payload_json,
                created_at=created_at or utc_now(),
            )
            await session.commit()
            return event

    async def start_run(
        self,
        *,
        run_id: str,
        user_id: str,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            self._ensure_status(record, allowed=(AgentRunStatus.QUEUED,))
            self._mark_running(
                session=session,
                record=record,
                lease_owner=lease_owner,
                lease_expires_at=lease_expires_at,
                now=now,
            )
            await session.commit()
            return record

    async def claim_runs(
        self,
        *,
        worker_id: str,
        queues: Sequence[str],
        kinds: Sequence[AgentRunKind] = (),
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[AgentRunRecord, ...]:
        if batch_size <= 0 or not queues:
            return ()
        now = utc_now()
        statement = (
            select(AgentRunRecord)
            .where(
                col(AgentRunRecord.status) == AgentRunStatus.QUEUED.value,
                col(AgentRunRecord.deleted_at).is_(None),
                col(AgentRunRecord.queue).in_(tuple(queues)),
                or_(
                    col(AgentRunRecord.next_attempt_at).is_(None),
                    col(AgentRunRecord.next_attempt_at) <= now,
                ),
            )
            .order_by(
                col(AgentRunRecord.priority).desc(),
                col(AgentRunRecord.created_at).asc(),
            )
            .limit(batch_size)
        )
        if kinds:
            statement = statement.where(
                col(AgentRunRecord.kind).in_(tuple(kind.value for kind in kinds)),
            )

        async with self.session_factory() as session:
            bind = session.get_bind()
            if bind.dialect.name != "sqlite":
                statement = statement.with_for_update(skip_locked=True)
            result = await session.exec(statement)
            records = tuple(result.all())
            lease_expires_at = now + timedelta(seconds=lease_seconds)
            for record in records:
                self._mark_running(
                    session=session,
                    record=record,
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    now=now,
                )
            await session.commit()
            for record in records:
                await session.refresh(record)
            return records

    async def has_worker_work(
        self,
        *,
        queues: Sequence[str],
        kinds: Sequence[AgentRunKind] = (),
        now: datetime | None = None,
    ) -> bool:
        """Return whether a worker can claim or recover work now."""
        if not queues:
            return False
        current_time = now or utc_now()
        statement = (
            select(AgentRunRecord.id)
            .where(
                col(AgentRunRecord.deleted_at).is_(None),
                col(AgentRunRecord.queue).in_(tuple(queues)),
                or_(
                    and_(
                        col(AgentRunRecord.status) == AgentRunStatus.QUEUED.value,
                        or_(
                            col(AgentRunRecord.next_attempt_at).is_(None),
                            col(AgentRunRecord.next_attempt_at) <= current_time,
                        ),
                    ),
                    and_(
                        col(AgentRunRecord.status).in_(
                            [
                                AgentRunStatus.RUNNING.value,
                                AgentRunStatus.CANCELLING.value,
                            ],
                        ),
                        col(AgentRunRecord.lease_expires_at).is_not(None),
                        col(AgentRunRecord.lease_expires_at) < current_time,
                    ),
                ),
            )
            .limit(1)
        )
        if kinds:
            statement = statement.where(
                col(AgentRunRecord.kind).in_(tuple(kind.value for kind in kinds)),
            )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return result.first() is not None

    async def heartbeat_run(
        self,
        *,
        run_id: str,
        user_id: str,
        lease_owner: str,
        lease_expires_at: datetime,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None or record.lease_owner != lease_owner:
                return None
            if AgentRunStatus(record.status) not in (
                AgentRunStatus.RUNNING,
                AgentRunStatus.CANCELLING,
            ):
                return None
            record.heartbeat_at = now
            record.lease_expires_at = lease_expires_at
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def complete_run(
        self,
        *,
        run_id: str,
        user_id: str,
        result_json: JsonObject | None = None,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            self._ensure_status(record, allowed=(AgentRunStatus.RUNNING,))
            record.status = AgentRunStatus.SUCCEEDED.value
            record.result_json = safe_payload(result_json or {})
            record.completed_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            self._add_event(
                session=session,
                run=record,
                kind=AgentRunEventKind.RUN_COMPLETED,
                payload_json=record.result_json,
                created_at=now,
            )
            if self.terminal_notifications_enabled:
                await enqueue_terminal_run_notification(
                    session=session,
                    run=record,
                    status=AgentRunStatus.SUCCEEDED,
                    created_at=now,
                    max_attempts=self.terminal_notification_max_attempts,
                )
            await session.commit()
            await session.refresh(record)
            return record

    async def fail_run(
        self,
        *,
        run_id: str,
        user_id: str,
        error_type: str,
        error_message: str | None,
        failure_category: AgentRunFailureCategory = AgentRunFailureCategory.UNKNOWN,
        retry: bool = False,
        retry_delay_seconds: int = 30,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            self._ensure_status(
                record,
                allowed=(
                    AgentRunStatus.RUNNING,
                    AgentRunStatus.CANCELLING,
                    AgentRunStatus.QUEUED,
                ),
            )
            record.error_type = truncate(error_type, 256)
            record.error_message = truncate(error_message, 4096)
            record.lease_owner = None
            record.lease_expires_at = None
            if retry and record.attempt < record.max_attempts:
                record.status = AgentRunStatus.QUEUED.value
                record.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
                kind = AgentRunEventKind.RUN_RETRY_SCHEDULED
                payload = {
                    "attempt": record.attempt,
                    "max_attempts": record.max_attempts,
                    "next_attempt_at": record.next_attempt_at.isoformat(),
                    "error_type": record.error_type,
                    "failure_category": failure_category.value,
                }
            else:
                record.status = AgentRunStatus.FAILED.value
                record.completed_at = now
                kind = AgentRunEventKind.RUN_FAILED
                payload = {
                    "error_type": record.error_type,
                    "error_message": record.error_message,
                    "failure_category": failure_category.value,
                }
            record.updated_at = now
            self._add_event(
                session=session,
                run=record,
                kind=kind,
                payload_json=payload,
                created_at=now,
            )
            if self.terminal_notifications_enabled and record.status == AgentRunStatus.FAILED.value:
                await enqueue_terminal_run_notification(
                    session=session,
                    run=record,
                    status=AgentRunStatus.FAILED,
                    created_at=now,
                    max_attempts=self.terminal_notification_max_attempts,
                )
            if (
                record.status == AgentRunStatus.FAILED.value
                and record.kind == AgentRunKind.TEXT_CHAT_TURN.value
                and record.parent_run_id is None
                and record.session_id is not None
            ):
                origin = await session.get(AgentSessionRecord, record.session_id)
                if origin is not None and origin.user_id == user_id and origin.channel == "text":
                    await enqueue_error_reflection(
                        session, session=origin, policy_json=record.effective_tool_policy_json
                    )
            await session.commit()
            await session.refresh(record)
            return record

    async def request_cancel(
        self,
        *,
        run_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            status = AgentRunStatus(record.status)
            if status in TERMINAL_RUN_STATUSES:
                return record
            record.cancel_requested_at = record.cancel_requested_at or now
            record.cancel_reason = truncate(reason, 512)
            if status == AgentRunStatus.QUEUED:
                record.status = AgentRunStatus.CANCELLED.value
                record.completed_at = now
                event_kind = AgentRunEventKind.RUN_CANCELLED
            else:
                record.status = AgentRunStatus.CANCELLING.value
                event_kind = AgentRunEventKind.RUN_CANCEL_REQUESTED
            record.updated_at = now
            self._add_event(
                session=session,
                run=record,
                kind=event_kind,
                payload_json={"reason": record.cancel_reason},
                created_at=now,
            )
            await self._request_child_cancellation(
                session=session,
                parent_run_id=record.id,
                user_id=user_id,
                reason=reason,
                now=now,
            )
            await session.commit()
            await session.refresh(record)
            return record

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        user_id: str,
        reason: str | None = None,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            self._ensure_status(
                record,
                allowed=(
                    AgentRunStatus.RUNNING,
                    AgentRunStatus.CANCELLING,
                    AgentRunStatus.QUEUED,
                ),
            )
            record.status = AgentRunStatus.CANCELLED.value
            record.cancel_requested_at = record.cancel_requested_at or now
            record.cancel_reason = truncate(reason or record.cancel_reason, 512)
            record.completed_at = now
            record.updated_at = now
            record.lease_owner = None
            record.lease_expires_at = None
            self._add_event(
                session=session,
                run=record,
                kind=AgentRunEventKind.RUN_CANCELLED,
                payload_json={"reason": record.cancel_reason},
                created_at=now,
            )
            await session.commit()
            await session.refresh(record)
            return record

    async def retry_run(
        self,
        *,
        run_id: str,
        user_id: str,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            self._ensure_status(
                record,
                allowed=(AgentRunStatus.FAILED, AgentRunStatus.LOST),
            )
            record.status = AgentRunStatus.QUEUED.value
            record.next_attempt_at = now
            record.completed_at = None
            record.error_type = None
            record.error_message = None
            record.cancel_requested_at = None
            record.cancel_reason = None
            record.updated_at = now
            self._add_event(
                session=session,
                run=record,
                kind=AgentRunEventKind.RUN_RETRY_SCHEDULED,
                payload_json={"manual": True},
                created_at=now,
            )
            await session.commit()
            await session.refresh(record)
            return record

    async def is_cancel_requested(self, *, run_id: str, user_id: str) -> bool:
        record = await self.get_run(user_id=user_id, run_id=run_id)
        if record is None:
            return False
        return record.cancel_requested_at is not None or record.status == AgentRunStatus.CANCELLING.value

    async def raise_if_cancel_requested(self, *, run_id: str, user_id: str) -> None:
        if await self.is_cancel_requested(run_id=run_id, user_id=user_id):
            raise AgentRunCancelledError(run_id)

    async def reconcile_stale_leases(
        self,
        *,
        queues: Sequence[str] = (),
        kinds: Sequence[AgentRunKind] = (),
        now: datetime | None = None,
        retry_delay_seconds: int = 30,
        limit: int = 100,
    ) -> tuple[AgentRunSummary, ...]:
        current_time = now or utc_now()
        statement = (
            select(AgentRunRecord)
            .where(
                col(AgentRunRecord.status).in_(
                    [AgentRunStatus.RUNNING.value, AgentRunStatus.CANCELLING.value],
                ),
                col(AgentRunRecord.lease_expires_at).is_not(None),
                col(AgentRunRecord.lease_expires_at) < current_time,
                col(AgentRunRecord.deleted_at).is_(None),
            )
            .limit(limit)
        )
        if queues:
            statement = statement.where(col(AgentRunRecord.queue).in_(tuple(queues)))
        if kinds:
            statement = statement.where(
                col(AgentRunRecord.kind).in_(tuple(kind.value for kind in kinds)),
            )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
            for record in records:
                record.lease_owner = None
                record.lease_expires_at = None
                if record.status == AgentRunStatus.CANCELLING.value:
                    record.status = AgentRunStatus.CANCELLED.value
                    record.completed_at = current_time
                    event_kind = AgentRunEventKind.RUN_CANCELLED
                    payload: JsonObject = {"reason": record.cancel_reason}
                elif record.attempt < record.max_attempts:
                    record.status = AgentRunStatus.QUEUED.value
                    record.next_attempt_at = current_time + timedelta(
                        seconds=retry_delay_seconds,
                    )
                    event_kind = AgentRunEventKind.RUN_RETRY_SCHEDULED
                    payload = {"reason": "stale_lease"}
                else:
                    record.status = AgentRunStatus.LOST.value
                    record.completed_at = current_time
                    event_kind = AgentRunEventKind.RUN_LOST
                    payload = {"reason": "stale_lease"}
                record.updated_at = current_time
                self._add_event(
                    session=session,
                    run=record,
                    kind=event_kind,
                    payload_json=payload,
                    created_at=current_time,
                )
            await session.commit()
            child_counts = await self._child_counts(
                session=session,
                run_ids=tuple(record.id for record in records),
            )
            return tuple(
                run_summary_from_record(record, child_count=child_counts.get(record.id, 0)) for record in records
            )

    async def redact_run(
        self,
        *,
        run_id: str,
        user_id: str,
    ) -> AgentRunRecord | None:
        now = utc_now()
        async with self.session_factory() as session:
            await lock_learning_settings(session, user_id)
            record = await self._scoped_run(session=session, user_id=user_id, run_id=run_id)
            if record is None:
                return None
            if record.session_id is not None:
                await withdraw_session_learning(session, user_id=user_id, session_id=record.session_id)
            record.payload_json = {"redacted": True}
            record.result_json = {"redacted": True}
            record.effective_tool_policy_json = {}
            record.error_message = None
            record.redacted_at = now
            record.updated_at = now
            events = await session.exec(
                select(AgentRunEventRecord).where(
                    col(AgentRunEventRecord.run_id) == run_id,
                    col(AgentRunEventRecord.user_id) == user_id,
                ),
            )
            for event in events.all():
                event.payload_json = {"redacted": True}
            self._add_event(
                session=session,
                run=record,
                kind=AgentRunEventKind.RUN_REDACTED,
                payload_json={},
                created_at=now,
            )
            await session.commit()
            await session.refresh(record)
            return record

    async def redact_runs_for_session(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
    ) -> None:
        statement = select(AgentRunRecord).where(
            col(AgentRunRecord.session_id) == session_id,
            col(AgentRunRecord.user_id) == user_id,
            col(AgentRunRecord.agent_id) == agent_id,
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            run_ids = tuple(record.id for record in result.all())
        for run_id in run_ids:
            await self.redact_run(run_id=run_id, user_id=user_id)

    async def _run_by_idempotency_key(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        idempotency_key: str,
    ) -> AgentRunRecord | None:
        result = await session.exec(
            select(AgentRunRecord).where(
                col(AgentRunRecord.user_id) == user_id,
                col(AgentRunRecord.idempotency_key) == idempotency_key,
                col(AgentRunRecord.deleted_at).is_(None),
            ),
        )
        return result.one_or_none()

    async def _validate_run_relationships(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        created_by_user_id: str,
        session_id: str | None,
        chat_id: str | None,
        parent_run_id: str | None,
        parent_session_id: str | None,
        root_run_id: str | None,
        schedule_id: str | None,
        webhook_id: str | None,
    ) -> None:
        if created_by_user_id != user_id:
            message = "Run creator must match run user"
            raise AgentRunRelationshipError(message)
        if parent_run_id is not None:
            parent = await session.get(AgentRunRecord, parent_run_id)
            if parent is None or parent.user_id != user_id or parent.deleted_at is not None:
                message = "Parent run must belong to the run user"
                raise AgentRunRelationshipError(message)
            if parent_session_id is not None and parent.session_id != parent_session_id:
                message = "Parent session link does not match the parent run"
                raise AgentRunRelationshipError(message)
        if root_run_id is not None:
            root = await session.get(AgentRunRecord, root_run_id)
            if root is None or root.user_id != user_id or root.deleted_at is not None:
                message = "Root run must belong to the run user"
                raise AgentRunRelationshipError(message)
        if session_id is not None:
            await self._validate_session_link(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                link_name="Session",
            )
        if chat_id is not None and chat_id != session_id:
            await self._validate_session_link(
                session=session,
                user_id=user_id,
                agent_id=agent_id,
                session_id=chat_id,
                link_name="Chat",
            )
        if schedule_id is not None:
            schedule = await session.get(AgentScheduleRecord, schedule_id)
            if (
                schedule is None
                or schedule.user_id != user_id
                or schedule.agent_id != agent_id
                or schedule.deleted_at is not None
            ):
                message = "Schedule must belong to the run user and agent"
                raise AgentRunRelationshipError(message)
        if webhook_id is not None:
            webhook = await session.get(AgentWebhookRecord, webhook_id)
            if (
                webhook is None
                or webhook.user_id != user_id
                or webhook.agent_id != agent_id
                or not webhook.enabled
                or webhook.deleted_at is not None
            ):
                message = "Webhook must be active and belong to the run user and agent"
                raise AgentRunRelationshipError(message)

    async def _validate_session_link(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        session_id: str,
        link_name: str,
    ) -> None:
        session_record = await session.get(AgentSessionRecord, session_id)
        if (
            session_record is None
            or session_record.user_id != user_id
            or session_record.agent_id != agent_id
            or session_record.deleted_at is not None
        ):
            message = f"{link_name} must belong to the run user and agent"
            raise AgentRunRelationshipError(message)

    async def _scoped_run(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        run_id: str,
    ) -> AgentRunRecord | None:
        record = await session.get(AgentRunRecord, run_id)
        if record is None or record.user_id != user_id or record.deleted_at is not None:
            return None
        return record

    def _mark_running(
        self,
        *,
        session: AsyncSession,
        record: AgentRunRecord,
        lease_owner: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> None:
        self._ensure_status(record, allowed=(AgentRunStatus.QUEUED,))
        record.status = AgentRunStatus.RUNNING.value
        record.attempt += 1
        record.started_at = record.started_at or now
        record.heartbeat_at = now
        record.lease_owner = lease_owner
        record.lease_expires_at = lease_expires_at
        record.updated_at = now
        self._add_event(
            session=session,
            run=record,
            kind=AgentRunEventKind.RUN_STARTED,
            payload_json={
                "attempt": record.attempt,
                "queue": record.queue,
                "lease_owner": lease_owner,
            },
            created_at=now,
        )

    def _ensure_status(
        self,
        record: AgentRunRecord,
        *,
        allowed: tuple[AgentRunStatus, ...],
    ) -> None:
        current = AgentRunStatus(record.status)
        if current not in allowed:
            message = (
                f"Cannot transition run {record.id} from {current.value}; "
                f"allowed statuses: {', '.join(status.value for status in allowed)}"
            )
            raise AgentRunTransitionError(message)

    def _add_event(
        self,
        *,
        session: AsyncSession,
        run: AgentRunRecord,
        kind: AgentRunEventKind,
        payload_json: JsonObject,
        created_at: datetime,
    ) -> AgentRunEventRecord:
        event = AgentRunEventRecord(
            run_id=run.id,
            user_id=run.user_id,
            agent_id=run.agent_id,
            kind=kind.value,
            payload_json=safe_payload(payload_json),
            created_at=created_at,
        )
        session.add(event)
        return event

    async def _request_child_cancellation(
        self,
        *,
        session: AsyncSession,
        parent_run_id: str,
        user_id: str,
        reason: str | None,
        now: datetime,
    ) -> None:
        result = await session.exec(
            select(AgentRunRecord).where(
                col(AgentRunRecord.user_id) == user_id,
                col(AgentRunRecord.parent_run_id) == parent_run_id,
                col(AgentRunRecord.status).in_([status.value for status in ACTIVE_RUN_STATUSES]),
            ),
        )
        for child in result.all():
            child.cancel_requested_at = child.cancel_requested_at or now
            child.cancel_reason = truncate(reason, 512)
            if child.status == AgentRunStatus.QUEUED.value:
                child.status = AgentRunStatus.CANCELLED.value
                child.completed_at = now
                event_kind = AgentRunEventKind.RUN_CANCELLED
            else:
                child.status = AgentRunStatus.CANCELLING.value
                event_kind = AgentRunEventKind.RUN_CANCEL_REQUESTED
            child.updated_at = now
            self._add_event(
                session=session,
                run=child,
                kind=event_kind,
                payload_json={"reason": child.cancel_reason, "parent_run_id": parent_run_id},
                created_at=now,
            )

    async def _child_count(self, *, session: AsyncSession, run_id: str) -> int:
        counts = await self._child_counts(session=session, run_ids=(run_id,))
        return counts.get(run_id, 0)

    async def _child_counts(
        self,
        *,
        session: AsyncSession,
        run_ids: tuple[str, ...],
    ) -> dict[str, int]:
        if not run_ids:
            return {}
        parent_column = col(AgentRunRecord.parent_run_id)
        statement = (
            select(parent_column, func.count(col(AgentRunRecord.id)))
            .where(parent_column.in_(run_ids))
            .group_by(parent_column)
        )
        result = await session.exec(statement)
        return {str(parent_run_id): int(count) for parent_run_id, count in result.all() if parent_run_id is not None}


def run_summary_from_record(
    record: AgentRunRecord,
    *,
    child_count: int = 0,
) -> AgentRunSummary:
    return AgentRunSummary(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        kind=AgentRunKind(record.kind),
        status=AgentRunStatus(record.status),
        queue=record.queue,
        priority=record.priority,
        session_id=record.session_id,
        chat_id=record.chat_id,
        parent_run_id=record.parent_run_id,
        root_run_id=record.root_run_id,
        schedule_id=record.schedule_id,
        webhook_id=record.webhook_id,
        browser_account_id=record.browser_account_id,
        task_plan_id=record.task_plan_id,
        effective_tool_policy_id=effective_tool_policy_id(record.effective_tool_policy_json),
        attempt=record.attempt,
        max_attempts=record.max_attempts,
        created_at=record.created_at,
        updated_at=record.updated_at,
        next_attempt_at=record.next_attempt_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        heartbeat_at=record.heartbeat_at,
        lease_owner=record.lease_owner,
        lease_expires_at=record.lease_expires_at,
        cancel_requested_at=record.cancel_requested_at,
        cancel_reason=record.cancel_reason,
        error_type=record.error_type,
        error_message=record.error_message,
        child_run_count=child_count,
    )


def run_detail_from_record(
    record: AgentRunRecord,
    *,
    child_count: int = 0,
) -> AgentRunDetail:
    summary = run_summary_from_record(record, child_count=child_count)
    return AgentRunDetail(
        **summary.model_dump(),
        payload_json=record.payload_json,
        result_json=record.result_json,
        effective_tool_policy_json=record.effective_tool_policy_json,
    )


def run_event_from_record(record: AgentRunEventRecord) -> AgentRunEvent:
    return AgentRunEvent(
        id=record.id,
        run_id=record.run_id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        kind=AgentRunEventKind(record.kind),
        payload_json=record.payload_json,
        delivery_state=AgentRunDeliveryState(record.delivery_state),
        created_at=record.created_at,
    )


def safe_payload(payload: JsonObject) -> JsonObject:
    return {key: safe_value(key=key, value=value) for key, value in payload.items() if value is not None}


def safe_value(*, key: str, value: object) -> object:
    if is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, str):
        return truncate(value, prompt_payload_limit(key))
    if isinstance(value, int | float | bool) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [safe_value(key=key, value=item) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(child_key): safe_value(key=str(child_key), value=child_value)
            for child_key, child_value in tuple(value.items())[:100]
        }
    return str(value)


def prompt_payload_limit(key: str) -> int:
    if key in {"text", "task_prompt"}:
        return 24000
    if key == "parent_summary":
        return 12000
    return 4096


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_PAYLOAD_KEY_PARTS)


def truncate(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    if len(value) <= max_length:
        return value
    return f"{value[: max_length - 3]}..."

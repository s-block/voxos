import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import EffectiveToolPolicyService
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.records import AgentRunRecord, AgentScheduleRecord
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_store import (
    AgentRunRelationshipError,
    SqlModelAgentRunStore,
    safe_payload,
)
from voxos.agent_runtime.schedule_models import (
    AgentSchedule,
    AgentScheduleCreateRequest,
    AgentScheduleUpdateRequest,
    ScheduleCatchUpPolicy,
    ScheduleKind,
    ScheduleStatus,
)
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_routing import SqlModelToolRoutingService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_user_context
from voxos.text.run_chat_service import RunChatService, RunChatValidationError

SCHEDULE_BROWSER_ACCOUNTS_UNAVAILABLE = "Browser Account schedules are unavailable"
logger = logging.getLogger(__name__)


class ScheduleBrowserAccountAuthorizer(Protocol):
    async def require_account(
        self,
        *,
        user_id: str,
        agent_id: str,
        browser_account_id: str,
    ) -> None:
        """Require a ready account explicitly assigned to the schedule's agent."""


@dataclass(frozen=True, slots=True)
class AgentScheduleStore:
    session_factory: AsyncSessionFactory
    run_store: SqlModelAgentRunStore
    identity_service: AgentIdentityService
    run_chat_service: RunChatService
    tenant_session_factory: AsyncSessionFactory | None = None
    tenant_run_store: SqlModelAgentRunStore | None = None
    effective_tool_policy_service: EffectiveToolPolicyService | None = None
    policy_session_factory: AsyncSessionFactory | None = None
    browser_account_authorizer: ScheduleBrowserAccountAuthorizer | None = None

    async def create_schedule(
        self,
        *,
        user_id: str,
        request: AgentScheduleCreateRequest,
    ) -> AgentSchedule:
        await self.identity_service.require_agent(
            owner_user_id=user_id,
            agent_id=request.agent_id,
        )
        await self._require_browser_account(
            user_id=user_id,
            agent_id=request.agent_id,
            browser_account_id=request.browser_account_id,
        )
        now = utc_now()
        next_run_at = request.run_at if request.kind == ScheduleKind.ONE_SHOT else now
        if request.kind == ScheduleKind.INTERVAL and request.run_at is not None:
            next_run_at = request.run_at
        async with self.session_factory() as session:
            chat = await self.run_chat_service.resolve_or_create_chat_in_session(
                session=session,
                user_id=user_id,
                agent_id=request.agent_id,
                chat_id=request.chat_id,
                title=request.name,
            )
            record = AgentScheduleRecord(
                user_id=user_id,
                agent_id=request.agent_id,
                chat_id=chat.id,
                browser_account_id=request.browser_account_id,
                name=request.name,
                kind=request.kind.value,
                status=ScheduleStatus.ACTIVE.value,
                timezone=request.timezone,
                payload_json=safe_payload(request.payload_json),
                run_at=request.run_at,
                interval_seconds=request.interval_seconds,
                cron_expression=request.cron_expression,
                next_run_at=next_run_at,
                max_concurrent_runs=request.max_concurrent_runs,
                catch_up_policy=request.catch_up_policy.value,
                misfire_grace_seconds=request.misfire_grace_seconds,
                retry_policy_json=safe_payload(request.retry_policy_json),
                enabled=request.enabled,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
        return schedule_from_record(record)

    async def list_schedules(
        self,
        *,
        user_id: str,
        statuses: tuple[ScheduleStatus, ...] = (),
        limit: int = 100,
    ) -> tuple[AgentSchedule, ...]:
        statement = select(AgentScheduleRecord).where(
            col(AgentScheduleRecord.user_id) == user_id,
            col(AgentScheduleRecord.deleted_at).is_(None),
        )
        if statuses:
            statement = statement.where(
                col(AgentScheduleRecord.status).in_([status.value for status in statuses]),
            )
        statement = statement.order_by(col(AgentScheduleRecord.created_at).desc()).limit(limit)
        async with self.session_factory() as session:
            result = await session.exec(statement)
            return tuple(schedule_from_record(record) for record in result.all())

    async def get_schedule(
        self,
        *,
        user_id: str,
        schedule_id: str,
    ) -> AgentSchedule | None:
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            return schedule_from_record(record)

    async def update_schedule(
        self,
        *,
        user_id: str,
        schedule_id: str,
        request: AgentScheduleUpdateRequest,
    ) -> AgentSchedule | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            if "chat_id" in request.model_fields_set:
                chat = await self.run_chat_service.resolve_or_create_chat_in_session(
                    session=session,
                    user_id=user_id,
                    agent_id=record.agent_id,
                    chat_id=request.chat_id,
                    title=request.name or record.name,
                )
                record.chat_id = chat.id
            if "browser_account_id" in request.model_fields_set:
                await self._require_browser_account(
                    user_id=user_id,
                    agent_id=record.agent_id,
                    browser_account_id=request.browser_account_id,
                )
                record.browser_account_id = request.browser_account_id
            if request.name is not None:
                record.name = request.name
            if request.timezone is not None:
                record.timezone = request.timezone
            if request.payload_json is not None:
                record.payload_json = safe_payload(request.payload_json)
            if request.run_at is not None:
                record.run_at = request.run_at
                if record.kind == ScheduleKind.ONE_SHOT.value:
                    record.next_run_at = request.run_at
                    record.status = ScheduleStatus.ACTIVE.value
                    record.enabled = True
            if request.interval_seconds is not None:
                record.interval_seconds = request.interval_seconds
                if record.kind == ScheduleKind.INTERVAL.value:
                    record.next_run_at = now + timedelta(seconds=request.interval_seconds)
            if request.max_concurrent_runs is not None:
                record.max_concurrent_runs = request.max_concurrent_runs
            if request.catch_up_policy is not None:
                record.catch_up_policy = request.catch_up_policy.value
            if request.misfire_grace_seconds is not None:
                record.misfire_grace_seconds = request.misfire_grace_seconds
            if request.retry_policy_json is not None:
                record.retry_policy_json = safe_payload(request.retry_policy_json)
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def delete_schedule(
        self,
        *,
        user_id: str,
        schedule_id: str,
    ) -> AgentSchedule | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            record.deleted_at = now
            record.enabled = False
            record.status = ScheduleStatus.ARCHIVED.value
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def pause_schedule(
        self,
        *,
        user_id: str,
        schedule_id: str,
    ) -> AgentSchedule | None:
        return await self._set_status(
            user_id=user_id,
            schedule_id=schedule_id,
            status=ScheduleStatus.PAUSED,
            enabled=False,
        )

    async def resume_schedule(
        self,
        *,
        user_id: str,
        schedule_id: str,
    ) -> AgentSchedule | None:
        return await self._set_status(
            user_id=user_id,
            schedule_id=schedule_id,
            status=ScheduleStatus.ACTIVE,
            enabled=True,
        )

    async def run_now(
        self,
        *,
        user_id: str,
        schedule_id: str,
    ) -> AgentSchedule | None:
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            schedule = schedule_from_record(record)
        run = await self._enqueue_schedule_run(schedule=schedule)
        return await self._record_schedule_run(
            schedule_id=schedule.id,
            user_id=user_id,
            run_id=run.id,
            run_status=AgentRunStatus(run.status),
        )

    async def enqueue_due_schedules(self, *, limit: int = 100) -> tuple[AgentSchedule, ...]:
        now = utc_now()
        statement = (
            select(AgentScheduleRecord)
            .where(
                col(AgentScheduleRecord.enabled).is_(True),
                col(AgentScheduleRecord.status) == ScheduleStatus.ACTIVE.value,
                col(AgentScheduleRecord.deleted_at).is_(None),
                col(AgentScheduleRecord.next_run_at).is_not(None),
                col(AgentScheduleRecord.next_run_at) <= now,
            )
            .order_by(col(AgentScheduleRecord.next_run_at).asc())
            .limit(limit)
        )
        async with self.session_factory() as session:
            result = await session.exec(statement)
            records = tuple(result.all())
            schedules = tuple(schedule_from_record(record) for record in records)

        enqueued: list[AgentSchedule] = []
        for schedule in schedules:
            if schedule_missed_grace(schedule):
                await self._skip_missed_schedule(schedule=schedule)
                continue
            if not await self._may_enqueue_schedule(schedule):
                continue
            try:
                run = await self._enqueue_schedule_run(schedule=schedule)
            except (AgentRunRelationshipError, RunChatValidationError):
                logger.warning(
                    "Pausing schedule with an unavailable chat destination",
                    extra={"schedule_id": schedule.id, "user_id": schedule.user_id},
                )
                await self.pause_schedule(
                    user_id=schedule.user_id,
                    schedule_id=schedule.id,
                )
                continue
            updated = await self._record_schedule_fire(schedule=schedule, run_id=run.id)
            if updated is not None:
                enqueued.append(updated)
        return tuple(enqueued)

    async def _may_enqueue_schedule(self, schedule: AgentSchedule) -> bool:
        active_runs = await self.run_store.list_runs(
            user_id=schedule.user_id,
            statuses=(
                AgentRunStatus.QUEUED,
                AgentRunStatus.RUNNING,
                AgentRunStatus.CANCELLING,
            ),
            schedule_id=schedule.id,
            limit=schedule.max_concurrent_runs,
        )
        return len(active_runs) < schedule.max_concurrent_runs

    async def _enqueue_schedule_run(self, *, schedule: AgentSchedule) -> AgentRunRecord:
        schedule = await self._ensure_schedule_chat(schedule)
        payload = schedule_payload(schedule.payload_json, timezone=schedule.timezone)
        with tenant_user_context(schedule.user_id):
            policy = await self._effective_tool_policy_service().snapshot_for_agent(
                user_id=schedule.user_id,
                agent_id=schedule.agent_id,
                source="agent_runtime.schedule_store",
            )
            return await (self.tenant_run_store or self.run_store).enqueue_run(
                user_id=schedule.user_id,
                agent_id=schedule.agent_id,
                kind=AgentRunKind.SCHEDULED_AGENT_TURN,
                payload_json=payload,
                created_by_user_id=schedule.user_id,
                session_id=schedule.chat_id,
                chat_id=schedule.chat_id,
                schedule_id=schedule.id,
                browser_account_id=schedule.browser_account_id,
                queue="agent-default",
                max_attempts=retry_attempts(schedule.retry_policy_json),
                effective_tool_policy_json=policy.to_json(),
            )

    async def _ensure_schedule_chat(self, schedule: AgentSchedule) -> AgentSchedule:
        session_factory = self.tenant_session_factory or self.session_factory
        with tenant_user_context(schedule.user_id):
            async with session_factory() as session:
                if schedule.chat_id is not None:
                    await self.run_chat_service.require_chat_in_session(
                        session=session,
                        user_id=schedule.user_id,
                        agent_id=schedule.agent_id,
                        chat_id=schedule.chat_id,
                    )
                    return schedule
                result = await session.exec(
                    select(AgentScheduleRecord)
                    .where(
                        col(AgentScheduleRecord.id) == schedule.id,
                        col(AgentScheduleRecord.user_id) == schedule.user_id,
                        col(AgentScheduleRecord.deleted_at).is_(None),
                    )
                    .with_for_update(),
                )
                record = result.one_or_none()
                if record is None:
                    message = "Schedule was not found while linking its chat"
                    raise LookupError(message)
                if record.chat_id is None:
                    chat = await self.run_chat_service.create_chat_in_session(
                        session=session,
                        user_id=record.user_id,
                        agent_id=record.agent_id,
                        title=record.name,
                    )
                    record.chat_id = chat.id
                    record.updated_at = utc_now()
                    session.add(record)
                await session.commit()
                await session.refresh(record)
                return schedule_from_record(record)

    def _effective_tool_policy_service(self) -> EffectiveToolPolicyService:
        if self.effective_tool_policy_service is not None:
            return self.effective_tool_policy_service
        session_factory = self.policy_session_factory or self.session_factory
        return EffectiveToolPolicyService(
            SqlModelToolAuthorizationService(session_factory),
            SqlModelToolRoutingService(session_factory),
        )

    async def _record_schedule_fire(
        self,
        *,
        schedule: AgentSchedule,
        run_id: str,
    ) -> AgentSchedule | None:
        now = utc_now()
        next_run_at = next_schedule_run_at(schedule=schedule, now=now)
        status = schedule.status
        enabled = schedule.enabled
        if schedule.kind == ScheduleKind.ONE_SHOT:
            status = ScheduleStatus.COMPLETED
            enabled = False
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=schedule.user_id,
                schedule_id=schedule.id,
            )
            if record is None:
                return None
            record.last_run_at = now
            record.last_run_id = run_id
            record.last_run_status = AgentRunStatus.QUEUED.value
            record.next_run_at = next_run_at
            record.status = status.value
            record.enabled = enabled
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def _skip_missed_schedule(self, *, schedule: AgentSchedule) -> AgentSchedule | None:
        now = utc_now()
        next_run_at = next_schedule_run_at(schedule=schedule, now=now)
        status = schedule.status
        enabled = schedule.enabled
        if schedule.kind == ScheduleKind.ONE_SHOT:
            status = ScheduleStatus.COMPLETED
            enabled = False
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=schedule.user_id,
                schedule_id=schedule.id,
            )
            if record is None:
                return None
            record.next_run_at = next_run_at
            record.status = status.value
            record.enabled = enabled
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def _record_schedule_run(
        self,
        *,
        schedule_id: str,
        user_id: str,
        run_id: str,
        run_status: AgentRunStatus,
    ) -> AgentSchedule | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            record.last_run_at = now
            record.last_run_id = run_id
            record.last_run_status = run_status.value
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def _set_status(
        self,
        *,
        user_id: str,
        schedule_id: str,
        status: ScheduleStatus,
        enabled: bool,
    ) -> AgentSchedule | None:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._scoped_schedule(
                session=session,
                user_id=user_id,
                schedule_id=schedule_id,
            )
            if record is None:
                return None
            if enabled:
                chat = await self.run_chat_service.resolve_or_create_chat_in_session(
                    session=session,
                    user_id=user_id,
                    agent_id=record.agent_id,
                    chat_id=record.chat_id,
                    title=record.name,
                )
                record.chat_id = chat.id
            record.status = status.value
            record.enabled = enabled
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return schedule_from_record(record)

    async def _scoped_schedule(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        schedule_id: str,
    ) -> AgentScheduleRecord | None:
        record = await session.get(AgentScheduleRecord, schedule_id)
        if record is None or record.user_id != user_id or record.deleted_at is not None:
            return None
        return record

    async def _require_browser_account(
        self,
        *,
        user_id: str,
        agent_id: str,
        browser_account_id: str | None,
    ) -> None:
        if browser_account_id is None:
            return
        if self.browser_account_authorizer is None:
            raise PermissionError(SCHEDULE_BROWSER_ACCOUNTS_UNAVAILABLE)
        await self.browser_account_authorizer.require_account(
            user_id=user_id,
            agent_id=agent_id,
            browser_account_id=browser_account_id,
        )


def schedule_from_record(record: AgentScheduleRecord) -> AgentSchedule:
    return AgentSchedule(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        chat_id=record.chat_id,
        browser_account_id=record.browser_account_id,
        name=record.name,
        kind=ScheduleKind(record.kind),
        status=ScheduleStatus(record.status),
        timezone=record.timezone,
        payload_json=record.payload_json,
        run_at=record.run_at,
        interval_seconds=record.interval_seconds,
        cron_expression=record.cron_expression,
        next_run_at=record.next_run_at,
        last_run_at=record.last_run_at,
        last_run_id=record.last_run_id,
        last_run_status=(AgentRunStatus(record.last_run_status) if record.last_run_status is not None else None),
        max_concurrent_runs=record.max_concurrent_runs,
        catch_up_policy=ScheduleCatchUpPolicy(record.catch_up_policy),
        misfire_grace_seconds=record.misfire_grace_seconds,
        retry_policy_json=record.retry_policy_json,
        enabled=record.enabled,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def next_schedule_run_at(*, schedule: AgentSchedule, now: datetime) -> datetime | None:
    if schedule.kind == ScheduleKind.ONE_SHOT:
        return None
    if schedule.kind == ScheduleKind.INTERVAL and schedule.interval_seconds is not None:
        return now + timedelta(seconds=schedule.interval_seconds)
    return None


def schedule_payload(payload: JsonObject, *, timezone: str) -> JsonObject:
    now = utc_now()
    enriched = dict(payload)
    enriched["scheduled_context"] = {
        "date": now.date().isoformat(),
        "time": now.time().replace(microsecond=0).isoformat(),
        "timezone": timezone,
    }
    return safe_payload(enriched)


def schedule_missed_grace(schedule: AgentSchedule) -> bool:
    if schedule.next_run_at is None or schedule.catch_up_policy != ScheduleCatchUpPolicy.SKIP_MISSED:
        return False
    missed_by = utc_now() - as_utc(schedule.next_run_at)
    return missed_by.total_seconds() > schedule.misfire_grace_seconds


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def retry_attempts(retry_policy_json: JsonObject) -> int:
    value = retry_policy_json.get("max_attempts")
    if isinstance(value, int) and 1 <= value <= 10:
        return value
    return 3

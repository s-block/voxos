import asyncio
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import cast

import orjson
from pydantic import SecretStr
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    String,
    Table,
    UniqueConstraint,
    and_,
    delete,
    func,
    or_,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.base import Executable
from sqlmodel import Field, SQLModel, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.db.models import new_record_id, utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.voice.errors import (
    VoiceWorkerRegistrationError,
    VoiceWorkerUnavailableError,
)
from voxos.voice.livekit_tokens import ResolvedLiveKitCredentials
from voxos.voice.route_signing import MIN_ROUTE_SIGNING_SECRET_LENGTH

logger = logging.getLogger(__name__)
PROJECT_KEY_VERSION = "voxos-livekit-project-v1"
TENANT_ADMISSION_LOCK_SEED = 1_449_082_195
PROJECT_CAPACITY_LOCK_ID = 6_223_985_746_975_424_852


class VoiceCallDemandStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class VoiceProjectLeaseStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"


class VoiceProjectClaimResult(StrEnum):
    CLAIMED = "claimed"
    BUSY = "busy"
    CAPACITY_EXHAUSTED = "capacity_exhausted"


LIVE_DEMAND_STATUSES = (
    VoiceCallDemandStatus.PENDING.value,
    VoiceCallDemandStatus.READY.value,
    VoiceCallDemandStatus.ACTIVE.value,
)
ALL_DEMAND_STATUSES = tuple(status.value for status in VoiceCallDemandStatus)
ALL_PROJECT_LEASE_STATUSES = tuple(status.value for status in VoiceProjectLeaseStatus)
DEMAND_STATUS_CHECK = ", ".join(f"'{status}'" for status in ALL_DEMAND_STATUSES)
PROJECT_LEASE_STATUS_CHECK = ", ".join(f"'{status}'" for status in ALL_PROJECT_LEASE_STATUSES)


class VoiceCallDemandRecord(SQLModel, table=True):
    __tablename__ = "voice_call_demands"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "runtime_session_id",
            name="uq_voice_call_demands_user_runtime_session",
        ),
        Index(
            "ix_voice_call_demands_project_status_expires",
            "project_key",
            "status",
            "expires_at",
        ),
        Index(
            "ix_voice_call_demands_user_status_updated",
            "user_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_voice_call_demands_status_expires_requested",
            "status",
            "expires_at",
            "requested_at",
        ),
        Index(
            "ix_voice_call_demands_status_updated",
            "status",
            "updated_at",
        ),
        CheckConstraint(
            f"status IN ({DEMAND_STATUS_CHECK})",
            name="ck_voice_call_demands_status",
        ),
    )

    id: str = Field(default_factory=new_record_id, primary_key=True, max_length=36)
    user_id: str = Field(index=True, min_length=1, max_length=256)
    agent_id: str = Field(index=True, min_length=1, max_length=36)
    runtime_session_id: str = Field(min_length=1, max_length=36)
    room_name: str = Field(min_length=1, max_length=512)
    source: str = Field(
        min_length=1,
        max_length=64,
        sa_column=Column(String(64), nullable=False),
    )
    project_key: str = Field(min_length=64, max_length=64, index=True)
    status: str = Field(
        default=VoiceCallDemandStatus.PENDING.value,
        sa_column=Column(String(32), nullable=False),
    )
    worker_id: str | None = Field(default=None, max_length=256)
    worker_ready_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    project_claimed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    failure_code: str | None = Field(default=None, max_length=64)
    failure_type: str | None = Field(default=None, max_length=256)
    requested_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    ready_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    active_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class VoiceWorkerWarmRequestRecord(SQLModel, table=True):
    __tablename__ = "voice_worker_warm_requests"
    __table_args__ = (
        Index(
            "ix_voice_worker_warm_requests_project_expires",
            "project_key",
            "expires_at",
        ),
    )

    user_id: str = Field(primary_key=True, min_length=1, max_length=256)
    project_key: str = Field(min_length=64, max_length=64, index=True)
    requested_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True, index=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class VoiceProjectWorkerLeaseRecord(SQLModel, table=True):
    __tablename__ = "voice_project_worker_leases"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({PROJECT_LEASE_STATUS_CHECK})",
            name="ck_voice_project_worker_leases_status",
        ),
    )

    project_key: str = Field(primary_key=True, min_length=64, max_length=64)
    owner_id: str = Field(index=True, min_length=1, max_length=256)
    status: str = Field(
        default=VoiceProjectLeaseStatus.STARTING.value,
        sa_column=Column(String(32), nullable=False),
    )
    worker_id: str | None = Field(default=None, max_length=256)
    lease_expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True),
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


@dataclass(frozen=True, slots=True)
class VoiceCallDemand:
    id: str
    user_id: str
    agent_id: str
    runtime_session_id: str
    room_name: str
    source: str
    project_key: str
    status: VoiceCallDemandStatus
    worker_id: str | None
    worker_ready_expires_at: datetime | None
    failure_code: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VoiceWorkerWarmRequest:
    user_id: str
    project_key: str
    requested_at: datetime
    expires_at: datetime | None


type VoiceProjectDemand = VoiceCallDemand | VoiceWorkerWarmRequest


@dataclass(frozen=True, slots=True)
class VoiceProjectKeyFactory:
    signing_secret: SecretStr

    def __post_init__(self) -> None:
        if len(self.signing_secret.get_secret_value().strip()) < MIN_ROUTE_SIGNING_SECRET_LENGTH:
            message = "Voice project signing secret must contain at least 32 characters"
            raise ValueError(message)

    def create(self, credentials: ResolvedLiveKitCredentials) -> str:
        payload = orjson.dumps(
            (
                PROJECT_KEY_VERSION,
                credentials.livekit_url,
                credentials.api_key,
                credentials.api_secret.get_secret_value(),
            ),
        )
        return hmac.new(
            self.signing_secret.get_secret_value().encode("utf-8"),
            payload,
            sha256,
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class VoiceWorkerWarmRequestClient:
    session_factory: AsyncSessionFactory
    user_id: str
    project_key_factory: VoiceProjectKeyFactory

    async def request(
        self,
        *,
        credentials: ResolvedLiveKitCredentials,
    ) -> VoiceWorkerWarmRequest:
        async with self.session_factory() as session:
            now = await database_now(session)
            project_key = self.project_key_factory.create(credentials)
            values = {
                "user_id": self.user_id,
                "project_key": project_key,
                "requested_at": now,
                "expires_at": None,
                "created_at": now,
                "updated_at": now,
            }
            table = cast(
                Table,
                VoiceWorkerWarmRequestRecord.__table__,  # type: ignore[attr-defined]
            )
            dialect_name = session.get_bind().dialect.name
            insert_statement: Executable
            update_values = {key: value for key, value in values.items() if key != "created_at"}
            if dialect_name == "postgresql":
                insert_statement = (
                    postgresql_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["user_id"],
                        set_=update_values,
                    )
                )
            elif dialect_name == "sqlite":
                insert_statement = (
                    sqlite_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["user_id"],
                        set_=update_values,
                    )
                )
            else:
                message = f"Unsupported voice warm request dialect: {dialect_name}"
                raise RuntimeError(message)
            await session.exec(insert_statement)
            result = await session.exec(
                select(VoiceWorkerWarmRequestRecord).where(
                    col(VoiceWorkerWarmRequestRecord.user_id) == self.user_id,
                ),
            )
            record = result.one()
            await session.commit()
        logger.info(
            "Requested warm LiveKit project worker",
            extra={
                "user_id": self.user_id,
                "project_key": project_key,
            },
        )
        return warm_request_from_record(record)


@dataclass(frozen=True, slots=True)
class VoiceCallDemandClient:
    session_factory: AsyncSessionFactory
    user_id: str
    project_key_factory: VoiceProjectKeyFactory
    demand_ttl_seconds: int
    poll_interval_seconds: float
    start_timeout_seconds: float
    max_active_projects_per_tenant: int
    idle_project_ttl_seconds: float

    async def request(
        self,
        *,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> VoiceCallDemand:
        async with self.session_factory() as session:
            demand = await self.request_in_session(
                session=session,
                credentials=credentials,
                agent_id=agent_id,
                runtime_session_id=runtime_session_id,
                room_name=room_name,
                source=source,
            )
            await session.commit()
        return demand

    async def request_in_session(
        self,
        *,
        session: AsyncSession,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> VoiceCallDemand:
        """Persist demand in the caller's existing tenant transaction."""
        await lock_tenant_project_admission(session=session, user_id=self.user_id)
        now = await database_now(session)
        project_key = self.project_key_factory.create(credentials)
        expires_at = now + timedelta(seconds=self.demand_ttl_seconds)
        active_keys_statement = (
            select(VoiceCallDemandRecord.project_key)
            .where(
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) != runtime_session_id,
                or_(
                    and_(
                        col(VoiceCallDemandRecord.status).in_(
                            LIVE_DEMAND_STATUSES,
                        ),
                        col(VoiceCallDemandRecord.expires_at) > now,
                    ),
                    and_(
                        col(VoiceCallDemandRecord.status).in_(
                            (
                                VoiceCallDemandStatus.COMPLETED.value,
                                VoiceCallDemandStatus.EXPIRED.value,
                                VoiceCallDemandStatus.FAILED.value,
                            ),
                        ),
                        or_(
                            col(VoiceCallDemandRecord.worker_id).is_not(None),
                            col(VoiceCallDemandRecord.project_claimed_at).is_not(
                                None,
                            ),
                        ),
                        col(VoiceCallDemandRecord.updated_at)
                        > now
                        - timedelta(
                            seconds=self.idle_project_ttl_seconds,
                        ),
                    ),
                ),
            )
            .distinct()
        )
        active_keys_result = await session.exec(active_keys_statement)
        active_project_keys = set(active_keys_result.all())
        if project_key not in active_project_keys and len(active_project_keys) >= self.max_active_projects_per_tenant:
            reason_code = "tenant_project_capacity_exhausted"
            logger.warning(
                "Rejected voice call demand",
                extra={
                    "user_id": self.user_id,
                    "runtime_session_id": runtime_session_id,
                    "project_key": project_key,
                    "reason": reason_code,
                },
            )
            raise VoiceWorkerUnavailableError(reason_code)
        table = cast(
            Table,
            VoiceCallDemandRecord.__table__,  # type: ignore[attr-defined]
        )
        values = {
            "id": new_record_id(),
            "user_id": self.user_id,
            "agent_id": agent_id,
            "runtime_session_id": runtime_session_id,
            "room_name": room_name,
            "source": source,
            "project_key": project_key,
            "status": VoiceCallDemandStatus.PENDING.value,
            "worker_id": None,
            "worker_ready_expires_at": None,
            "project_claimed_at": None,
            "failure_code": None,
            "failure_type": None,
            "requested_at": now,
            "ready_at": None,
            "active_at": None,
            "completed_at": None,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
        }
        dialect_name = session.get_bind().dialect.name
        insert_statement: Executable
        if dialect_name == "postgresql":
            insert_statement = (
                postgresql_insert(table)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "runtime_session_id"],
                )
            )
        elif dialect_name == "sqlite":
            insert_statement = (
                sqlite_insert(table)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=["user_id", "runtime_session_id"],
                )
            )
        else:
            message = f"Unsupported voice demand dialect: {dialect_name}"
            raise RuntimeError(message)
        await session.exec(insert_statement)

        statement = (
            select(VoiceCallDemandRecord)
            .where(
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
            )
            .with_for_update()
        )
        result = await session.exec(statement)
        record = result.one_or_none()
        if record is None:
            message = "Voice call demand upsert did not return a record"
            raise RuntimeError(message)
        route_matches = demand_route_matches(
            record=record,
            project_key=project_key,
            agent_id=agent_id,
            room_name=room_name,
            source=source,
        )
        retryable_statuses = (
            VoiceCallDemandStatus.PENDING.value,
            VoiceCallDemandStatus.READY.value,
            VoiceCallDemandStatus.FAILED.value,
            VoiceCallDemandStatus.EXPIRED.value,
        )
        retained_project_cutoff = now - timedelta(
            seconds=self.idle_project_ttl_seconds,
        )
        if record.project_key != project_key and (
            (record.status in LIVE_DEMAND_STATUSES and as_utc(record.expires_at) > now)
            or (
                (record.worker_id is not None or record.project_claimed_at is not None)
                and as_utc(record.updated_at) > retained_project_cutoff
            )
        ):
            reason_code = "retained_project_route_conflict"
            self._log_route_rejection(
                runtime_session_id=runtime_session_id,
                project_key=project_key,
                reason=reason_code,
            )
            raise VoiceWorkerUnavailableError(reason_code)
        if not route_matches or record.status in {
            VoiceCallDemandStatus.FAILED.value,
            VoiceCallDemandStatus.EXPIRED.value,
        }:
            if record.status not in retryable_statuses:
                reason_code = "demand_route_conflict"
                self._log_route_rejection(
                    runtime_session_id=runtime_session_id,
                    project_key=project_key,
                    reason=reason_code,
                )
                raise VoiceWorkerUnavailableError(reason_code)
            retry_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.id) == record.id,
                    col(VoiceCallDemandRecord.status).in_(retryable_statuses),
                )
                .values(
                    agent_id=agent_id,
                    room_name=room_name,
                    source=source,
                    project_key=project_key,
                    status=VoiceCallDemandStatus.PENDING.value,
                    worker_id=None,
                    worker_ready_expires_at=None,
                    project_claimed_at=None,
                    failure_code=None,
                    failure_type=None,
                    requested_at=now,
                    ready_at=None,
                    active_at=None,
                    completed_at=None,
                    expires_at=expires_at,
                    updated_at=now,
                )
            )
            await session.exec(retry_statement)
        elif record.status in LIVE_DEMAND_STATUSES:
            refresh_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.id) == record.id,
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                )
                .values(expires_at=expires_at, updated_at=now)
            )
            await session.exec(refresh_statement)
        await session.flush()
        await session.refresh(record)
        if not demand_route_matches(
            record=record,
            project_key=project_key,
            agent_id=agent_id,
            room_name=room_name,
            source=source,
        ):
            reason_code = "demand_route_conflict"
            self._log_route_rejection(
                runtime_session_id=runtime_session_id,
                project_key=project_key,
                reason=reason_code,
            )
            raise VoiceWorkerUnavailableError(reason_code)

        demand = demand_from_record(record)
        logger.info(
            "Requested LiveKit project worker",
            extra={
                "user_id": self.user_id,
                "runtime_session_id": runtime_session_id,
                "source": source,
                "project_key": project_key,
            },
        )
        return demand

    async def request_and_wait(
        self,
        *,
        credentials: ResolvedLiveKitCredentials,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> VoiceCallDemand:
        demand = await self.request(
            credentials=credentials,
            agent_id=agent_id,
            runtime_session_id=runtime_session_id,
            room_name=room_name,
            source=source,
        )
        ready_demand = await self.wait_until_ready(demand.id)
        await self.refresh_ready_route(ready_demand.runtime_session_id)
        refreshed_demand = await self.get(ready_demand.id)
        if refreshed_demand.status != VoiceCallDemandStatus.READY:
            reason_code = "worker_readiness_expired"
            raise VoiceWorkerUnavailableError(reason_code)
        expected_project_key = self.project_key_factory.create(credentials)
        if not call_demand_route_matches(
            demand=refreshed_demand,
            project_key=expected_project_key,
            agent_id=agent_id,
            room_name=room_name,
            source=source,
        ):
            reason_code = "demand_route_conflict"
            self._log_route_rejection(
                runtime_session_id=runtime_session_id,
                project_key=expected_project_key,
                reason=reason_code,
            )
            raise VoiceWorkerUnavailableError(reason_code)
        return refreshed_demand

    async def refresh_ready_route(self, runtime_session_id: str) -> None:
        """Start the route TTL after readiness, immediately before token minting."""
        async with self.session_factory() as session:
            now = await database_now(session)
            table = cast(
                Table,
                VoiceCallDemandRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                    col(VoiceCallDemandRecord.worker_ready_expires_at) > now,
                )
                .values(
                    expires_at=now + timedelta(seconds=self.demand_ttl_seconds),
                    updated_at=now,
                )
                .returning(table.c.id)
            )
            result = await session.exec(statement)
            refreshed = result.scalar_one_or_none() is not None
            await session.commit()
        if not refreshed:
            reason_code = "worker_readiness_expired"
            raise VoiceWorkerUnavailableError(reason_code)

    async def wait_until_ready(self, demand_id: str) -> VoiceCallDemand:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.start_timeout_seconds
        while True:
            demand = await self.get(demand_id)
            if demand.status == VoiceCallDemandStatus.READY:
                return demand
            if demand.status == VoiceCallDemandStatus.ACTIVE:
                reason_code = "demand_already_active"
                raise VoiceWorkerUnavailableError(reason_code)
            if demand.status == VoiceCallDemandStatus.FAILED:
                raise VoiceWorkerRegistrationError(
                    demand.failure_code or "registration_failed",
                )
            if demand.status in {
                VoiceCallDemandStatus.COMPLETED,
                VoiceCallDemandStatus.EXPIRED,
            }:
                reason_code = "demand_expired"
                raise VoiceWorkerUnavailableError(reason_code)

            remaining = deadline - loop.time()
            if remaining <= 0:
                expired = await self._expire(
                    demand_id,
                    failure_code="worker_start_timeout",
                )
                if expired:
                    reason_code = "worker_start_timeout"
                    logger.warning(
                        "Voice worker start timed out",
                        extra={
                            "user_id": self.user_id,
                            "runtime_session_id": demand.runtime_session_id,
                            "project_key": demand.project_key,
                            "reason": reason_code,
                        },
                    )
                    raise VoiceWorkerUnavailableError(reason_code)
                await asyncio.sleep(0)
                continue
            await asyncio.sleep(min(self.poll_interval_seconds, remaining))

    async def wait_for_runtime(self, runtime_session_id: str) -> VoiceCallDemand:
        async with self.session_factory() as session:
            statement = select(VoiceCallDemandRecord.id).where(
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
            )
            result = await session.exec(statement)
            demand_id = result.one_or_none()
        if demand_id is None:
            reason_code = "demand_not_found"
            raise VoiceWorkerUnavailableError(reason_code)
        ready_demand = await self.wait_until_ready(demand_id)
        await self.refresh_ready_route(runtime_session_id)
        refreshed_demand = await self.get(ready_demand.id)
        if refreshed_demand.status != VoiceCallDemandStatus.READY:
            reason_code = "worker_readiness_expired"
            raise VoiceWorkerUnavailableError(reason_code)
        return refreshed_demand

    async def get(self, demand_id: str) -> VoiceCallDemand:
        async with self.session_factory() as session:
            now = await database_now(session)
            stale_ready_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.id) == demand_id,
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                    or_(
                        col(VoiceCallDemandRecord.worker_ready_expires_at).is_(None),
                        col(VoiceCallDemandRecord.worker_ready_expires_at) <= now,
                    ),
                )
                .values(
                    status=VoiceCallDemandStatus.PENDING.value,
                    worker_id=None,
                    worker_ready_expires_at=None,
                    ready_at=None,
                    updated_at=now,
                )
            )
            stale_result = await session.exec(stale_ready_statement)
            statement = select(VoiceCallDemandRecord).where(
                col(VoiceCallDemandRecord.id) == demand_id,
                col(VoiceCallDemandRecord.user_id) == self.user_id,
            )
            result = await session.exec(statement)
            record = result.one_or_none()
            if stale_result.rowcount:
                await session.commit()
        if record is None:
            reason_code = "demand_not_found"
            raise VoiceWorkerUnavailableError(reason_code)
        return demand_from_record(record)

    async def mark_active(
        self,
        runtime_session_id: str,
        worker_id: str,
        *,
        agent_id: str,
        room_name: str,
    ) -> VoiceCallDemand:
        activated = await self._set_runtime_status(
            runtime_session_id=runtime_session_id,
            status=VoiceCallDemandStatus.ACTIVE,
            worker_id=worker_id,
            agent_id=agent_id,
            room_name=room_name,
        )
        if not activated:
            reason_code = "demand_not_ready"
            raise VoiceWorkerUnavailableError(reason_code)
        return activated

    async def reserve_for_worker(
        self,
        *,
        runtime_session_id: str,
        agent_id: str,
        room_name: str,
        project_key: str,
        worker_id: str,
    ) -> bool:
        """Atomically fence one READY route before the SDK launches a job process."""
        async with self.session_factory() as session:
            now = await database_now(session)
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    col(VoiceCallDemandRecord.agent_id) == agent_id,
                    col(VoiceCallDemandRecord.room_name) == room_name,
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.worker_id) == worker_id,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                    col(VoiceCallDemandRecord.worker_ready_expires_at) > now,
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .values(
                    status=VoiceCallDemandStatus.ACTIVE.value,
                    active_at=now,
                    worker_ready_expires_at=None,
                    expires_at=now + timedelta(seconds=self.demand_ttl_seconds),
                    updated_at=now,
                )
            )
            result = await session.exec(statement)
            await session.commit()
        return result.rowcount == 1

    async def heartbeat(self, runtime_session_id: str, worker_id: str) -> None:
        async with self.session_factory() as session:
            now = await database_now(session)
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    col(VoiceCallDemandRecord.worker_id) == worker_id,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.ACTIVE.value,
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .values(
                    expires_at=now + timedelta(seconds=self.demand_ttl_seconds),
                    updated_at=now,
                )
            )
            result = await session.exec(statement)
            await session.commit()
        if result.rowcount != 1:
            reason_code = "demand_lease_lost"
            raise VoiceWorkerUnavailableError(reason_code)

    async def complete(self, runtime_session_id: str, worker_id: str) -> None:
        await self._set_runtime_status(
            runtime_session_id=runtime_session_id,
            status=VoiceCallDemandStatus.COMPLETED,
            worker_id=worker_id,
            agent_id=None,
            room_name=None,
        )

    async def cancel_in_session(
        self,
        *,
        session: AsyncSession,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        now = await database_now(session)
        statement = (
            update(VoiceCallDemandRecord)
            .where(
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                col(VoiceCallDemandRecord.status).in_(
                    (
                        VoiceCallDemandStatus.PENDING.value,
                        VoiceCallDemandStatus.READY.value,
                        VoiceCallDemandStatus.ACTIVE.value,
                    ),
                ),
            )
            .values(
                status=VoiceCallDemandStatus.FAILED.value,
                failure_code=failure_code,
                failure_type=None,
                worker_ready_expires_at=None,
                expires_at=now,
                updated_at=now,
            )
        )
        await session.exec(statement)

    async def complete_in_session(
        self,
        *,
        session: AsyncSession,
        runtime_session_id: str,
    ) -> None:
        now = await database_now(session)
        statement = (
            update(VoiceCallDemandRecord)
            .where(
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
            )
            .values(
                status=VoiceCallDemandStatus.COMPLETED.value,
                failure_code=None,
                failure_type=None,
                worker_ready_expires_at=None,
                completed_at=now,
                expires_at=now,
                updated_at=now,
            )
        )
        await session.exec(statement)

    async def _set_runtime_status(
        self,
        *,
        runtime_session_id: str,
        status: VoiceCallDemandStatus,
        worker_id: str,
        agent_id: str | None,
        room_name: str | None,
    ) -> VoiceCallDemand | None:
        async with self.session_factory() as session:
            now = await database_now(session)
            allowed_statuses: tuple[str, ...]
            if status == VoiceCallDemandStatus.ACTIVE:
                # Admission changes READY to ACTIVE before the SDK spawns a job.
                # The entrypoint repeats this operation to verify the same worker
                # still owns the durable reservation.
                allowed_statuses = (
                    VoiceCallDemandStatus.READY.value,
                    VoiceCallDemandStatus.ACTIVE.value,
                )
                values = {
                    "status": status.value,
                    "active_at": now,
                    "expires_at": now + timedelta(seconds=self.demand_ttl_seconds),
                    "worker_ready_expires_at": None,
                    "updated_at": now,
                }
                require_unexpired = True
            else:
                allowed_statuses = (
                    VoiceCallDemandStatus.READY.value,
                    VoiceCallDemandStatus.ACTIVE.value,
                )
                values = {
                    "status": status.value,
                    "completed_at": now,
                    "expires_at": now,
                    "updated_at": now,
                }
                require_unexpired = False
            filters = [
                col(VoiceCallDemandRecord.user_id) == self.user_id,
                col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                col(VoiceCallDemandRecord.worker_id) == worker_id,
                col(VoiceCallDemandRecord.status).in_(allowed_statuses),
            ]
            if require_unexpired:
                if agent_id is None or room_name is None:
                    message = "agent_id and room_name are required to activate voice demand"
                    raise ValueError(message)
                filters.append(col(VoiceCallDemandRecord.agent_id) == agent_id)
                filters.append(col(VoiceCallDemandRecord.room_name) == room_name)
                filters.append(col(VoiceCallDemandRecord.expires_at) > now)
                filters.append(
                    or_(
                        and_(
                            col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                            col(VoiceCallDemandRecord.worker_ready_expires_at) > now,
                        ),
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.ACTIVE.value,
                    ),
                )
            statement = update(VoiceCallDemandRecord).where(*filters).values(**values)
            result = await session.exec(statement)
            record: VoiceCallDemandRecord | None = None
            if result.rowcount == 1:
                record_statement = select(VoiceCallDemandRecord).where(
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    col(VoiceCallDemandRecord.worker_id) == worker_id,
                    col(VoiceCallDemandRecord.status) == status.value,
                )
                record_result = await session.exec(record_statement)
                record = record_result.one_or_none()
            await session.commit()
        if record is None:
            return None
        return demand_from_record(record)

    def _log_route_rejection(
        self,
        *,
        runtime_session_id: str,
        project_key: str,
        reason: str,
    ) -> None:
        logger.warning(
            "Rejected voice call route",
            extra={
                "user_id": self.user_id,
                "runtime_session_id": runtime_session_id,
                "project_key": project_key,
                "reason": reason,
            },
        )

    async def _expire(self, demand_id: str, *, failure_code: str) -> bool:
        async with self.session_factory() as session:
            now = await database_now(session)
            table = cast(
                Table,
                VoiceCallDemandRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.id) == demand_id,
                    col(VoiceCallDemandRecord.user_id) == self.user_id,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.PENDING.value,
                )
                .values(
                    status=VoiceCallDemandStatus.EXPIRED.value,
                    failure_code=failure_code,
                    expires_at=now,
                    updated_at=now,
                )
                .returning(table.c.id)
            )
            result = await session.exec(statement)
            expired = result.scalar_one_or_none() is not None
            await session.commit()
        return expired


@dataclass(frozen=True, slots=True)
class VoiceWorkerDemandStore:
    session_factory: AsyncSessionFactory
    warm_requests_enabled: bool = True

    async def list_live_demands(self) -> tuple[VoiceProjectDemand, ...]:
        async with self.session_factory() as session:
            now = await database_now(session)
            expired_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                    col(VoiceCallDemandRecord.expires_at) <= now,
                )
                .values(
                    status=VoiceCallDemandStatus.EXPIRED.value,
                    failure_code="demand_expired",
                    updated_at=now,
                )
            )
            await session.exec(expired_statement)
            await session.exec(
                delete(VoiceWorkerWarmRequestRecord).where(
                    col(VoiceWorkerWarmRequestRecord.expires_at).is_not(None),
                    col(VoiceWorkerWarmRequestRecord.expires_at) <= now,
                ),
            )

            live_statement = (
                select(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .order_by(col(VoiceCallDemandRecord.requested_at).asc())
            )
            live_result = await session.exec(live_statement)
            call_records = live_result.all()
            warm_records: tuple[VoiceWorkerWarmRequestRecord, ...] = ()
            if self.warm_requests_enabled:
                warm_result = await session.exec(
                    select(VoiceWorkerWarmRequestRecord)
                    .where(
                        or_(
                            col(VoiceWorkerWarmRequestRecord.expires_at).is_(None),
                            col(VoiceWorkerWarmRequestRecord.expires_at) > now,
                        ),
                    )
                    .order_by(col(VoiceWorkerWarmRequestRecord.requested_at).asc()),
                )
                warm_records = tuple(warm_result.all())
            await session.commit()
        return (
            *(demand_from_record(record) for record in call_records),
            *(warm_request_from_record(record) for record in warm_records),
        )

    async def has_live_project_demand(self, project_key: str) -> bool:
        async with self.session_factory() as session:
            now = await database_now(session)
            call_statement = (
                select(VoiceCallDemandRecord.id)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .limit(1)
            )
            call_result = await session.exec(call_statement)
            if call_result.one_or_none() is not None:
                return True
            if not self.warm_requests_enabled:
                return False
            warm_statement = (
                select(VoiceWorkerWarmRequestRecord.user_id)
                .where(
                    col(VoiceWorkerWarmRequestRecord.project_key) == project_key,
                    or_(
                        col(VoiceWorkerWarmRequestRecord.expires_at).is_(None),
                        col(VoiceWorkerWarmRequestRecord.expires_at) > now,
                    ),
                )
                .limit(1)
            )
            warm_result = await session.exec(warm_statement)
        return warm_result.one_or_none() is not None

    async def live_project_lease_owners(
        self,
        project_keys: tuple[str, ...],
    ) -> dict[str, str]:
        if not project_keys:
            return {}
        async with self.session_factory() as session:
            now = await database_now(session)
            lease_reference_time = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else now
            statement = select(
                VoiceProjectWorkerLeaseRecord.project_key,
                VoiceProjectWorkerLeaseRecord.owner_id,
            ).where(
                col(VoiceProjectWorkerLeaseRecord.project_key).in_(project_keys),
                col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > lease_reference_time,
            )
            result = await session.exec(statement)
        return dict(result.all())

    async def mark_project_ready(
        self,
        *,
        project_key: str,
        owner_id: str,
        worker_id: str,
        max_active_calls: int,
        observed_active_jobs: int,
    ) -> bool:
        async with self.session_factory() as session:
            ready_expires_at = await lock_owned_project_lease(
                session=session,
                project_key=project_key,
                owner_id=owner_id,
                required_status=VoiceProjectLeaseStatus.READY,
                worker_id=worker_id,
            )
            if ready_expires_at is None:
                return False
            now = await database_now(session)
            # Lock the admitted cohort in one snapshot. A concurrent request_fnc
            # reservation (READY -> ACTIVE) must wait, so it cannot be counted in
            # both states under PostgreSQL READ COMMITTED.
            admitted_statement = (
                select(
                    VoiceCallDemandRecord.id,
                    VoiceCallDemandRecord.status,
                )
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status).in_(
                        (
                            VoiceCallDemandStatus.READY.value,
                            VoiceCallDemandStatus.ACTIVE.value,
                        ),
                    ),
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .with_for_update()
            )
            admitted_result = await session.exec(admitted_statement)
            admitted_rows = tuple(admitted_result.all())
            ready_call_count = sum(status == VoiceCallDemandStatus.READY.value for _demand_id, status in admitted_rows)
            active_call_count = sum(
                status == VoiceCallDemandStatus.ACTIVE.value for _demand_id, status in admitted_rows
            )
            unreconciled_observed_jobs = max(
                observed_active_jobs - active_call_count,
                0,
            )
            admitted_call_count = ready_call_count + active_call_count + unreconciled_observed_jobs
            capacity_is_ambiguous = unreconciled_observed_jobs > 0 and ready_call_count > 0
            available_slots = max(max_active_calls - admitted_call_count, 0)
            # Snapshot and lock only the PENDING cohort this pass is allowed to
            # admit or reject. A demand inserted after this query remains pending
            # for the next reconciliation rather than being blanket-failed.
            pending_statement = (
                select(VoiceCallDemandRecord.id)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.PENDING.value,
                    col(VoiceCallDemandRecord.expires_at) > now,
                )
                .order_by(col(VoiceCallDemandRecord.requested_at).asc())
                .with_for_update()
            )
            pending_result = await session.exec(pending_statement)
            pending_ids = tuple(pending_result.all())
            now = await database_now(session)
            if as_utc(ready_expires_at) <= now:
                return False
            selected_ids = pending_ids[:available_slots]
            if selected_ids:
                ready_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.id).in_(selected_ids),
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.PENDING.value,
                        col(VoiceCallDemandRecord.expires_at) > now,
                    )
                    .values(
                        status=VoiceCallDemandStatus.READY.value,
                        worker_id=worker_id,
                        worker_ready_expires_at=ready_expires_at,
                        ready_at=now,
                        updated_at=now,
                    )
                )
                await session.exec(ready_statement)
            exhausted_ids = () if capacity_is_ambiguous else pending_ids[len(selected_ids) :]
            if exhausted_ids:
                exhausted_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.id).in_(exhausted_ids),
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.PENDING.value,
                        col(VoiceCallDemandRecord.expires_at) > now,
                    )
                    .values(
                        status=VoiceCallDemandStatus.FAILED.value,
                        failure_code="project_call_capacity_exhausted",
                        failure_type=None,
                        expires_at=now,
                        updated_at=now,
                    )
                )
                await session.exec(exhausted_statement)
                logger.warning(
                    "Rejected voice calls at project capacity",
                    extra={
                        "project_key": project_key,
                        "reason": "project_call_capacity_exhausted",
                    },
                )
            await session.commit()
        return True

    async def mark_project_unready(self, *, project_key: str, owner_id: str) -> bool:
        async with self.session_factory() as session:
            if (
                await lock_owned_project_lease(
                    session=session,
                    project_key=project_key,
                    owner_id=owner_id,
                )
                is None
            ):
                return False
            now = await database_now(session)
            demand_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                )
                .values(
                    status=VoiceCallDemandStatus.PENDING.value,
                    worker_id=None,
                    worker_ready_expires_at=None,
                    ready_at=None,
                    updated_at=now,
                )
            )
            await session.exec(demand_statement)
            lease_statement = (
                update(VoiceProjectWorkerLeaseRecord)
                .where(
                    col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                    col(VoiceProjectWorkerLeaseRecord.owner_id) == owner_id,
                    col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > now,
                )
                .values(
                    status=VoiceProjectLeaseStatus.STARTING.value,
                    worker_id=None,
                    updated_at=now,
                )
            )
            await session.exec(lease_statement)
            await session.commit()
        return True

    async def mark_project_failed(
        self,
        *,
        project_key: str,
        owner_id: str,
        failure_code: str,
        failure_type: str | None,
    ) -> bool:
        async with self.session_factory() as session:
            if (
                await lock_owned_project_lease(
                    session=session,
                    project_key=project_key,
                    owner_id=owner_id,
                )
                is None
            ):
                return False
            now = await database_now(session)
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                )
                .values(
                    status=VoiceCallDemandStatus.FAILED.value,
                    failure_code=failure_code,
                    failure_type=failure_type,
                    expires_at=now,
                    worker_ready_expires_at=None,
                    updated_at=now,
                )
            )
            await session.exec(statement)
            await session.exec(
                delete(VoiceWorkerWarmRequestRecord).where(
                    col(VoiceWorkerWarmRequestRecord.project_key) == project_key,
                ),
            )
            await session.commit()
        return True

    async def mark_demand_failed(
        self,
        *,
        demand_id: str,
        project_key: str,
        owner_id: str,
        failure_code: str,
        failure_type: str | None,
    ) -> bool:
        async with self.session_factory() as session:
            if (
                await lock_owned_project_lease(
                    session=session,
                    project_key=project_key,
                    owner_id=owner_id,
                )
                is None
            ):
                return False
            now = await database_now(session)
            statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.id) == demand_id,
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                )
                .values(
                    status=VoiceCallDemandStatus.FAILED.value,
                    failure_code=failure_code,
                    failure_type=failure_type,
                    expires_at=now,
                    worker_ready_expires_at=None,
                    updated_at=now,
                )
            )
            await session.exec(statement)
            await session.commit()
        return True

    async def remove_warm_request(
        self,
        *,
        user_id: str,
        project_key: str,
        owner_id: str,
    ) -> bool:
        async with self.session_factory() as session:
            if (
                await lock_owned_project_lease(
                    session=session,
                    project_key=project_key,
                    owner_id=owner_id,
                )
                is None
            ):
                return False
            await session.exec(
                delete(VoiceWorkerWarmRequestRecord).where(
                    col(VoiceWorkerWarmRequestRecord.user_id) == user_id,
                    col(VoiceWorkerWarmRequestRecord.project_key) == project_key,
                ),
            )
            await session.commit()
        return True

    async def claim_project(
        self,
        *,
        project_key: str,
        owner_id: str,
        lease_ttl_seconds: float,
        max_active_projects: int,
    ) -> VoiceProjectClaimResult:
        async with self.session_factory() as session:
            dialect_name = session.get_bind().dialect.name
            if dialect_name == "postgresql":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_id)"),
                    {"lock_id": PROJECT_CAPACITY_LOCK_ID},
                )
            now = await database_now(session)
            lease_reference_time = func.clock_timestamp() if dialect_name == "postgresql" else now
            live_owner_statement = (
                select(VoiceProjectWorkerLeaseRecord.owner_id)
                .where(
                    col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                    col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > lease_reference_time,
                )
                .with_for_update()
            )
            live_owner_result = await session.exec(live_owner_statement)
            live_owner_id = live_owner_result.one_or_none()
            if live_owner_id is not None and live_owner_id != owner_id:
                return VoiceProjectClaimResult.BUSY

            now = await database_now(session)
            lease_reference_time = func.clock_timestamp() if dialect_name == "postgresql" else now
            count_statement = select(func.count()).where(
                col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > lease_reference_time,
            )
            count_result = await session.exec(count_statement)
            active_project_count = count_result.one()
            owns_live_lease = live_owner_id == owner_id
            if not owns_live_lease and active_project_count >= max_active_projects:
                capacity_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status).in_(LIVE_DEMAND_STATUSES),
                    )
                    .values(
                        status=VoiceCallDemandStatus.FAILED.value,
                        failure_code="project_capacity_exhausted",
                        failure_type=None,
                        expires_at=now,
                        worker_ready_expires_at=None,
                        updated_at=now,
                    )
                )
                await session.exec(capacity_statement)
                await session.commit()
                return VoiceProjectClaimResult.CAPACITY_EXHAUSTED

            timestamp_value = func.clock_timestamp() if dialect_name == "postgresql" else now
            lease_expires_at = timestamp_value + timedelta(seconds=lease_ttl_seconds)
            values = {
                "project_key": project_key,
                "owner_id": owner_id,
                "status": VoiceProjectLeaseStatus.STARTING.value,
                "worker_id": None,
                "lease_expires_at": lease_expires_at,
                "created_at": timestamp_value,
                "updated_at": timestamp_value,
            }
            table = cast(
                Table,
                VoiceProjectWorkerLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            statement: Executable
            if dialect_name == "postgresql":
                statement = (
                    postgresql_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["project_key"],
                        set_={
                            "owner_id": owner_id,
                            "status": VoiceProjectLeaseStatus.STARTING.value,
                            "worker_id": None,
                            "lease_expires_at": lease_expires_at,
                            "updated_at": timestamp_value,
                        },
                        where=or_(
                            table.c.owner_id == owner_id,
                            table.c.lease_expires_at <= func.clock_timestamp(),
                        ),
                    )
                    .returning(table.c.project_key)
                )
            elif dialect_name == "sqlite":
                statement = (
                    sqlite_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["project_key"],
                        set_={
                            "owner_id": owner_id,
                            "status": VoiceProjectLeaseStatus.STARTING.value,
                            "worker_id": None,
                            "lease_expires_at": lease_expires_at,
                            "updated_at": now,
                        },
                        where=or_(
                            table.c.owner_id == owner_id,
                            table.c.lease_expires_at <= now,
                        ),
                    )
                    .returning(table.c.project_key)
                )
            else:
                message = f"Unsupported voice worker lease dialect: {dialect_name}"
                raise RuntimeError(message)
            result = await session.exec(statement)
            claimed = result.scalar_one_or_none() is not None
            if claimed:
                now = await database_now(session)
                claimed_demand_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status).in_(
                            LIVE_DEMAND_STATUSES,
                        ),
                        col(VoiceCallDemandRecord.expires_at) > now,
                    )
                    .values(project_claimed_at=now, updated_at=now)
                )
                await session.exec(claimed_demand_statement)
                reset_ready_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                    )
                    .values(
                        status=VoiceCallDemandStatus.PENDING.value,
                        worker_id=None,
                        worker_ready_expires_at=None,
                        ready_at=None,
                        updated_at=now,
                    )
                )
                await session.exec(reset_ready_statement)
                fail_active_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.ACTIVE.value,
                    )
                    .values(
                        status=VoiceCallDemandStatus.FAILED.value,
                        failure_code="worker_lease_reclaimed",
                        failure_type=None,
                        worker_ready_expires_at=None,
                        expires_at=now,
                        updated_at=now,
                    )
                )
                await session.exec(fail_active_statement)
            await session.commit()
        if claimed:
            return VoiceProjectClaimResult.CLAIMED
        return VoiceProjectClaimResult.BUSY

    async def renew_project_lease(
        self,
        *,
        project_key: str,
        owner_id: str,
        lease_ttl_seconds: float,
        ready_worker_id: str | None = None,
    ) -> bool:
        async with self.session_factory() as session:
            now = await database_now(session)
            dialect_name = session.get_bind().dialect.name
            timestamp_value = func.clock_timestamp() if dialect_name == "postgresql" else now
            lease_table = cast(
                Table,
                VoiceProjectWorkerLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            filters = [
                col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                col(VoiceProjectWorkerLeaseRecord.owner_id) == owner_id,
                col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > timestamp_value,
            ]
            if ready_worker_id is not None:
                filters.extend(
                    (
                        col(VoiceProjectWorkerLeaseRecord.status) == VoiceProjectLeaseStatus.READY.value,
                        col(VoiceProjectWorkerLeaseRecord.worker_id) == ready_worker_id,
                    ),
                )
            statement = (
                update(VoiceProjectWorkerLeaseRecord)
                .where(*filters)
                .values(
                    lease_expires_at=(timestamp_value + timedelta(seconds=lease_ttl_seconds)),
                    updated_at=timestamp_value,
                )
                .returning(lease_table.c.lease_expires_at)
            )
            result = await session.exec(statement)
            renewed_expires_at = result.scalar_one_or_none()
            if renewed_expires_at is not None and ready_worker_id is not None:
                ready_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                        col(VoiceCallDemandRecord.worker_id) == ready_worker_id,
                        col(VoiceCallDemandRecord.expires_at) > timestamp_value,
                    )
                    .values(
                        worker_ready_expires_at=renewed_expires_at,
                        updated_at=timestamp_value,
                    )
                )
                await session.exec(ready_statement)
            await session.commit()
        return renewed_expires_at is not None

    async def mark_project_lease_ready(
        self,
        *,
        project_key: str,
        owner_id: str,
        worker_id: str,
        lease_ttl_seconds: float,
    ) -> bool:
        async with self.session_factory() as session:
            now = await database_now(session)
            dialect_name = session.get_bind().dialect.name
            timestamp_value = func.clock_timestamp() if dialect_name == "postgresql" else now
            lease_expires_at = timestamp_value + timedelta(
                seconds=lease_ttl_seconds,
            )
            lease_table = cast(
                Table,
                VoiceProjectWorkerLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(VoiceProjectWorkerLeaseRecord)
                .where(
                    col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                    col(VoiceProjectWorkerLeaseRecord.owner_id) == owner_id,
                    col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > timestamp_value,
                )
                .values(
                    status=VoiceProjectLeaseStatus.READY.value,
                    worker_id=worker_id,
                    lease_expires_at=lease_expires_at,
                    updated_at=timestamp_value,
                )
                .returning(lease_table.c.lease_expires_at)
            )
            result = await session.exec(statement)
            renewed_expires_at = result.scalar_one_or_none()
            if renewed_expires_at is not None:
                claimed_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.project_claimed_at).is_not(None),
                        col(VoiceCallDemandRecord.status).in_(
                            LIVE_DEMAND_STATUSES,
                        ),
                    )
                    .values(
                        project_claimed_at=timestamp_value,
                        updated_at=timestamp_value,
                    )
                )
                await session.exec(claimed_statement)
                ready_statement = (
                    update(VoiceCallDemandRecord)
                    .where(
                        col(VoiceCallDemandRecord.project_key) == project_key,
                        col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                    )
                    .values(
                        worker_id=worker_id,
                        worker_ready_expires_at=renewed_expires_at,
                        updated_at=timestamp_value,
                    )
                )
                await session.exec(ready_statement)
            await session.commit()
        return renewed_expires_at is not None

    async def release_project_lease(self, *, project_key: str, owner_id: str) -> None:
        async with self.session_factory() as session:
            owner_statement = (
                select(VoiceProjectWorkerLeaseRecord.owner_id)
                .where(
                    col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                )
                .with_for_update()
            )
            owner_result = await session.exec(owner_statement)
            current_owner_id = owner_result.one_or_none()
            if current_owner_id != owner_id:
                return
            now = await database_now(session)
            reset_ready_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.READY.value,
                )
                .values(
                    status=VoiceCallDemandStatus.PENDING.value,
                    worker_id=None,
                    worker_ready_expires_at=None,
                    ready_at=None,
                    updated_at=now,
                )
            )
            await session.exec(reset_ready_statement)
            fail_active_statement = (
                update(VoiceCallDemandRecord)
                .where(
                    col(VoiceCallDemandRecord.project_key) == project_key,
                    col(VoiceCallDemandRecord.status) == VoiceCallDemandStatus.ACTIVE.value,
                )
                .values(
                    status=VoiceCallDemandStatus.FAILED.value,
                    failure_code="worker_registration_stopped",
                    failure_type=None,
                    worker_ready_expires_at=None,
                    expires_at=now,
                    updated_at=now,
                )
            )
            await session.exec(fail_active_statement)
            delete_statement = delete(VoiceProjectWorkerLeaseRecord).where(
                col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
                col(VoiceProjectWorkerLeaseRecord.owner_id) == owner_id,
            )
            await session.exec(delete_statement)
            await session.commit()

    async def prune_terminal_demands(self, *, retention_seconds: int) -> int:
        terminal_statuses = (
            VoiceCallDemandStatus.COMPLETED.value,
            VoiceCallDemandStatus.FAILED.value,
            VoiceCallDemandStatus.EXPIRED.value,
        )
        async with self.session_factory() as session:
            now = await database_now(session)
            cutoff = now - timedelta(seconds=retention_seconds)
            statement = delete(VoiceCallDemandRecord).where(
                col(VoiceCallDemandRecord.status).in_(terminal_statuses),
                col(VoiceCallDemandRecord.updated_at) < cutoff,
            )
            result = await session.exec(statement)
            await session.commit()
        return result.rowcount

    async def prune_expired_project_leases(self) -> int:
        async with self.session_factory() as session:
            now = await database_now(session)
            statement = delete(VoiceProjectWorkerLeaseRecord).where(
                col(VoiceProjectWorkerLeaseRecord.lease_expires_at) <= now,
            )
            result = await session.exec(statement)
            await session.commit()
        return result.rowcount


def demand_from_record(record: VoiceCallDemandRecord) -> VoiceCallDemand:
    return VoiceCallDemand(
        id=record.id,
        user_id=record.user_id,
        agent_id=record.agent_id,
        runtime_session_id=record.runtime_session_id,
        room_name=record.room_name,
        source=record.source,
        project_key=record.project_key,
        status=VoiceCallDemandStatus(record.status),
        worker_id=record.worker_id,
        worker_ready_expires_at=record.worker_ready_expires_at,
        failure_code=record.failure_code,
        expires_at=record.expires_at,
    )


def warm_request_from_record(
    record: VoiceWorkerWarmRequestRecord,
) -> VoiceWorkerWarmRequest:
    return VoiceWorkerWarmRequest(
        user_id=record.user_id,
        project_key=record.project_key,
        requested_at=record.requested_at,
        expires_at=record.expires_at,
    )


def demand_route_matches(
    *,
    record: VoiceCallDemandRecord,
    project_key: str,
    agent_id: str,
    room_name: str,
    source: str,
) -> bool:
    return (
        record.project_key == project_key
        and record.agent_id == agent_id
        and record.room_name == room_name
        and record.source == source
    )


def call_demand_route_matches(
    *,
    demand: VoiceCallDemand,
    project_key: str,
    agent_id: str,
    room_name: str,
    source: str,
) -> bool:
    return (
        demand.project_key == project_key
        and demand.agent_id == agent_id
        and demand.room_name == room_name
        and demand.source == source
    )


async def lock_owned_project_lease(
    *,
    session: AsyncSession,
    project_key: str,
    owner_id: str,
    required_status: VoiceProjectLeaseStatus | None = None,
    worker_id: str | None = None,
) -> datetime | None:
    now = await database_now(session)
    lease_reference_time = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else now
    filters = [
        col(VoiceProjectWorkerLeaseRecord.project_key) == project_key,
        col(VoiceProjectWorkerLeaseRecord.owner_id) == owner_id,
        col(VoiceProjectWorkerLeaseRecord.lease_expires_at) > lease_reference_time,
    ]
    if required_status is not None:
        filters.append(
            col(VoiceProjectWorkerLeaseRecord.status) == required_status.value,
        )
    if worker_id is not None:
        filters.append(col(VoiceProjectWorkerLeaseRecord.worker_id) == worker_id)
    statement = select(VoiceProjectWorkerLeaseRecord.lease_expires_at).where(*filters).with_for_update()
    result = await session.exec(statement)
    return result.one_or_none()


async def database_now(session: AsyncSession) -> datetime:
    if session.get_bind().dialect.name == "sqlite":
        result = await session.exec(select(func.julianday("now")))
        julian_day = float(result.one())
        unix_seconds = (julian_day - 2_440_587.5) * 86_400
        return datetime.fromtimestamp(unix_seconds, tz=UTC)
    result = await session.exec(select(func.clock_timestamp()))
    value = cast(datetime, result.one())
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def lock_tenant_project_admission(
    *,
    session: AsyncSession,
    user_id: str,
) -> None:
    """Serialize each tenant's project-capacity decision on PostgreSQL."""
    if session.get_bind().dialect.name != "postgresql":
        return
    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:user_id, :lock_seed))",
        ),
        {"user_id": user_id, "lock_seed": TENANT_ADMISSION_LOCK_SEED},
    )

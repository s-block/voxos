from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import orjson
from sqlalchemy import Table, and_, case, delete, func, or_, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql.base import Executable
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import set_tenant_user_id
from voxos.integrations.models import (
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
)
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
)
from voxos.integrations.whatsapp_jids import phone_number_from_whatsapp_pn_jid
from voxos.whatsapp.crypto import (
    WhatsAppCipherContext,
    WhatsAppEncryptedValue,
    WhatsAppSessionCipher,
    decode_session_data_key,
    encode_session_data_key,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppLinkMethod,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import (
    WhatsAppOutboxRecord,
    WhatsAppSessionKeyRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppSessionRecord,
)

MAX_AUTH_WRITES = 2048
MAX_AUTH_KEY_CATEGORY_LENGTH = 64
MAX_AUTH_LOGICAL_KEY_ID_LENGTH = 512
CHALLENGE_KEY_CATEGORY = "link_challenge"
CLAIMABLE_SESSION_STATES = frozenset(
    {
        WhatsAppSessionState.AWAITING_LINK,
        WhatsAppSessionState.CONNECTING,
        WhatsAppSessionState.CONNECTED,
        WhatsAppSessionState.RECONNECTING,
        WhatsAppSessionState.WAITING_FOR_CAPACITY,
    },
)
# Older bridge versions persisted connection replacement and unknown socket
# closures as terminal_local_failure. Reclaim those sessions once under the
# current bounded reconnect policy so a worker restart can restore valid saved
# credentials instead of leaving the integration failed forever.
RECOVERABLE_SESSION_FAILURE_CODES = frozenset(
    {
        "already_linked",
        "terminal_local_failure",
    },
)


class WhatsAppSessionDataError(RuntimeError):
    def __init__(self, message: str = "WhatsApp session data is unavailable") -> None:
        super().__init__(message)


class WhatsAppLeaseLostError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("WhatsApp session lease is no longer owned")


@dataclass(frozen=True, slots=True)
class WhatsAppSessionDemand:
    user_id: str
    integration_id: str
    resource_id: str
    source_resource_id: str
    credential_id: str
    desired_state: WhatsAppDesiredState
    state: WhatsAppSessionState
    link_method: WhatsAppLinkMethod
    challenge_request_revision: int
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class WhatsAppLeaseFence:
    user_id: str
    integration_id: str
    owner_id: str
    generation: int
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WhatsAppAuthWrite:
    category: str
    logical_id: str
    value: object | None


@dataclass(frozen=True, slots=True)
class StoredWhatsAppChallenge:
    method: WhatsAppLinkMethod
    value: str
    expires_at: datetime


class WhatsAppWorkerStore:
    def __init__(self, session_factory: AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def fenced_tenant_session(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            await _enter_fenced_tenant_session(
                session=session,
                fence=fence,
            )
            yield session

    async def list_session_demands(
        self,
        *,
        owner_id: str,
        limit: int,
    ) -> tuple[WhatsAppSessionDemand, ...]:
        if limit <= 0:
            return ()
        async with self._session_factory() as session:
            now = await _database_now(session)
            lease_join = and_(
                col(WhatsAppSessionLeaseRecord.integration_id) == col(WhatsAppSessionRecord.integration_id),
                col(WhatsAppSessionLeaseRecord.user_id) == col(WhatsAppSessionRecord.user_id),
            )
            active_owner_lease = and_(
                col(WhatsAppSessionLeaseRecord.owner_id) == owner_id,
                col(WhatsAppSessionLeaseRecord.lease_expires_at) > now,
            )
            statement = (
                select(WhatsAppSessionRecord)
                .outerjoin(WhatsAppSessionLeaseRecord, lease_join)
                .where(
                    or_(
                        col(WhatsAppSessionRecord.state) == WhatsAppSessionState.DISCONNECTING.value,
                        and_(
                            col(WhatsAppSessionRecord.desired_state) == WhatsAppDesiredState.CONNECTED.value,
                            or_(
                                col(WhatsAppSessionRecord.state).in_(
                                    tuple(state.value for state in CLAIMABLE_SESSION_STATES),
                                ),
                                and_(
                                    col(WhatsAppSessionRecord.state) == WhatsAppSessionState.FAILED.value,
                                    col(WhatsAppSessionRecord.failure_code).in_(
                                        tuple(RECOVERABLE_SESSION_FAILURE_CODES),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    or_(
                        col(WhatsAppSessionLeaseRecord.integration_id).is_(None),
                        col(WhatsAppSessionLeaseRecord.owner_id) == owner_id,
                        col(WhatsAppSessionLeaseRecord.lease_expires_at) <= now,
                    ),
                )
                .order_by(
                    case((active_owner_lease, 0), else_=1),
                    col(WhatsAppSessionRecord.updated_at),
                    col(WhatsAppSessionRecord.id),
                )
                .limit(limit)
            )
            records = (await session.exec(statement)).all()
        return tuple(_demand_from_record(record) for record in records)

    async def claim_session(
        self,
        *,
        demand: WhatsAppSessionDemand,
        owner_id: str,
        lease_ttl_seconds: float,
    ) -> WhatsAppLeaseFence | None:
        if lease_ttl_seconds <= 0:
            message = "lease TTL must be positive"
            raise ValueError(message)
        async with self._session_factory() as session:
            now = await _database_now(session)
            dialect_name = session.get_bind().dialect.name
            timestamp = func.clock_timestamp() if dialect_name == "postgresql" else now
            expires_at = timestamp + timedelta(seconds=lease_ttl_seconds)
            table = cast(
                Table,
                WhatsAppSessionLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            generation = case(
                (
                    and_(
                        table.c.owner_id == owner_id,
                        table.c.lease_expires_at > timestamp,
                    ),
                    table.c.generation,
                ),
                else_=table.c.generation + 1,
            )
            values = {
                "integration_id": demand.integration_id,
                "user_id": demand.user_id,
                "owner_id": owner_id,
                "generation": 1,
                "lease_expires_at": expires_at,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            statement: Executable
            if dialect_name == "postgresql":
                statement = (
                    postgresql_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["integration_id"],
                        set_={
                            "user_id": demand.user_id,
                            "owner_id": owner_id,
                            "generation": generation,
                            "lease_expires_at": expires_at,
                            "updated_at": timestamp,
                        },
                        where=and_(
                            table.c.user_id == demand.user_id,
                            or_(
                                table.c.owner_id == owner_id,
                                table.c.lease_expires_at <= timestamp,
                            ),
                        ),
                    )
                    .returning(
                        table.c.generation,
                        table.c.lease_expires_at,
                    )
                )
            elif dialect_name == "sqlite":
                statement = (
                    sqlite_insert(table)
                    .values(**values)
                    .on_conflict_do_update(
                        index_elements=["integration_id"],
                        set_={
                            "user_id": demand.user_id,
                            "owner_id": owner_id,
                            "generation": generation,
                            "lease_expires_at": expires_at,
                            "updated_at": timestamp,
                        },
                        where=and_(
                            table.c.user_id == demand.user_id,
                            or_(
                                table.c.owner_id == owner_id,
                                table.c.lease_expires_at <= timestamp,
                            ),
                        ),
                    )
                    .returning(
                        table.c.generation,
                        table.c.lease_expires_at,
                    )
                )
            else:
                message = f"Unsupported WhatsApp worker lease dialect: {dialect_name}"
                raise RuntimeError(message)
            result = await session.exec(statement)
            claimed = result.one_or_none()
            await session.commit()
        if claimed is None:
            return None
        return WhatsAppLeaseFence(
            user_id=demand.user_id,
            integration_id=demand.integration_id,
            owner_id=owner_id,
            generation=int(claimed[0]),
            expires_at=_as_utc(cast(datetime, claimed[1])),
        )

    async def renew_session(
        self,
        *,
        fence: WhatsAppLeaseFence,
        lease_ttl_seconds: float,
    ) -> WhatsAppLeaseFence | None:
        async with self._session_factory() as session:
            now = await _database_now(session)
            dialect_name = session.get_bind().dialect.name
            timestamp = func.clock_timestamp() if dialect_name == "postgresql" else now
            table = cast(
                Table,
                WhatsAppSessionLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(WhatsAppSessionLeaseRecord)
                .where(
                    col(WhatsAppSessionLeaseRecord.integration_id) == fence.integration_id,
                    col(WhatsAppSessionLeaseRecord.user_id) == fence.user_id,
                    col(WhatsAppSessionLeaseRecord.owner_id) == fence.owner_id,
                    col(WhatsAppSessionLeaseRecord.generation) == fence.generation,
                    col(WhatsAppSessionLeaseRecord.lease_expires_at) > timestamp,
                )
                .values(
                    lease_expires_at=timestamp + timedelta(seconds=lease_ttl_seconds),
                    updated_at=timestamp,
                )
                .returning(table.c.lease_expires_at)
            )
            result = await session.exec(statement)
            expires_at = result.scalar_one_or_none()
            await session.commit()
        if expires_at is None:
            return None
        return WhatsAppLeaseFence(
            user_id=fence.user_id,
            integration_id=fence.integration_id,
            owner_id=fence.owner_id,
            generation=fence.generation,
            expires_at=_as_utc(cast(datetime, expires_at)),
        )

    async def advance_session_generation(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> WhatsAppLeaseFence:
        async with self._session_factory() as session:
            now = await _database_now(session)
            timestamp = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else now
            table = cast(
                Table,
                WhatsAppSessionLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(WhatsAppSessionLeaseRecord)
                .where(
                    col(WhatsAppSessionLeaseRecord.integration_id) == fence.integration_id,
                    col(WhatsAppSessionLeaseRecord.user_id) == fence.user_id,
                    col(WhatsAppSessionLeaseRecord.owner_id) == fence.owner_id,
                    col(WhatsAppSessionLeaseRecord.generation) == fence.generation,
                    col(WhatsAppSessionLeaseRecord.lease_expires_at) > timestamp,
                )
                .values(
                    generation=fence.generation + 1,
                    updated_at=timestamp,
                )
                .returning(table.c.lease_expires_at)
            )
            result = await session.exec(statement)
            expires_at = result.scalar_one_or_none()
            await session.commit()
        if expires_at is None:
            raise WhatsAppLeaseLostError
        return WhatsAppLeaseFence(
            user_id=fence.user_id,
            integration_id=fence.integration_id,
            owner_id=fence.owner_id,
            generation=fence.generation + 1,
            expires_at=_as_utc(cast(datetime, expires_at)),
        )

    async def release_session(self, *, fence: WhatsAppLeaseFence) -> bool:
        async with self._session_factory() as session:
            now = await _database_now(session)
            table = cast(
                Table,
                WhatsAppSessionLeaseRecord.__table__,  # type: ignore[attr-defined]
            )
            session_exists = (
                await session.exec(
                    select(WhatsAppSessionRecord.id).where(
                        col(WhatsAppSessionRecord.user_id) == fence.user_id,
                        col(WhatsAppSessionRecord.integration_id) == fence.integration_id,
                    ),
                )
            ).one_or_none()
            where = (
                col(WhatsAppSessionLeaseRecord.integration_id) == fence.integration_id,
                col(WhatsAppSessionLeaseRecord.user_id) == fence.user_id,
                col(WhatsAppSessionLeaseRecord.owner_id) == fence.owner_id,
                col(WhatsAppSessionLeaseRecord.generation) == fence.generation,
            )
            statement: Executable
            if session_exists is None:
                statement = (
                    delete(WhatsAppSessionLeaseRecord)
                    .where(*where)
                    .returning(
                        table.c.integration_id,
                    )
                )
            else:
                statement = (
                    update(WhatsAppSessionLeaseRecord)
                    .where(*where)
                    .values(
                        lease_expires_at=now,
                        updated_at=now,
                    )
                    .returning(table.c.integration_id)
                )
            result = await session.exec(statement)
            released = result.scalar_one_or_none() is not None
            await session.commit()
        return released

    async def mark_stale_submissions_ambiguous(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> int:
        async with self._session_factory() as session:
            await _enter_fenced_tenant_session(session=session, fence=fence)
            now = await _database_now(session)
            table = cast(
                Table,
                WhatsAppOutboxRecord.__table__,  # type: ignore[attr-defined]
            )
            statement = (
                update(WhatsAppOutboxRecord)
                .where(
                    col(WhatsAppOutboxRecord.user_id) == fence.user_id,
                    col(WhatsAppOutboxRecord.integration_id) == fence.integration_id,
                    col(WhatsAppOutboxRecord.status) == WhatsAppOutboxStatus.SUBMITTING.value,
                )
                .values(
                    status=WhatsAppOutboxStatus.AMBIGUOUS.value,
                    owner_id=None,
                    lease_generation=None,
                    failure_code="worker_restarted_during_submission",
                    updated_at=now,
                )
                .returning(table.c.reply_id)
            )
            result = await session.exec(statement)
            returned_reply_ids = tuple(result.scalars().all())
            reply_ids = tuple(reply_id for reply_id in returned_reply_ids if reply_id is not None)
            if reply_ids:
                await session.exec(
                    update(IntegrationReplyRecord)
                    .where(
                        col(IntegrationReplyRecord.user_id) == fence.user_id,
                        col(IntegrationReplyRecord.id).in_(reply_ids),
                    )
                    .values(
                        status=IntegrationReplyStatus.AMBIGUOUS.value,
                        error_code="worker_restarted_during_submission",
                        updated_at=now,
                    ),
                )
            await session.commit()
        return len(returned_reply_ids)

    async def claim_outbox(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> WhatsAppOutboxRecord | None:
        async with self._session_factory() as session:
            await _enter_fenced_tenant_session(session=session, fence=fence)
            whatsapp_session = (
                await session.exec(
                    select(WhatsAppSessionRecord).where(
                        col(WhatsAppSessionRecord.user_id) == fence.user_id,
                        col(WhatsAppSessionRecord.integration_id) == fence.integration_id,
                        col(WhatsAppSessionRecord.desired_state) == WhatsAppDesiredState.CONNECTED.value,
                        col(WhatsAppSessionRecord.state) == WhatsAppSessionState.CONNECTED.value,
                    ),
                )
            ).one_or_none()
            if whatsapp_session is None:
                return None
            statement = (
                select(WhatsAppOutboxRecord)
                .where(
                    col(WhatsAppOutboxRecord.user_id) == fence.user_id,
                    col(WhatsAppOutboxRecord.integration_id) == fence.integration_id,
                    col(WhatsAppOutboxRecord.status) == WhatsAppOutboxStatus.QUEUED.value,
                )
                .order_by(
                    col(WhatsAppOutboxRecord.created_at),
                    col(WhatsAppOutboxRecord.id),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            record = (await session.exec(statement)).one_or_none()
            if record is None:
                return None
            now = await _database_now(session)
            record.status = WhatsAppOutboxStatus.SUBMITTING.value
            record.owner_id = fence.owner_id
            record.lease_generation = fence.generation
            record.submission_started_at = now
            record.updated_at = now
            await session.commit()
            await session.refresh(record)
            return record

    async def complete_outbox(
        self,
        *,
        fence: WhatsAppLeaseFence,
        outbox_id: str,
        status: WhatsAppOutboxStatus,
        external_message_id: str | None = None,
        failure_code: str | None = None,
    ) -> bool:
        if status not in {
            WhatsAppOutboxStatus.QUEUED,
            WhatsAppOutboxStatus.SENT,
            WhatsAppOutboxStatus.FAILED,
            WhatsAppOutboxStatus.AMBIGUOUS,
        }:
            message = "outbox completion status is invalid"
            raise ValueError(message)
        if status == WhatsAppOutboxStatus.SENT and external_message_id is None:
            message = "sent outbox completion requires a provider message ID"
            raise ValueError(message)
        async with self._session_factory() as session:
            await _enter_fenced_tenant_session(session=session, fence=fence)
            now = await _database_now(session)
            statement = (
                select(WhatsAppOutboxRecord)
                .where(
                    col(WhatsAppOutboxRecord.id) == outbox_id,
                    col(WhatsAppOutboxRecord.user_id) == fence.user_id,
                    col(WhatsAppOutboxRecord.integration_id) == fence.integration_id,
                    col(WhatsAppOutboxRecord.status) == WhatsAppOutboxStatus.SUBMITTING.value,
                    col(WhatsAppOutboxRecord.owner_id) == fence.owner_id,
                    col(WhatsAppOutboxRecord.lease_generation) == fence.generation,
                )
                .with_for_update()
            )
            outbox = (await session.exec(statement)).one_or_none()
            if outbox is None:
                return False
            reply: IntegrationReplyRecord | None = None
            if outbox.reply_id is not None:
                reply = (
                    await session.exec(
                        select(IntegrationReplyRecord)
                        .where(
                            col(IntegrationReplyRecord.user_id) == fence.user_id,
                            col(IntegrationReplyRecord.id) == outbox.reply_id,
                        )
                        .with_for_update(),
                    )
                ).one_or_none()
                if reply is None:
                    message = "WhatsApp outbox reply record is unavailable"
                    raise WhatsAppSessionDataError(message)
            outbox.status = status.value
            outbox.owner_id = None
            outbox.lease_generation = None
            outbox.external_message_id = external_message_id
            outbox.failure_code = failure_code
            outbox.sent_at = now if status == WhatsAppOutboxStatus.SENT else None
            if status == WhatsAppOutboxStatus.QUEUED:
                outbox.submission_started_at = None
            outbox.updated_at = now
            if status == WhatsAppOutboxStatus.SENT:
                await _record_sent_outbox(
                    session=session,
                    fence=fence,
                    outbox=outbox,
                    reply=reply,
                    external_message_id=cast(str, external_message_id),
                    now=now,
                )
            elif status == WhatsAppOutboxStatus.QUEUED and reply is not None:
                reply.status = IntegrationReplyStatus.GENERATED.value
                reply.error_code = None
                reply.updated_at = now
            elif status == WhatsAppOutboxStatus.AMBIGUOUS and reply is not None:
                reply.status = IntegrationReplyStatus.AMBIGUOUS.value
                reply.error_code = failure_code or "provider_submission_ambiguous"
                reply.updated_at = now
            elif reply is not None:
                reply.status = IntegrationReplyStatus.FAILED.value
                reply.error_code = failure_code or "provider_submission_failed"
                reply.updated_at = now
            await session.commit()
        return True


class WhatsAppSessionDataStore:
    def __init__(
        self,
        *,
        session_factory: AsyncSessionFactory,
        vault: UserSecretVaultService,
        user_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._vault = vault
        self._user_id = user_id

    async def seal_session_data_key(
        self,
        *,
        credential_id: str,
        data_key: bytes,
    ) -> None:
        async with self._tenant_session() as session:
            await self._vault.seal_secret(
                session=session,
                secret=encode_session_data_key(data_key),
                reference=_data_key_reference(
                    user_id=self._user_id,
                    credential_id=credential_id,
                ),
            )
            await session.commit()

    async def delete_session_data_key(self, *, credential_id: str) -> None:
        async with self._tenant_session() as session:
            await self._vault.delete_secret(
                session=session,
                reference=_data_key_reference(
                    user_id=self._user_id,
                    credential_id=credential_id,
                ),
            )
            await session.commit()

    async def resolve_cipher(
        self,
        *,
        credential_id: str,
        fence: WhatsAppLeaseFence | None = None,
    ) -> WhatsAppSessionCipher:
        async with self._tenant_session() as session:
            if fence is not None:
                await _assert_fence(session=session, fence=fence)
            encoded = await self._vault.unseal_secret(
                session=session,
                reference=_data_key_reference(
                    user_id=self._user_id,
                    credential_id=credential_id,
                ),
            )
        if encoded is None:
            raise WhatsAppSessionDataError
        return WhatsAppSessionCipher(decode_session_data_key(encoded))

    async def get_auth_values(
        self,
        *,
        integration_id: str,
        cipher: WhatsAppSessionCipher,
        category: str,
        logical_ids: Sequence[str],
        fence: WhatsAppLeaseFence | None = None,
    ) -> dict[str, object | None]:
        _validate_auth_category(category)
        for logical_id in logical_ids:
            _validate_auth_logical_id(logical_id)
        if len(logical_ids) > MAX_AUTH_WRITES:
            message = "too many WhatsApp auth keys requested"
            raise ValueError(message)
        if not logical_ids:
            return {}
        async with self._tenant_session() as session:
            if fence is not None:
                await _assert_fence(session=session, fence=fence)
            statement = select(WhatsAppSessionKeyRecord).where(
                col(WhatsAppSessionKeyRecord.user_id) == self._user_id,
                col(WhatsAppSessionKeyRecord.integration_id) == integration_id,
                col(WhatsAppSessionKeyRecord.key_category) == category,
                col(WhatsAppSessionKeyRecord.logical_key_id).in_(
                    tuple(logical_ids),
                ),
            )
            records = (await session.exec(statement)).all()
        by_id = {record.logical_key_id: record for record in records}
        values: dict[str, object | None] = {}
        for logical_id in logical_ids:
            record = by_id.get(logical_id)
            values[logical_id] = None if record is None else _decrypt_json_value(cipher=cipher, record=record)
        return values

    async def set_auth_values(
        self,
        *,
        integration_id: str,
        cipher: WhatsAppSessionCipher,
        writes: Sequence[WhatsAppAuthWrite],
        fence: WhatsAppLeaseFence | None = None,
    ) -> None:
        if len(writes) > MAX_AUTH_WRITES:
            message = "too many WhatsApp auth keys supplied"
            raise ValueError(message)
        keys: set[tuple[str, str]] = set()
        for write in writes:
            _validate_auth_category(write.category)
            _validate_auth_logical_id(write.logical_id)
            key = (write.category, write.logical_id)
            if key in keys:
                message = "duplicate WhatsApp auth key write"
                raise ValueError(message)
            keys.add(key)
        async with self._tenant_session() as session:
            if fence is not None:
                await _assert_fence(session=session, fence=fence)
            if not writes:
                return
            statement = (
                select(WhatsAppSessionKeyRecord)
                .where(
                    col(WhatsAppSessionKeyRecord.user_id) == self._user_id,
                    col(WhatsAppSessionKeyRecord.integration_id) == integration_id,
                    tuple_(
                        col(WhatsAppSessionKeyRecord.key_category),
                        col(WhatsAppSessionKeyRecord.logical_key_id),
                    ).in_(tuple(keys)),
                )
                .with_for_update()
            )
            records = (await session.exec(statement)).all()
            records_by_key = {(record.key_category, record.logical_key_id): record for record in records}
            now = await _database_now(session)
            for write in writes:
                record = records_by_key.get((write.category, write.logical_id))
                if _is_deleted_auth_value(write.value):
                    if record is not None:
                        await session.delete(record)
                    continue
                revision = 1 if record is None else record.revision + 1
                plaintext = _encode_json_value(write.value)
                encrypted = cipher.encrypt(
                    plaintext=plaintext,
                    context=WhatsAppCipherContext(
                        user_id=self._user_id,
                        integration_id=integration_id,
                        key_category=write.category,
                        logical_key_id=write.logical_id,
                        revision=revision,
                    ),
                )
                if record is None:
                    session.add(
                        WhatsAppSessionKeyRecord(
                            user_id=self._user_id,
                            integration_id=integration_id,
                            key_category=write.category,
                            logical_key_id=write.logical_id,
                            ciphertext=encrypted.ciphertext,
                            nonce=encrypted.nonce,
                            revision=revision,
                            created_at=now,
                            updated_at=now,
                        ),
                    )
                    continue
                record.ciphertext = encrypted.ciphertext
                record.nonce = encrypted.nonce
                record.revision = revision
                record.updated_at = now
            await session.commit()

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
        if not value or len(value) > 4096:
            message = "WhatsApp challenge value is invalid"
            raise ValueError(message)
        if request_revision < 1:
            message = "WhatsApp challenge request revision is invalid"
            raise ValueError(message)
        if method != requested_method:
            message = "WhatsApp challenge method does not match the request"
            raise ValueError(message)
        async with self._tenant_session() as session:
            if fence is not None:
                await _assert_fence(session=session, fence=fence)
            statement = (
                select(WhatsAppSessionRecord)
                .where(
                    col(WhatsAppSessionRecord.user_id) == self._user_id,
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                )
                .with_for_update()
            )
            record = (await session.exec(statement)).one_or_none()
            if record is None:
                raise WhatsAppSessionDataError
            if record.state == WhatsAppSessionState.CONNECTED.value:
                return
            if record.link_method != requested_method.value or record.challenge_request_revision != request_revision:
                return
            if (
                record.desired_state != WhatsAppDesiredState.CONNECTED.value
                or record.state == WhatsAppSessionState.DISCONNECTING.value
            ):
                raise WhatsAppSessionDataError
            revision = record.challenge_revision + 1
            encrypted = cipher.encrypt(
                plaintext=value.encode("utf-8"),
                context=WhatsAppCipherContext(
                    user_id=self._user_id,
                    integration_id=integration_id,
                    key_category=CHALLENGE_KEY_CATEGORY,
                    logical_key_id=method.value,
                    revision=revision,
                ),
            )
            now = await _database_now(session)
            record.challenge_ciphertext = encrypted.ciphertext
            record.challenge_nonce = encrypted.nonce
            record.challenge_revision = revision
            record.challenge_expires_at = _as_utc(expires_at)
            record.state = WhatsAppSessionState.AWAITING_LINK.value
            record.failure_code = None
            record.updated_at = now
            await session.commit()

    async def get_challenge(
        self,
        *,
        integration_id: str,
        cipher: WhatsAppSessionCipher,
    ) -> StoredWhatsAppChallenge | None:
        async with self._tenant_session() as session:
            statement = select(WhatsAppSessionRecord).where(
                col(WhatsAppSessionRecord.user_id) == self._user_id,
                col(WhatsAppSessionRecord.integration_id) == integration_id,
            )
            record = (await session.exec(statement)).one_or_none()
            now = await _database_now(session)
        if (
            record is None
            or record.challenge_ciphertext is None
            or record.challenge_nonce is None
            or record.challenge_expires_at is None
            or _as_utc(record.challenge_expires_at) <= now
        ):
            return None
        method = WhatsAppLinkMethod(record.link_method)
        value = cipher.decrypt(
            encrypted=WhatsAppEncryptedValue(
                ciphertext=record.challenge_ciphertext,
                nonce=record.challenge_nonce,
                revision=record.challenge_revision,
            ),
            context=WhatsAppCipherContext(
                user_id=self._user_id,
                integration_id=integration_id,
                key_category=CHALLENGE_KEY_CATEGORY,
                logical_key_id=method.value,
                revision=record.challenge_revision,
            ),
        ).decode("utf-8")
        return StoredWhatsAppChallenge(
            method=method,
            value=value,
            expires_at=_as_utc(record.challenge_expires_at),
        )

    async def clear_auth_state(
        self,
        *,
        integration_id: str,
        fence: WhatsAppLeaseFence | None = None,
    ) -> None:
        async with self._tenant_session() as session:
            if fence is not None:
                await _assert_fence(session=session, fence=fence)
            await session.exec(
                delete(WhatsAppSessionKeyRecord).where(
                    col(WhatsAppSessionKeyRecord.user_id) == self._user_id,
                    col(WhatsAppSessionKeyRecord.integration_id) == integration_id,
                ),
            )
            statement = (
                select(WhatsAppSessionRecord)
                .where(
                    col(WhatsAppSessionRecord.user_id) == self._user_id,
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                )
                .with_for_update()
            )
            record = (await session.exec(statement)).one_or_none()
            if record is not None:
                record.challenge_ciphertext = None
                record.challenge_nonce = None
                record.challenge_expires_at = None
                record.validation_fingerprint = None
                record.validated_at = None
                record.connected_at = None
                record.updated_at = await _database_now(session)
            await session.commit()

    @asynccontextmanager
    async def _tenant_session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            await set_tenant_user_id(session=session, user_id=self._user_id)
            yield session


async def _record_sent_outbox(
    *,
    session: AsyncSession,
    fence: WhatsAppLeaseFence,
    outbox: WhatsAppOutboxRecord,
    reply: IntegrationReplyRecord | None,
    external_message_id: str,
    now: datetime,
) -> None:
    inbound_message: IntegrationMessageRecord | None = None
    if reply is not None:
        inbound_message = await session.get(
            IntegrationMessageRecord,
            reply.inbound_message_id,
        )
    resource = await session.get(
        IntegrationResourceRecord,
        outbox.resource_id,
    )
    if (
        resource is None
        or resource.user_id != fence.user_id
        or resource.integration_id != fence.integration_id
        or resource.provider != IntegrationProviderType.WHATSAPP.value
        or (
            reply is not None
            and (
                inbound_message is None
                or inbound_message.user_id != fence.user_id
                or inbound_message.integration_id != fence.integration_id
            )
        )
    ):
        message = "WhatsApp outbox delivery context is unavailable"
        raise WhatsAppSessionDataError(message)
    target_phone_number = (
        inbound_message.from_number
        if inbound_message is not None
        else phone_number_from_whatsapp_pn_jid(outbox.target_jid)
    )
    if target_phone_number is None:
        message = "WhatsApp outbox delivery target is unavailable"
        raise WhatsAppSessionDataError(message)
    outbound_message = IntegrationMessageRecord(
        user_id=fence.user_id,
        integration_id=fence.integration_id,
        resource_id=outbox.resource_id,
        provider=IntegrationProviderType.WHATSAPP.value,
        direction=IntegrationDirection.OUTBOUND.value,
        external_message_id=external_message_id,
        from_number=resource.display_name,
        to_number=target_phone_number,
        body=outbox.body,
        status=IntegrationMessageStatus.SENT.value,
        sent_at=now,
        payload_json={"remote_jid": outbox.target_jid},
        created_at=now,
        updated_at=now,
    )
    session.add(outbound_message)
    await session.flush()
    if reply is not None:
        reply.outbound_message_id = outbound_message.id
        reply.status = IntegrationReplyStatus.PROVIDER_ACCEPTED.value
        reply.error_code = None
        reply.updated_at = now
    session.add(
        IntegrationAuditLogRecord(
            user_id=fence.user_id,
            integration_id=fence.integration_id,
            provider=IntegrationProviderType.WHATSAPP.value,
            action=IntegrationAuditAction.WHATSAPP_SENT.value,
            actor_type=IntegrationAuditActorType.AGENT.value,
            actor_id=reply.agent_run_id if reply is not None else None,
            outcome=IntegrationAuditOutcome.SUCCEEDED.value,
            safe_metadata={
                "resource_id": outbox.resource_id,
                "outbound_message_id": outbound_message.id,
                **({"reply_id": reply.id} if reply is not None else {"outbox_id": outbox.id}),
            },
            created_at=now,
        ),
    )


def _data_key_reference(
    *,
    user_id: str,
    credential_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider="whatsapp",
        credential_id=credential_id,
        purpose=SecretPurpose.WHATSAPP_SESSION_DATA_KEY,
    )


async def _enter_fenced_tenant_session(
    *,
    session: AsyncSession,
    fence: WhatsAppLeaseFence,
) -> None:
    await set_tenant_user_id(session=session, user_id=fence.user_id)
    await _assert_fence(session=session, fence=fence)


async def _assert_fence(
    *,
    session: AsyncSession,
    fence: WhatsAppLeaseFence,
) -> None:
    now = await _database_now(session)
    timestamp = func.clock_timestamp() if session.get_bind().dialect.name == "postgresql" else now
    statement = (
        select(WhatsAppSessionLeaseRecord.integration_id)
        .where(
            col(WhatsAppSessionLeaseRecord.integration_id) == fence.integration_id,
            col(WhatsAppSessionLeaseRecord.user_id) == fence.user_id,
            col(WhatsAppSessionLeaseRecord.owner_id) == fence.owner_id,
            col(WhatsAppSessionLeaseRecord.generation) == fence.generation,
            col(WhatsAppSessionLeaseRecord.lease_expires_at) > timestamp,
        )
        .with_for_update()
    )
    if (await session.exec(statement)).one_or_none() is None:
        raise WhatsAppLeaseLostError


def _demand_from_record(record: WhatsAppSessionRecord) -> WhatsAppSessionDemand:
    return WhatsAppSessionDemand(
        user_id=record.user_id,
        integration_id=record.integration_id,
        resource_id=record.resource_id,
        source_resource_id=record.source_resource_id,
        credential_id=record.credential_id,
        desired_state=WhatsAppDesiredState(record.desired_state),
        state=WhatsAppSessionState(record.state),
        link_method=WhatsAppLinkMethod(record.link_method),
        challenge_request_revision=record.challenge_request_revision,
        failure_code=record.failure_code,
    )


def _decrypt_json_value(
    *,
    cipher: WhatsAppSessionCipher,
    record: WhatsAppSessionKeyRecord,
) -> object:
    plaintext = cipher.decrypt(
        encrypted=WhatsAppEncryptedValue(
            ciphertext=record.ciphertext,
            nonce=record.nonce,
            revision=record.revision,
        ),
        context=WhatsAppCipherContext(
            user_id=record.user_id,
            integration_id=record.integration_id,
            key_category=record.key_category,
            logical_key_id=record.logical_key_id,
            revision=record.revision,
        ),
    )
    try:
        return orjson.loads(plaintext)
    except orjson.JSONDecodeError as error:
        raise WhatsAppSessionDataError from error


def _encode_json_value(value: object) -> bytes:
    try:
        return orjson.dumps(value)
    except TypeError as error:
        raise WhatsAppSessionDataError from error


def _is_deleted_auth_value(value: object | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    return dict(value) == {"$voxos_type": "undefined"}


def _validate_auth_category(value: str) -> None:
    if not value or len(value) > MAX_AUTH_KEY_CATEGORY_LENGTH:
        message = "WhatsApp auth key category is invalid"
        raise ValueError(message)


def _validate_auth_logical_id(value: str) -> None:
    if not value or len(value) > MAX_AUTH_LOGICAL_KEY_ID_LENGTH:
        message = "WhatsApp auth logical key id is invalid"
        raise ValueError(message)


async def _database_now(session: AsyncSession) -> datetime:
    if session.get_bind().dialect.name == "sqlite":
        result = await session.exec(select(func.julianday("now")))
        julian_day = float(result.one())
        unix_seconds = (julian_day - 2_440_587.5) * 86_400
        return datetime.fromtimestamp(unix_seconds, tz=UTC)
    result = await session.exec(select(func.clock_timestamp()))
    value = cast(datetime, result.one())
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

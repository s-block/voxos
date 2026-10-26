from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import HttpUrl
from sqlalchemy import or_
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.codex_cloud.models import (
    CODEX_CLOUD_QUEUE,
    CodexAccountResponse,
    CodexAccountStatus,
    CodexAuthSessionResponse,
    CodexAuthSessionStatus,
    CodexCloudRemoteStatus,
    CodexCloudSubmissionState,
    CodexCloudTaskRunPayload,
)
from voxos.codex_cloud.records import (
    CodexAccountRecord,
    CodexAuthSessionRecord,
    CodexCloudTaskRecord,
)
from voxos.codex_cloud.sandbox import CodexLoginResult
from voxos.codex_cloud.secrets import CodexAccountSecretStore, OpaqueCodexAuth
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.text.run_chat_service import RunChatService

ACTIVE_AUTH_SESSION_STATUSES = (
    CodexAuthSessionStatus.QUEUED.value,
    CodexAuthSessionStatus.WAITING_FOR_USER.value,
)
LOGIN_IN_PROGRESS_MESSAGE = "A Codex login is already in progress"
LOGIN_LEASE_LOST_MESSAGE = "Codex login lease was lost"
CONNECTED_ACCOUNT_NOT_FOUND_MESSAGE = "Connected Codex account was not found"
AUTH_MISSING_MESSAGE = "Codex authentication is missing"
ACCOUNT_NOT_FOUND_MESSAGE = "Codex account was not found"
ACCOUNT_OPERATION_CONFLICT_MESSAGE = "Another Codex account operation is in progress"
AUTH_SESSION_NOT_FOUND_MESSAGE = "Codex login session was not found"
TASK_NOT_FOUND_MESSAGE = "Codex Cloud task was not found"


class CodexCloudConflictError(ValueError):
    pass


class CodexCloudNotFoundError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CodexCloudStore:
    session_factory: AsyncSessionFactory
    secret_store: CodexAccountSecretStore
    queue_session_factory: AsyncSessionFactory | None = None

    async def account_response(self, *, user_id: str) -> CodexAccountResponse:
        async with self.session_factory() as session:
            account = await self._account(session=session, user_id=user_id)
            return account_response_from_record(account)

    async def create_auth_session(
        self,
        *,
        user_id: str,
        expires_at: datetime,
    ) -> CodexAuthSessionRecord:
        now = utc_now()
        async with self.session_factory() as session:
            active = await session.exec(
                select(CodexAuthSessionRecord).where(
                    col(CodexAuthSessionRecord.user_id) == user_id,
                    col(CodexAuthSessionRecord.status).in_(ACTIVE_AUTH_SESSION_STATUSES),
                    col(CodexAuthSessionRecord.expires_at) > now,
                ),
            )
            if active.first() is not None:
                raise CodexCloudConflictError(LOGIN_IN_PROGRESS_MESSAGE)
            account = await self._account(session=session, user_id=user_id)
            record = CodexAuthSessionRecord(
                user_id=user_id,
                account_id=account.id if account is not None else None,
                expires_at=expires_at,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def get_auth_session(
        self,
        *,
        user_id: str,
        auth_session_id: str,
    ) -> CodexAuthSessionRecord | None:
        async with self.session_factory() as session:
            record = await session.get(CodexAuthSessionRecord, auth_session_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status in ACTIVE_AUTH_SESSION_STATUSES and _aware_datetime(record.expires_at) <= utc_now():
                record.status = CodexAuthSessionStatus.EXPIRED.value
                record.lease_owner = None
                record.lease_expires_at = None
                record.updated_at = utc_now()
                session.add(record)
                await session.commit()
                await session.refresh(record)
            return record

    async def claim_auth_sessions(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> tuple[CodexAuthSessionRecord, ...]:
        now = utc_now()
        statement = (
            select(CodexAuthSessionRecord)
            .where(
                col(CodexAuthSessionRecord.status) == CodexAuthSessionStatus.QUEUED.value,
                col(CodexAuthSessionRecord.expires_at) > now,
                or_(
                    col(CodexAuthSessionRecord.lease_expires_at).is_(None),
                    col(CodexAuthSessionRecord.lease_expires_at) < now,
                ),
            )
            .order_by(col(CodexAuthSessionRecord.created_at).asc())
            .limit(batch_size)
        )
        async with self._queue_factory() as session:
            if session.get_bind().dialect.name != "sqlite":
                statement = statement.with_for_update(skip_locked=True)
            result = await session.exec(statement)
            records = tuple(result.all())
            for record in records:
                record.lease_owner = worker_id
                record.lease_expires_at = now + timedelta(seconds=lease_seconds)
                record.updated_at = now
                session.add(record)
            await session.commit()
            for record in records:
                await session.refresh(record)
            return records

    async def set_auth_challenge(
        self,
        *,
        user_id: str,
        auth_session_id: str,
        worker_id: str,
        login_id: str,
        verification_url: str,
        user_code: str,
    ) -> None:
        async with self.session_factory() as session:
            record = await self._auth_session(session, user_id, auth_session_id)
            if record.lease_owner != worker_id:
                raise CodexCloudConflictError(LOGIN_LEASE_LOST_MESSAGE)
            record.status = CodexAuthSessionStatus.WAITING_FOR_USER.value
            record.login_id = login_id
            record.verification_url = verification_url
            record.user_code = user_code
            record.updated_at = utc_now()
            session.add(record)
            await session.commit()

    async def is_auth_cancelled(self, *, user_id: str, auth_session_id: str) -> bool:
        record = await self.get_auth_session(user_id=user_id, auth_session_id=auth_session_id)
        return record is None or record.status in {
            CodexAuthSessionStatus.CANCELLED.value,
            CodexAuthSessionStatus.EXPIRED.value,
        }

    async def cancel_auth_session(self, *, user_id: str, auth_session_id: str) -> CodexAuthSessionRecord:
        async with self.session_factory() as session:
            record = await self._auth_session(session, user_id, auth_session_id)
            if record.status in ACTIVE_AUTH_SESSION_STATUSES:
                record.status = CodexAuthSessionStatus.CANCELLED.value
                record.updated_at = utc_now()
                session.add(record)
                await session.commit()
                await session.refresh(record)
            return record

    async def complete_auth_session(
        self,
        *,
        user_id: str,
        auth_session_id: str,
        worker_id: str,
        result: CodexLoginResult,
    ) -> CodexAccountRecord:
        now = utc_now()
        async with self.session_factory() as session:
            auth_session = await self._auth_session(session, user_id, auth_session_id)
            if auth_session.lease_owner != worker_id:
                raise CodexCloudConflictError(LOGIN_LEASE_LOST_MESSAGE)
            account = await self._account(session=session, user_id=user_id, for_update=True)
            if account is None:
                account = CodexAccountRecord(user_id=user_id)
            else:
                account.operation_generation += 1
                account.operation_lease_owner = None
                account.operation_lease_expires_at = None
            account.status = CodexAccountStatus.CONNECTED.value
            account.email = result.email
            account.plan_type = result.plan_type
            account.authenticated_at = now
            account.updated_at = now
            session.add(account)
            await session.flush()
            await self.secret_store.seal_auth(
                session=session,
                user_id=user_id,
                account_id=account.id,
                auth=OpaqueCodexAuth(result.auth_json),
            )
            auth_session.account_id = account.id
            auth_session.status = CodexAuthSessionStatus.SUCCEEDED.value
            auth_session.lease_owner = None
            auth_session.lease_expires_at = None
            auth_session.updated_at = now
            session.add(auth_session)
            await session.commit()
            await session.refresh(account)
            return account

    async def fail_auth_session(
        self,
        *,
        user_id: str,
        auth_session_id: str,
        error_message: str,
    ) -> None:
        async with self.session_factory() as session:
            record = await self._auth_session(session, user_id, auth_session_id)
            if record.status not in ACTIVE_AUTH_SESSION_STATUSES:
                return
            record.status = CodexAuthSessionStatus.FAILED.value
            record.error_message = error_message[:1024]
            record.lease_owner = None
            record.lease_expires_at = None
            record.updated_at = utc_now()
            session.add(record)
            await session.commit()

    async def update_environment(
        self,
        *,
        user_id: str,
        environment_id: str,
        environment_label: str | None,
    ) -> CodexAccountRecord:
        async with self.session_factory() as session:
            account = await self._require_account(session=session, user_id=user_id)
            account.environment_id = environment_id
            account.environment_label = environment_label
            account.updated_at = utc_now()
            session.add(account)
            await session.commit()
            await session.refresh(account)
            return account

    async def disconnect_account(self, *, user_id: str) -> None:
        async with self.session_factory() as session:
            account = await self._require_account(session=session, user_id=user_id)
            await self.secret_store.delete_auth(
                session=session,
                user_id=user_id,
                account_id=account.id,
            )
            account.status = CodexAccountStatus.REAUTHENTICATION_REQUIRED.value
            account.email = None
            account.plan_type = None
            account.operation_lease_owner = None
            account.operation_lease_expires_at = None
            account.updated_at = utc_now()
            session.add(account)
            await session.commit()

    async def load_account_auth(
        self,
        *,
        user_id: str,
        account_id: str,
    ) -> OpaqueCodexAuth:
        async with self.session_factory() as session:
            account = await self._require_account(session=session, user_id=user_id)
            if account.id != account_id or account.status != CodexAccountStatus.CONNECTED.value:
                raise CodexCloudNotFoundError(CONNECTED_ACCOUNT_NOT_FOUND_MESSAGE)
            auth = await self.secret_store.load_auth(
                session=session,
                user_id=user_id,
                account_id=account_id,
            )
            if auth is None:
                raise CodexCloudNotFoundError(AUTH_MISSING_MESSAGE)
            return auth

    async def persist_refreshed_auth(
        self,
        *,
        user_id: str,
        account_id: str,
        auth: OpaqueCodexAuth,
        worker_id: str,
        generation: int,
    ) -> None:
        async with self.session_factory() as session:
            account = await self._require_account(
                session=session,
                user_id=user_id,
                for_update=True,
            )
            if (
                account.id != account_id
                or account.operation_lease_owner != worker_id
                or account.operation_generation != generation
            ):
                raise CodexCloudConflictError(LOGIN_LEASE_LOST_MESSAGE)
            await self.secret_store.seal_auth(
                session=session,
                user_id=user_id,
                account_id=account_id,
                auth=auth,
            )
            account.updated_at = utc_now()
            session.add(account)
            await session.commit()

    async def acquire_account_lease(
        self,
        *,
        user_id: str,
        account_id: str,
        worker_id: str,
        lease_seconds: int,
    ) -> int:
        now = utc_now()
        async with self.session_factory() as session:
            account = await self._require_account(session=session, user_id=user_id, for_update=True)
            if account.id != account_id:
                raise CodexCloudNotFoundError(ACCOUNT_NOT_FOUND_MESSAGE)
            if (
                account.operation_lease_owner not in {None, worker_id}
                and account.operation_lease_expires_at is not None
                and _aware_datetime(account.operation_lease_expires_at) > now
            ):
                raise CodexCloudConflictError(ACCOUNT_OPERATION_CONFLICT_MESSAGE)
            account.operation_generation += 1
            account.operation_lease_owner = worker_id
            account.operation_lease_expires_at = now + timedelta(seconds=lease_seconds)
            account.updated_at = now
            session.add(account)
            await session.commit()
            return account.operation_generation

    async def release_account_lease(
        self,
        *,
        user_id: str,
        account_id: str,
        worker_id: str,
        generation: int,
    ) -> None:
        async with self.session_factory() as session:
            account = await self._require_account(session=session, user_id=user_id, for_update=True)
            if (
                account.id == account_id
                and account.operation_lease_owner == worker_id
                and account.operation_generation == generation
            ):
                account.operation_lease_owner = None
                account.operation_lease_expires_at = None
                account.updated_at = utc_now()
                session.add(account)
                await session.commit()

    async def create_task_run(
        self,
        *,
        run_store: SqlModelAgentRunStore,
        run_chat_service: RunChatService,
        user_id: str,
        agent_id: str,
        chat_id: str | None,
        payload: CodexCloudTaskRunPayload,
        idempotency_key: str | None,
    ) -> tuple[CodexCloudTaskRecord, AgentRunRecord]:
        async with self.session_factory() as session:
            run: AgentRunRecord | None = None
            if idempotency_key is not None:
                existing_run = await session.exec(
                    select(AgentRunRecord).where(
                        col(AgentRunRecord.user_id) == user_id,
                        col(AgentRunRecord.idempotency_key) == idempotency_key,
                        col(AgentRunRecord.deleted_at).is_(None),
                    ),
                )
                run = existing_run.one_or_none()
            if run is None:
                chat = await run_chat_service.resolve_or_create_chat_in_session(
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    chat_id=chat_id,
                    title=codex_task_chat_title(payload.prompt),
                )
                run = await run_store.enqueue_run_in_session(
                    session=session,
                    user_id=user_id,
                    agent_id=agent_id,
                    kind=AgentRunKind.CODEX_CLOUD_TASK,
                    payload_json=payload.model_dump(mode="json"),
                    created_by_user_id=user_id,
                    session_id=chat.id,
                    chat_id=chat.id,
                    queue=CODEX_CLOUD_QUEUE,
                    max_attempts=3,
                    idempotency_key=idempotency_key,
                )
            result = await session.exec(
                select(CodexCloudTaskRecord).where(
                    col(CodexCloudTaskRecord.user_id) == user_id,
                    col(CodexCloudTaskRecord.agent_run_id) == run.id,
                ),
            )
            task = result.first()
            if task is None:
                task = CodexCloudTaskRecord(
                    user_id=user_id,
                    account_id=payload.account_id,
                    agent_run_id=run.id,
                    environment_id=payload.environment_id,
                    branch=payload.branch,
                    attempts=payload.attempts,
                )
                session.add(task)
            await session.commit()
            await session.refresh(run)
            await session.refresh(task)
            return task, run

    async def get_task_by_run(
        self,
        *,
        user_id: str,
        agent_run_id: str,
    ) -> CodexCloudTaskRecord | None:
        async with self.session_factory() as session:
            result = await session.exec(
                select(CodexCloudTaskRecord).where(
                    col(CodexCloudTaskRecord.user_id) == user_id,
                    col(CodexCloudTaskRecord.agent_run_id) == agent_run_id,
                ),
            )
            return result.first()

    async def get_task(
        self,
        *,
        user_id: str,
        task_id: str,
    ) -> CodexCloudTaskRecord | None:
        async with self.session_factory() as session:
            record = await session.get(CodexCloudTaskRecord, task_id)
            if record is None or record.user_id != user_id:
                return None
            return record

    async def list_tasks(self, *, user_id: str, limit: int = 100) -> tuple[CodexCloudTaskRecord, ...]:
        async with self.session_factory() as session:
            result = await session.exec(
                select(CodexCloudTaskRecord)
                .where(col(CodexCloudTaskRecord.user_id) == user_id)
                .order_by(col(CodexCloudTaskRecord.created_at).desc())
                .limit(limit),
            )
            return tuple(result.all())

    async def update_task_submission(
        self,
        *,
        user_id: str,
        task_id: str,
        submission_state: CodexCloudSubmissionState,
        remote_task_id: str | None = None,
        remote_task_url: str | None = None,
        remote_status: CodexCloudRemoteStatus | None = None,
        summary: str | None = None,
        remote_updated_at: datetime | None = None,
    ) -> CodexCloudTaskRecord:
        now = utc_now()
        async with self.session_factory() as session:
            record = await self._task(session, user_id, task_id)
            record.submission_state = submission_state.value
            if remote_task_id is not None:
                record.remote_task_id = remote_task_id
            if remote_task_url is not None:
                record.remote_task_url = remote_task_url
            if remote_status is not None:
                record.remote_status = remote_status.value
            if summary is not None:
                record.summary = summary[:4096]
            if submission_state == CodexCloudSubmissionState.SUBMITTED and record.submitted_at is None:
                record.submitted_at = now
            record.remote_updated_at = remote_updated_at or record.remote_updated_at
            record.updated_at = now
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return record

    async def _account(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        for_update: bool = False,
    ) -> CodexAccountRecord | None:
        statement = select(CodexAccountRecord).where(col(CodexAccountRecord.user_id) == user_id)
        if for_update and session.get_bind().dialect.name != "sqlite":
            statement = statement.with_for_update()
        result = await session.exec(statement)
        return result.first()

    async def _require_account(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        for_update: bool = False,
    ) -> CodexAccountRecord:
        statement = select(CodexAccountRecord).where(col(CodexAccountRecord.user_id) == user_id)
        if for_update and session.get_bind().dialect.name != "sqlite":
            statement = statement.with_for_update()
        result = await session.exec(statement)
        account = result.first()
        if account is None:
            raise CodexCloudNotFoundError(ACCOUNT_NOT_FOUND_MESSAGE)
        return account

    async def _auth_session(
        self,
        session: AsyncSession,
        user_id: str,
        auth_session_id: str,
    ) -> CodexAuthSessionRecord:
        record = await session.get(CodexAuthSessionRecord, auth_session_id)
        if record is None or record.user_id != user_id:
            raise CodexCloudNotFoundError(AUTH_SESSION_NOT_FOUND_MESSAGE)
        return record

    async def _task(
        self,
        session: AsyncSession,
        user_id: str,
        task_id: str,
    ) -> CodexCloudTaskRecord:
        record = await session.get(CodexCloudTaskRecord, task_id)
        if record is None or record.user_id != user_id:
            raise CodexCloudNotFoundError(TASK_NOT_FOUND_MESSAGE)
        return record

    @property
    def _queue_factory(self) -> AsyncSessionFactory:
        return self.queue_session_factory or self.session_factory


def account_response_from_record(record: CodexAccountRecord | None) -> CodexAccountResponse:
    if record is None or record.status != CodexAccountStatus.CONNECTED.value:
        return CodexAccountResponse(connected=False)
    return CodexAccountResponse(
        connected=True,
        id=record.id,
        status=CodexAccountStatus(record.status),
        email=record.email,
        plan_type=record.plan_type,
        environment_id=record.environment_id,
        environment_label=record.environment_label,
        authenticated_at=record.authenticated_at,
        updated_at=record.updated_at,
    )


def auth_session_response_from_record(record: CodexAuthSessionRecord) -> CodexAuthSessionResponse:
    return CodexAuthSessionResponse(
        id=record.id,
        status=CodexAuthSessionStatus(record.status),
        verification_url=(HttpUrl(record.verification_url) if record.verification_url is not None else None),
        user_code=record.user_code,
        error_message=record.error_message,
        expires_at=record.expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def codex_task_chat_title(prompt: str) -> str:
    compacted = " ".join(prompt.split())
    if len(compacted) <= 57:
        return f"Codex: {compacted}"
    return f"Codex: {compacted[:54]}..."


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

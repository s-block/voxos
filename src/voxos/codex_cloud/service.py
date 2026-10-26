from dataclasses import dataclass
from datetime import timedelta

from pydantic import HttpUrl

from voxos.agent_runtime.run_models import AgentRunDetail
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.codex_cloud.models import (
    CodexAccountResponse,
    CodexAuthSessionResponse,
    CodexCloudRemoteStatus,
    CodexCloudSubmissionState,
    CodexCloudTaskCreateRequest,
    CodexCloudTaskListResponse,
    CodexCloudTaskResponse,
    CodexCloudTaskRunPayload,
)
from voxos.codex_cloud.records import CodexCloudTaskRecord
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.codex_cloud.store import (
    CodexCloudNotFoundError,
    CodexCloudStore,
    account_response_from_record,
    auth_session_response_from_record,
)
from voxos.db.models import utc_now
from voxos.text.run_chat_service import RunChatService

AUTH_SESSION_NOT_FOUND_MESSAGE = "Codex login session was not found"
ACCOUNT_REQUIRED_MESSAGE = "Connect a Codex account before submitting a task"
ENVIRONMENT_REQUIRED_MESSAGE = "Save a Codex Cloud environment ID before submitting a task"
CREATED_RUN_NOT_FOUND_MESSAGE = "Created Codex run was not found"
DISABLED_MESSAGE = "Codex Cloud integration is disabled"


class CodexCloudDisabledError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CodexCloudService:
    store: CodexCloudStore
    run_store: SqlModelAgentRunStore
    run_chat_service: RunChatService
    settings: CodexCloudSettings

    async def account(self, *, user_id: str) -> CodexAccountResponse:
        self._require_enabled()
        return await self.store.account_response(user_id=user_id)

    async def start_login(self, *, user_id: str) -> CodexAuthSessionResponse:
        self._require_enabled()
        record = await self.store.create_auth_session(
            user_id=user_id,
            expires_at=utc_now() + timedelta(seconds=self.settings.login_timeout_seconds),
        )
        return auth_session_response_from_record(record)

    async def auth_session(
        self,
        *,
        user_id: str,
        auth_session_id: str,
    ) -> CodexAuthSessionResponse:
        self._require_enabled()
        record = await self.store.get_auth_session(
            user_id=user_id,
            auth_session_id=auth_session_id,
        )
        if record is None:
            raise CodexCloudNotFoundError(AUTH_SESSION_NOT_FOUND_MESSAGE)
        return auth_session_response_from_record(record)

    async def cancel_auth_session(
        self,
        *,
        user_id: str,
        auth_session_id: str,
    ) -> CodexAuthSessionResponse:
        self._require_enabled()
        record = await self.store.cancel_auth_session(
            user_id=user_id,
            auth_session_id=auth_session_id,
        )
        return auth_session_response_from_record(record)

    async def update_environment(
        self,
        *,
        user_id: str,
        environment_id: str,
        environment_label: str | None,
    ) -> CodexAccountResponse:
        self._require_enabled()
        account = await self.store.update_environment(
            user_id=user_id,
            environment_id=environment_id,
            environment_label=environment_label,
        )
        return account_response_from_record(account)

    async def disconnect(self, *, user_id: str) -> None:
        self._require_enabled()
        await self.store.disconnect_account(user_id=user_id)

    async def submit_task(
        self,
        *,
        user_id: str,
        request: CodexCloudTaskCreateRequest,
    ) -> CodexCloudTaskResponse:
        self._require_enabled()
        await self.run_chat_service.require_agent(
            user_id=user_id,
            agent_id=request.agent_id,
        )
        account = await self.store.account_response(user_id=user_id)
        if not account.connected or account.id is None:
            raise CodexCloudNotFoundError(ACCOUNT_REQUIRED_MESSAGE)
        if account.environment_id is None:
            raise ValueError(ENVIRONMENT_REQUIRED_MESSAGE)
        payload = CodexCloudTaskRunPayload(
            account_id=account.id,
            environment_id=account.environment_id,
            prompt=request.prompt,
            branch=request.branch,
            attempts=request.attempts,
        )
        task, run = await self.store.create_task_run(
            run_store=self.run_store,
            run_chat_service=self.run_chat_service,
            user_id=user_id,
            agent_id=request.agent_id,
            chat_id=request.chat_id,
            payload=payload,
            idempotency_key=(f"codex-cloud:{request.idempotency_key}" if request.idempotency_key is not None else None),
        )
        detail = await self.run_store.get_run_detail(user_id=user_id, run_id=run.id)
        if detail is None:
            raise CodexCloudNotFoundError(CREATED_RUN_NOT_FOUND_MESSAGE)
        return task_response_from_records(task, detail)

    async def list_tasks(self, *, user_id: str, limit: int = 100) -> CodexCloudTaskListResponse:
        self._require_enabled()
        records = await self.store.list_tasks(user_id=user_id, limit=limit)
        tasks: list[CodexCloudTaskResponse] = []
        for record in records:
            run = await self.run_store.get_run_detail(user_id=user_id, run_id=record.agent_run_id)
            if run is not None:
                tasks.append(task_response_from_records(record, run))
        return CodexCloudTaskListResponse(tasks=tuple(tasks))

    def _require_enabled(self) -> None:
        if not self.settings.enabled:
            raise CodexCloudDisabledError(DISABLED_MESSAGE)


def task_response_from_records(
    task: CodexCloudTaskRecord,
    run: AgentRunDetail,
) -> CodexCloudTaskResponse:
    return CodexCloudTaskResponse(
        id=task.id,
        run=run,
        environment_id=task.environment_id,
        branch=task.branch,
        attempts=task.attempts,
        submission_state=CodexCloudSubmissionState(task.submission_state),
        remote_task_id=task.remote_task_id,
        remote_task_url=(HttpUrl(task.remote_task_url) if task.remote_task_url is not None else None),
        remote_status=CodexCloudRemoteStatus(task.remote_status),
        summary=task.summary,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )

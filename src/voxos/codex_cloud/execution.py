import asyncio
import logging
import re
from dataclasses import dataclass

import orjson
from pydantic import TypeAdapter, ValidationError

from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_execution import AgentRunExecutionResult
from voxos.agent_runtime.run_models import (
    AgentRunEventKind,
    AgentRunFailureCategory,
    AgentRunKind,
)
from voxos.agent_runtime.run_store import AgentRunCancelledError, SqlModelAgentRunStore
from voxos.codex_cloud.models import (
    CodexCloudListResponse,
    CodexCloudListTask,
    CodexCloudRemoteStatus,
    CodexCloudSubmissionState,
    CodexCloudTaskRunPayload,
)
from voxos.codex_cloud.sandbox import (
    CodexSandboxError,
    CodexSandboxResult,
    DockerCodexSandboxRunner,
)
from voxos.codex_cloud.secrets import OpaqueCodexAuth
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.codex_cloud.store import (
    CodexCloudConflictError,
    CodexCloudNotFoundError,
    CodexCloudStore,
)
from voxos.text.run_chat_service import RunChatService

logger = logging.getLogger(__name__)

TASK_URL_PATTERN = re.compile(
    r"(?P<url>https://(?:chatgpt\.com|codex\.openai\.com)/[^\s]*"
    r"/tasks/(?P<task_id>[A-Za-z0-9_-]{8,128}))",
)
LIST_TASKS_ADAPTER = TypeAdapter(tuple[CodexCloudListTask, ...])
UNSUPPORTED_RUN_MESSAGE = "Codex Cloud executor received an unsupported run kind"
TASK_MAPPING_NOT_FOUND_MESSAGE = "Codex Cloud task mapping was not found"
TRACKING_TIMEOUT_CODE = "codex_cloud_tracking_timeout"
SUBMISSION_URL_MISSING_MESSAGE = "Codex Cloud submission did not return a task URL"
INVALID_LIST_OUTPUT_CODE = "invalid_codex_cloud_list_output"


@dataclass(frozen=True, slots=True)
class CodexCloudRunExecutor:
    store: CodexCloudStore
    run_store: SqlModelAgentRunStore
    run_chat_service: RunChatService
    sandbox: DockerCodexSandboxRunner
    settings: CodexCloudSettings

    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        if run.kind != AgentRunKind.CODEX_CLOUD_TASK.value:
            raise ValueError(UNSUPPORTED_RUN_MESSAGE)
        try:
            return await self._execute_task(run)
        except AgentRunCancelledError:
            await self._deliver_update(
                run=run,
                stage="cancelled",
                text="Codex Cloud task tracking was cancelled. The remote task may still continue.",
            )
            raise
        except Exception:
            if run.attempt >= run.max_attempts:
                await self._deliver_update(
                    run=run,
                    stage="failed",
                    text="Codex Cloud task failed. Check Operations for details.",
                )
            raise

    async def _execute_task(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        task = await self.store.get_task_by_run(user_id=run.user_id, agent_run_id=run.id)
        if task is None:
            raise CodexCloudNotFoundError(TASK_MAPPING_NOT_FOUND_MESSAGE)

        if task.submission_state == CodexCloudSubmissionState.SUBMITTING.value:
            await self.store.update_task_submission(
                user_id=run.user_id,
                task_id=task.id,
                submission_state=CodexCloudSubmissionState.SUBMISSION_UNCERTAIN,
            )
            return await self._finalize_uncertain_submission(run)

        if task.submission_state == CodexCloudSubmissionState.PENDING.value:
            payload = CodexCloudTaskRunPayload.model_validate(run.payload_json)
            task = await self.store.update_task_submission(
                user_id=run.user_id,
                task_id=task.id,
                submission_state=CodexCloudSubmissionState.SUBMITTING,
            )
            try:
                submission = await self._run_authenticated(
                    run=run,
                    account_id=payload.account_id,
                    arguments=_submission_arguments(payload),
                    ambiguous_on_failure=True,
                )
                remote_task_id, remote_task_url = parse_submission_output(submission.stdout)
            except CodexSandboxError as exc:
                if exc.ambiguous:
                    await self.store.update_task_submission(
                        user_id=run.user_id,
                        task_id=task.id,
                        submission_state=CodexCloudSubmissionState.SUBMISSION_UNCERTAIN,
                    )
                    return await self._finalize_uncertain_submission(run)
                raise
            except ValueError:
                await self.store.update_task_submission(
                    user_id=run.user_id,
                    task_id=task.id,
                    submission_state=CodexCloudSubmissionState.SUBMISSION_UNCERTAIN,
                )
                return await self._finalize_uncertain_submission(run)

            task = await self.store.update_task_submission(
                user_id=run.user_id,
                task_id=task.id,
                submission_state=CodexCloudSubmissionState.SUBMITTED,
                remote_task_id=remote_task_id,
                remote_task_url=remote_task_url,
                remote_status=CodexCloudRemoteStatus.PENDING,
            )
            await self.run_store.append_event(
                run_id=run.id,
                user_id=run.user_id,
                kind=AgentRunEventKind.PROGRESS,
                payload_json={
                    "stage": "submitted",
                    "remote_task_id": remote_task_id,
                    "remote_task_url": remote_task_url,
                },
            )

        if task.remote_task_id is None:
            return await self._finalize_uncertain_submission(run)
        if task.remote_task_url is not None:
            await self._deliver_update(
                run=run,
                stage="submitted",
                text=f"Codex Cloud task submitted: {task.remote_task_url}",
            )
        return await self._track_remote_task(
            run=run,
            account_id=task.account_id,
            environment_id=task.environment_id,
            task_id=task.id,
            remote_task_id=task.remote_task_id,
            remote_task_url=task.remote_task_url,
        )

    async def _track_remote_task(
        self,
        *,
        run: AgentRunRecord,
        account_id: str,
        environment_id: str,
        task_id: str,
        remote_task_id: str,
        remote_task_url: str | None,
    ) -> AgentRunExecutionResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.task_tracking_timeout_seconds
        previous_status: CodexCloudRemoteStatus | None = None
        while loop.time() < deadline:
            await self.run_store.raise_if_cancel_requested(run_id=run.id, user_id=run.user_id)
            result = await self._run_authenticated(
                run=run,
                account_id=account_id,
                arguments=(
                    "cloud",
                    "list",
                    "--env",
                    environment_id,
                    "--limit",
                    "20",
                    "--json",
                ),
            )
            remote = next(
                (candidate for candidate in parse_cloud_list(result.stdout) if candidate.id == remote_task_id),
                None,
            )
            if remote is not None:
                status = normalize_remote_status(remote.status)
                summary = remote_task_summary(remote)
                await self.store.update_task_submission(
                    user_id=run.user_id,
                    task_id=task_id,
                    submission_state=CodexCloudSubmissionState.SUBMITTED,
                    remote_task_url=str(remote.url) if remote.url is not None else remote_task_url,
                    remote_status=status,
                    summary=summary,
                    remote_updated_at=remote.updated_at,
                )
                if status != previous_status:
                    await self.run_store.append_event(
                        run_id=run.id,
                        user_id=run.user_id,
                        kind=AgentRunEventKind.PROGRESS,
                        payload_json={
                            "stage": "remote_status",
                            "remote_status": status.value,
                            "remote_task_url": (str(remote.url) if remote.url is not None else remote_task_url),
                        },
                    )
                    previous_status = status
                if status == CodexCloudRemoteStatus.READY:
                    await self._deliver_update(
                        run=run,
                        stage="complete",
                        text=codex_terminal_message(
                            status=status,
                            summary=summary,
                            remote_task_url=(str(remote.url) if remote.url is not None else remote_task_url),
                        ),
                    )
                    return AgentRunExecutionResult(
                        result_json={
                            "remote_task_id": remote_task_id,
                            "remote_task_url": (str(remote.url) if remote.url is not None else remote_task_url),
                            "remote_status": status.value,
                            "summary": summary,
                        },
                    )
                if status in {
                    CodexCloudRemoteStatus.FAILED,
                    CodexCloudRemoteStatus.CANCELLED,
                }:
                    await self._deliver_update(
                        run=run,
                        stage=status.value,
                        text=codex_terminal_message(
                            status=status,
                            summary=summary,
                            remote_task_url=(str(remote.url) if remote.url is not None else remote_task_url),
                        ),
                    )
                    await self.run_store.fail_run(
                        run_id=run.id,
                        user_id=run.user_id,
                        error_type="CodexCloudRemoteTaskFailed",
                        error_message=f"Codex Cloud task ended with status {status.value}",
                        failure_category=AgentRunFailureCategory.PROVIDER,
                        retry=False,
                    )
                    return AgentRunExecutionResult(finalized=True)
            await asyncio.sleep(self.settings.remote_poll_interval_seconds)
        raise CodexSandboxError(TRACKING_TIMEOUT_CODE, retryable=True)

    async def _run_authenticated(
        self,
        *,
        run: AgentRunRecord,
        account_id: str,
        arguments: tuple[str, ...],
        ambiguous_on_failure: bool = False,
    ) -> CodexSandboxResult:
        operation_id = f"{self.settings.worker_id}:{run.id}"
        generation = await self._wait_for_account_lease(
            run=run,
            account_id=account_id,
            operation_id=operation_id,
        )
        try:
            auth = await self.store.load_account_auth(user_id=run.user_id, account_id=account_id)
            result = await self.sandbox.run(
                arguments=arguments,
                auth=auth,
                ambiguous_on_failure=ambiguous_on_failure,
            )
            if result.refreshed_auth_json is not None:
                await self.store.persist_refreshed_auth(
                    user_id=run.user_id,
                    account_id=account_id,
                    auth=OpaqueCodexAuth(result.refreshed_auth_json),
                    worker_id=operation_id,
                    generation=generation,
                )
            return result
        except CodexSandboxError as exc:
            if exc.refreshed_auth_json is not None:
                await self.store.persist_refreshed_auth(
                    user_id=run.user_id,
                    account_id=account_id,
                    auth=OpaqueCodexAuth(exc.refreshed_auth_json),
                    worker_id=operation_id,
                    generation=generation,
                )
            raise
        finally:
            await self.store.release_account_lease(
                user_id=run.user_id,
                account_id=account_id,
                worker_id=operation_id,
                generation=generation,
            )

    async def _wait_for_account_lease(
        self,
        *,
        run: AgentRunRecord,
        account_id: str,
        operation_id: str,
    ) -> int:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.lease_timeout_seconds
        while True:
            await self.run_store.raise_if_cancel_requested(run_id=run.id, user_id=run.user_id)
            try:
                return await self.store.acquire_account_lease(
                    user_id=run.user_id,
                    account_id=account_id,
                    worker_id=operation_id,
                    lease_seconds=self.settings.lease_timeout_seconds,
                )
            except CodexCloudConflictError:
                if loop.time() >= deadline:
                    raise
                await asyncio.sleep(self.settings.poll_interval_seconds)

    async def _finalize_uncertain_submission(
        self,
        run: AgentRunRecord,
    ) -> AgentRunExecutionResult:
        await self._deliver_update(
            run=run,
            stage="uncertain",
            text=("The Codex Cloud submission outcome is uncertain. Check Codex Cloud before submitting another task."),
        )
        await self.run_store.fail_run(
            run_id=run.id,
            user_id=run.user_id,
            error_type="CodexCloudSubmissionUncertain",
            error_message=(
                "The Codex Cloud submission outcome is uncertain. Check Codex Cloud before submitting another task."
            ),
            failure_category=AgentRunFailureCategory.PROVIDER,
            retry=False,
        )
        return AgentRunExecutionResult(finalized=True)

    async def _deliver_update(
        self,
        *,
        run: AgentRunRecord,
        stage: str,
        text: str,
    ) -> None:
        try:
            result = await self.run_chat_service.deliver_to_run(
                run=run,
                stage=stage,
                text=text,
            )
            if not result.delivered:
                logger.warning(
                    "Codex Cloud update could not be delivered to its chat",
                    extra={
                        "run_id": run.id,
                        "stage": stage,
                        "reason": result.unavailable_reason,
                    },
                )
        except Exception:
            logger.exception(
                "Codex Cloud chat delivery failed",
                extra={"run_id": run.id, "stage": stage},
            )


def _submission_arguments(payload: CodexCloudTaskRunPayload) -> tuple[str, ...]:
    arguments = [
        "cloud",
        "exec",
        "--env",
        payload.environment_id,
        "--attempts",
        str(payload.attempts),
    ]
    if payload.branch is not None:
        arguments.extend(("--branch", payload.branch))
    arguments.extend(("--", payload.prompt))
    return tuple(arguments)


def parse_submission_output(output: str) -> tuple[str, str]:
    match = TASK_URL_PATTERN.search(output)
    if match is None:
        raise ValueError(SUBMISSION_URL_MISSING_MESSAGE)
    return match.group("task_id"), match.group("url").rstrip(".,)")


def parse_cloud_list(output: str) -> tuple[CodexCloudListTask, ...]:
    try:
        payload = orjson.loads(output)
        if isinstance(payload, list):
            return LIST_TASKS_ADAPTER.validate_python(payload)
        return CodexCloudListResponse.model_validate(payload).tasks
    except (orjson.JSONDecodeError, ValidationError) as exc:
        raise CodexSandboxError(INVALID_LIST_OUTPUT_CODE) from exc


def normalize_remote_status(value: str) -> CodexCloudRemoteStatus:
    normalized = value.strip().lower().replace("-", "_")
    if normalized in {"ready", "applied", "completed", "complete", "succeeded", "success"}:
        return CodexCloudRemoteStatus.READY
    if normalized in {"failed", "failure", "error"}:
        return CodexCloudRemoteStatus.FAILED
    if normalized in {"cancelled", "canceled"}:
        return CodexCloudRemoteStatus.CANCELLED
    if normalized in {"running", "in_progress", "processing"}:
        return CodexCloudRemoteStatus.RUNNING
    if normalized in {"pending", "queued", "starting"}:
        return CodexCloudRemoteStatus.PENDING
    return CodexCloudRemoteStatus.UNKNOWN


def remote_task_summary(task: CodexCloudListTask) -> str | None:
    if task.title:
        return task.title
    if isinstance(task.summary, str):
        return task.summary
    if task.summary is None:
        return None
    return (
        f"{task.summary.files_changed} files changed (+{task.summary.lines_added}/-{task.summary.lines_removed} lines)"
    )


def codex_terminal_message(
    *,
    status: CodexCloudRemoteStatus,
    summary: str | None,
    remote_task_url: str | None,
) -> str:
    label = "completed" if status == CodexCloudRemoteStatus.READY else status.value
    parts = [f"Codex Cloud task {label}."]
    if summary:
        parts.append(summary)
    if remote_task_url:
        parts.append(remote_task_url)
    return "\n\n".join(parts)

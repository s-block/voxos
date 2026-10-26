import asyncio
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from voxos.browser_accounts.models import BrowserAccountErrorCategory
from voxos.browser_accounts.providers.base import (
    BrowserProviderCapabilities,
    BrowserProviderCredential,
    BrowserProviderOperationError,
    BrowserProviderSetupSession,
    BrowserProviderTaskSession,
)
from voxos.browser_accounts.secrets import (
    OpaqueBrowserProfileHandle,
    OpaqueBrowserSessionHandle,
)
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.browser_runtime.contracts import BrowserTaskRequest, BrowserTaskResult

INVALID_STAGEHAND_URL = "Stagehand adapter URL is invalid"


class StagehandTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: str = Field(pattern=r"^(completed|blocked)$")
    output: str = Field(min_length=1, max_length=50_000)


class StagehandLocalProvider:
    capabilities = BrowserProviderCapabilities(
        human_live_view=False,
        managed_profiles=False,
        remote_profile_deletion=False,
        recording_can_be_disabled=True,
        exact_domain_restriction=True,
        persistent_state=False,
        native_secret_injection=True,
        concurrent_profile_sessions=1,
    )

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        settings: BrowserAccountSettings,
    ) -> None:
        self._http_client = http_client
        self._settings = settings
        self._base_url = self._validated_base_url(settings.stagehand_base_url)
        self._tasks: dict[str, asyncio.Task[BrowserTaskResult]] = {}

    async def create_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        account_label: str,
        external_user_reference: str,
    ) -> OpaqueBrowserProfileHandle:
        del credential, account_label, external_user_reference
        raise self._unsupported()

    async def delete_profile(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> None:
        del credential, profile
        raise self._unsupported()

    async def profile_exists(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
    ) -> bool:
        del credential, profile
        raise self._unsupported()

    async def start_setup_session(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle,
        browser_country_code: str,
    ) -> BrowserProviderSetupSession:
        del credential, profile, browser_country_code
        raise self._unsupported()

    async def stop_session(
        self,
        *,
        credential: BrowserProviderCredential,
        session: OpaqueBrowserSessionHandle,
    ) -> None:
        del credential
        task_id = session.value.get_secret_value()
        execution = self._tasks.pop(task_id, None)
        if execution is None:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.SESSION_MISSING,
            )
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)

    async def start_task(
        self,
        *,
        credential: BrowserProviderCredential,
        profile: OpaqueBrowserProfileHandle | None,
        request: BrowserTaskRequest,
    ) -> BrowserProviderTaskSession:
        del credential, profile
        if request.model_credential is None:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.PROVIDER_AUTH,
            )
        task_id = str(uuid4())
        self._tasks[task_id] = asyncio.create_task(
            self._run_task(request=request),
        )
        return BrowserProviderTaskSession(
            task_handle=SecretStr(task_id),
            session_handle=OpaqueBrowserSessionHandle(SecretStr(task_id)),
        )

    async def wait_for_task(
        self,
        *,
        credential: BrowserProviderCredential,
        task: BrowserProviderTaskSession,
    ) -> BrowserTaskResult:
        del credential
        task_id = task.task_handle.get_secret_value()
        execution = self._tasks.get(task_id)
        if execution is None:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.SESSION_MISSING,
            )
        try:
            async with asyncio.timeout(self._settings.task_timeout_seconds):
                return await asyncio.shield(execution)
        except TimeoutError as exc:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.SESSION_TIMEOUT,
            ) from exc

    async def _run_task(
        self,
        *,
        request: BrowserTaskRequest,
    ) -> BrowserTaskResult:
        model_credential = request.model_credential
        service_token = self._settings.stagehand_service_token
        if model_credential is None or service_token is None:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.PROVIDER_AUTH,
            )
        variables = {
            name: {
                "value": variable.value.get_secret_value(),
                "description": variable.description,
            }
            for name, variable in request.variables.items()
        }
        try:
            response = await self._http_client.post(
                f"{self._base_url}/v1/tasks/run",
                headers={"Authorization": f"Bearer {service_token.get_secret_value()}"},
                json={
                    "instruction": request.instruction,
                    "startUrl": request.start_url,
                    "allowedOrigins": list(request.allowed_origins),
                    "actionMode": request.action_mode.value,
                    "maxSteps": request.max_steps,
                    "variables": variables,
                    "model": {
                        "adapter": model_credential.adapter.value,
                        "modelName": model_credential.model_name,
                        "baseUrl": model_credential.base_url,
                        "apiKey": model_credential.api_key.get_secret_value(),
                    },
                },
                timeout=self._settings.task_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.SESSION_TIMEOUT,
            ) from exc
        except httpx.HTTPError as exc:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
            ) from exc

        if response.status_code != 200:
            raise BrowserProviderOperationError(
                self._error_category(response.status_code),
            )
        try:
            result = StagehandTaskResponse.model_validate(response.json())
        except ValueError as exc:
            raise BrowserProviderOperationError(
                BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE,
            ) from exc
        output = result.output
        secrets = [
            *(variable.value.get_secret_value() for variable in request.variables.values()),
            model_credential.api_key.get_secret_value(),
        ]
        for secret in secrets:
            if secret:
                output = output.replace(secret, "[redacted]")
        return BrowserTaskResult(
            output_text=output[: self._settings.max_output_characters],
            successful=result.status == "completed",
        )

    @staticmethod
    def _validated_base_url(value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(INVALID_STAGEHAND_URL)
        return value.rstrip("/")

    @staticmethod
    def _error_category(status_code: int) -> BrowserAccountErrorCategory:
        if status_code in {400, 404, 422}:
            return BrowserAccountErrorCategory.PROVIDER_REQUEST_INVALID
        if status_code in {401, 403}:
            return BrowserAccountErrorCategory.PROVIDER_AUTH
        if status_code in {409, 429}:
            return BrowserAccountErrorCategory.PROVIDER_REJECTED
        if status_code == 504:
            return BrowserAccountErrorCategory.SESSION_TIMEOUT
        return BrowserAccountErrorCategory.PROVIDER_UNAVAILABLE

    @staticmethod
    def _unsupported() -> BrowserProviderOperationError:
        return BrowserProviderOperationError(
            BrowserAccountErrorCategory.PROVIDER_REJECTED,
        )

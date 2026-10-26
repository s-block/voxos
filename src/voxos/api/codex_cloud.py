from collections.abc import Awaitable, Callable
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.storage import SqlModelAgentRuntimeStore
from voxos.auth.models import AuthenticatedUser
from voxos.codex_cloud.models import (
    CodexAccountResponse,
    CodexAuthSessionResponse,
    CodexCloudTaskCreateRequest,
    CodexCloudTaskListResponse,
    CodexCloudTaskResponse,
    CodexEnvironmentUpdateRequest,
)
from voxos.codex_cloud.secrets import CodexAccountSecretStore
from voxos.codex_cloud.service import CodexCloudDisabledError, CodexCloudService
from voxos.codex_cloud.settings import CodexCloudSettings
from voxos.codex_cloud.store import (
    CodexCloudConflictError,
    CodexCloudNotFoundError,
    CodexCloudStore,
)
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.text.run_chat_service import RunChatService, RunChatValidationError

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
ResponseT = TypeVar("ResponseT")


def create_codex_cloud_router(
    *,
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    settings: CodexCloudSettings | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/codex-cloud", tags=["codex-cloud"])
    resolved_settings = settings or CodexCloudSettings()

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def service_for(user_id: str) -> CodexCloudService:
        scoped_session_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )
        run_store = SqlModelAgentRunStore(session_factory=scoped_session_factory)
        return CodexCloudService(
            store=CodexCloudStore(
                session_factory=scoped_session_factory,
                secret_store=CodexAccountSecretStore(secret_vault),
            ),
            run_store=run_store,
            run_chat_service=RunChatService(
                session_factory=scoped_session_factory,
                runtime_store=SqlModelAgentRuntimeStore(scoped_session_factory),
                run_store=run_store,
            ),
            settings=resolved_settings,
        )

    @router.get("/account", response_model=CodexAccountResponse)
    async def get_account(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexAccountResponse:
        return await invoke_codex_operation(
            service_for(current_user.user_id).account(user_id=current_user.user_id),
        )

    @router.post(
        "/account/login",
        response_model=CodexAuthSessionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_login(
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexAuthSessionResponse:
        response.headers["Cache-Control"] = "no-store"
        return await invoke_codex_operation(
            service_for(current_user.user_id).start_login(user_id=current_user.user_id),
        )

    @router.get(
        "/auth-sessions/{auth_session_id}",
        response_model=CodexAuthSessionResponse,
    )
    async def get_auth_session(
        auth_session_id: str,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexAuthSessionResponse:
        response.headers["Cache-Control"] = "no-store"
        return await invoke_codex_operation(
            service_for(current_user.user_id).auth_session(
                user_id=current_user.user_id,
                auth_session_id=auth_session_id,
            ),
        )

    @router.post(
        "/auth-sessions/{auth_session_id}/cancel",
        response_model=CodexAuthSessionResponse,
    )
    async def cancel_auth_session(
        auth_session_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexAuthSessionResponse:
        return await invoke_codex_operation(
            service_for(current_user.user_id).cancel_auth_session(
                user_id=current_user.user_id,
                auth_session_id=auth_session_id,
            ),
        )

    @router.put("/account/environment", response_model=CodexAccountResponse)
    async def update_environment(
        request_body: CodexEnvironmentUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexAccountResponse:
        return await invoke_codex_operation(
            service_for(current_user.user_id).update_environment(
                user_id=current_user.user_id,
                environment_id=request_body.environment_id,
                environment_label=request_body.environment_label,
            ),
        )

    @router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
    async def disconnect_account(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Response:
        await invoke_codex_operation(
            service_for(current_user.user_id).disconnect(user_id=current_user.user_id),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/tasks",
        response_model=CodexCloudTaskResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_task(
        request_body: CodexCloudTaskCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexCloudTaskResponse:
        return await invoke_codex_operation(
            service_for(current_user.user_id).submit_task(
                user_id=current_user.user_id,
                request=request_body,
            ),
        )

    @router.get("/tasks", response_model=CodexCloudTaskListResponse)
    async def list_tasks(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> CodexCloudTaskListResponse:
        return await invoke_codex_operation(
            service_for(current_user.user_id).list_tasks(user_id=current_user.user_id),
        )

    return router


async def invoke_codex_operation(operation: Awaitable[ResponseT]) -> ResponseT:
    try:
        return await operation
    except CodexCloudDisabledError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except CodexCloudNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CodexCloudConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RunChatValidationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat was not found") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

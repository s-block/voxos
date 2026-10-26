from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from voxos.auth.models import AuthenticatedUser
from voxos.browser_accounts.connection import McpBrowserCredentialResolver
from voxos.browser_accounts.errors import (
    BrowserAccountAuthorizationError,
    BrowserAccountConflictError,
    BrowserAccountNotFoundError,
    BrowserAccountProviderConnectionError,
    BrowserAccountProviderError,
    BrowserAccountsDisabledError,
    BrowserAccountValidationError,
)
from voxos.browser_accounts.model_resolution import BrowserTaskModelCredentialResolver
from voxos.browser_accounts.models import (
    BrowserAccountAgentAssignmentsRequest,
    BrowserAccountApprovalRequest,
    BrowserAccountAuthorizationResponse,
    BrowserAccountCreateRequest,
    BrowserAccountCredentialsRequest,
    BrowserAccountListResponse,
    BrowserAccountResponse,
    BrowserAccountSetupResponse,
    BrowserAccountUpdateRequest,
)
from voxos.browser_accounts.providers.base import BrowserAccountProviderAdapter
from voxos.browser_accounts.providers.browser_use_cloud import BrowserUseCloudProvider
from voxos.browser_accounts.providers.browserless import BrowserlessProvider
from voxos.browser_accounts.providers.composite import CompositeBrowserAccountProvider
from voxos.browser_accounts.providers.stagehand_local import StagehandLocalProvider
from voxos.browser_accounts.secrets import BrowserAccountSecretStore
from voxos.browser_accounts.services import BrowserAccountService
from voxos.browser_accounts.settings import BrowserAccountSettings
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
ResponseT = TypeVar("ResponseT")


class SensitiveInputAPIRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_validation_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                safe_errors = [
                    {key: error[key] for key in ("type", "loc", "msg") if key in error} for error in exc.errors()
                ]
                return JSONResponse(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    content={"detail": safe_errors},
                    headers={"Cache-Control": "no-store"},
                )

        return safe_validation_handler


def create_browser_accounts_router(
    *,
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    http_client: httpx.AsyncClient,
    settings: BrowserAccountSettings | None = None,
    provider: BrowserAccountProviderAdapter | None = None,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/browser-accounts",
        tags=["browser-accounts"],
        route_class=SensitiveInputAPIRoute,
    )
    resolved_settings = settings or BrowserAccountSettings()
    resolved_provider = provider or CompositeBrowserAccountProvider(
        browser_use=BrowserUseCloudProvider(
            http_client=http_client,
            settings=resolved_settings,
        ),
        browserless=BrowserlessProvider(resolved_settings),
        stagehand_local=StagehandLocalProvider(
            http_client=http_client,
            settings=resolved_settings,
        ),
    )

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def service_for(user_id: str) -> BrowserAccountService:
        scoped_session_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )
        return BrowserAccountService(
            session_factory=scoped_session_factory,
            provider=resolved_provider,
            credential_resolver=McpBrowserCredentialResolver(secret_vault),
            secret_store=BrowserAccountSecretStore(secret_vault),
            settings=resolved_settings,
            model_credential_resolver=BrowserTaskModelCredentialResolver(
                session_factory=scoped_session_factory,
                secret_vault=secret_vault,
            ),
        )

    @router.get("", response_model=BrowserAccountListResponse)
    async def list_browser_accounts(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountListResponse:
        accounts = await invoke_browser_account_operation(
            service_for(current_user.user_id).list_accounts(
                user_id=current_user.user_id,
            ),
        )
        return BrowserAccountListResponse(accounts=accounts)

    @router.post(
        "",
        response_model=BrowserAccountResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_browser_account(
        request_body: BrowserAccountCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).create_account(
                user_id=current_user.user_id,
                request=request_body,
            ),
        )

    @router.put("/{account_id}", response_model=BrowserAccountResponse)
    async def update_browser_account(
        account_id: str,
        request_body: BrowserAccountUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).update_account(
                user_id=current_user.user_id,
                account_id=account_id,
                request=request_body,
            ),
        )

    @router.put("/{account_id}/agents", response_model=BrowserAccountResponse)
    async def set_browser_account_agents(
        account_id: str,
        request_body: BrowserAccountAgentAssignmentsRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).set_agent_assignments(
                user_id=current_user.user_id,
                account_id=account_id,
                request=request_body,
            ),
        )

    @router.put("/{account_id}/credentials", response_model=BrowserAccountResponse)
    async def update_browser_account_credentials(
        account_id: str,
        request_body: BrowserAccountCredentialsRequest,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        response.headers["Cache-Control"] = "no-store"
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).update_credentials(
                user_id=current_user.user_id,
                account_id=account_id,
                request=request_body,
            ),
        )

    @router.post("/{account_id}/setup/start", response_model=BrowserAccountSetupResponse)
    @router.post("/{account_id}/reauthenticate", response_model=BrowserAccountSetupResponse)
    async def start_browser_account_setup(
        account_id: str,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountSetupResponse:
        response.headers["Cache-Control"] = "no-store"
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).start_setup(
                user_id=current_user.user_id,
                account_id=account_id,
            ),
        )

    @router.post("/{account_id}/setup/complete", response_model=BrowserAccountResponse)
    async def complete_browser_account_setup(
        account_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).complete_setup(
                user_id=current_user.user_id,
                account_id=account_id,
            ),
        )

    @router.post("/{account_id}/setup/cancel", response_model=BrowserAccountResponse)
    async def cancel_browser_account_setup(
        account_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).cancel_setup(
                user_id=current_user.user_id,
                account_id=account_id,
            ),
        )

    @router.post("/authorizations", response_model=BrowserAccountAuthorizationResponse)
    async def authorize_browser_account(
        request_body: BrowserAccountApprovalRequest,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountAuthorizationResponse:
        response.headers["Cache-Control"] = "no-store"
        return await invoke_browser_account_operation(
            service_for(current_user.user_id).approve_for_chat(
                user_id=current_user.user_id,
                request=request_body,
            ),
        )

    @router.post("/{account_id}/revoke", response_model=BrowserAccountResponse)
    async def revoke_browser_account(
        account_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> BrowserAccountResponse:
        account = await invoke_browser_account_operation(
            service_for(current_user.user_id).revoke_account(
                user_id=current_user.user_id,
                account_id=account_id,
            ),
        )
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return account

    @router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_browser_account(
        account_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Response:
        await invoke_browser_account_operation(
            service_for(current_user.user_id).revoke_account(
                user_id=current_user.user_id,
                account_id=account_id,
                delete_after=True,
            ),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


async def invoke_browser_account_operation(operation: Awaitable[ResponseT]) -> ResponseT:
    try:
        return await operation
    except BrowserAccountsDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except BrowserAccountNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except BrowserAccountValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except BrowserAccountAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except BrowserAccountConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except BrowserAccountProviderConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    except BrowserAccountProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": str(exc),
                "category": exc.category.value,
            },
        ) from exc

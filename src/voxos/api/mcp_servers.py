from collections.abc import Awaitable, Callable
from html import escape
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.mcp_tool_cache import McpToolCatalogCache
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.mcp.client import SdkUserMcpClient, UserMcpClient
from voxos.mcp.delegation import (
    McpCredentialDelegationSigner,
    credential_delegation_signer_from_settings,
)
from voxos.mcp.errors import (
    McpAuthRequiredError,
    McpConnectionTestError,
    McpInvalidAuthTypeError,
    McpOAuthConfigurationError,
    McpOAuthStateError,
    McpPresetConfigurationError,
    McpPresetNotFoundError,
    McpServerLabelConflictError,
    McpServerNotFoundError,
    McpServerUrlPolicyError,
    UserMcpError,
)
from voxos.mcp.models import (
    McpClientCredentialsAuthRequest,
    McpOAuthStartRequest,
    McpOAuthStartResponse,
    McpPresetListResponse,
    McpPresetServerCreateRequest,
    McpPrivateKeyJwtAuthRequest,
    McpServerCreateRequest,
    McpServerListResponse,
    McpServerResponse,
    McpServerUpdateRequest,
    McpStaticAuthRequest,
)
from voxos.mcp.oauth import McpOAuthHttpClient
from voxos.mcp.oauth_state import McpOAuthStateSigner, SignedMcpOAuthState
from voxos.mcp.presets import (
    MCP_PRESET_CATALOG_RESEARCHED_AT,
    MCP_PRESET_CATALOG_VERSION,
    list_mcp_service_presets,
)
from voxos.mcp.services import UserMcpServerService
from voxos.mcp.settings import McpSettings
from voxos.mcp.url_policy import McpOutboundUrlPolicy
from voxos.security.outbound_endpoints import (
    OutboundEndpointPolicy,
    OutboundEndpointPurpose,
)

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]


def create_mcp_servers_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    http_client: httpx.AsyncClient,
    mcp_client: UserMcpClient | None = None,
    mcp_settings: McpSettings | None = None,
    oauth_state_signer: McpOAuthStateSigner | None = None,
    credential_delegation_signer: McpCredentialDelegationSigner | None = None,
    outbound_endpoint_policy: OutboundEndpointPolicy | None = None,
    mcp_tool_catalog_cache: McpToolCatalogCache | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/mcp", tags=["mcp"])
    resolved_mcp_settings = mcp_settings or McpSettings()
    resolved_endpoint_policy = outbound_endpoint_policy or OutboundEndpointPolicy.from_settings()
    resolved_url_policy = McpOutboundUrlPolicy(resolved_endpoint_policy)
    resolved_mcp_client = mcp_client or SdkUserMcpClient(resolved_url_policy)
    resolved_oauth_state_signer = oauth_state_signer or oauth_state_signer_from_settings(
        resolved_mcp_settings,
    )
    resolved_credential_delegation_signer = credential_delegation_signer or credential_delegation_signer_from_settings(
        resolved_mcp_settings
    )
    resolved_mcp_tool_catalog_cache = mcp_tool_catalog_cache or McpToolCatalogCache(
        max_entries=(resolved_mcp_settings.mcp_tool_catalog_cache_max_entries),
        ttl_seconds=(resolved_mcp_settings.mcp_tool_catalog_cache_ttl_seconds),
    )

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    def create_service(session: AsyncSession) -> UserMcpServerService:
        return UserMcpServerService(
            session=session,
            secret_vault=secret_vault,
            mcp_client=resolved_mcp_client,
            oauth_client=McpOAuthHttpClient(http_client, resolved_url_policy),
            oauth_state_signer=resolved_oauth_state_signer,
            credential_delegation_signer=(resolved_credential_delegation_signer),
            url_policy=resolved_url_policy,
        )

    async def invalidate_catalog(user_id: str) -> None:
        await resolved_mcp_tool_catalog_cache.invalidate_user(user_id)

    @router.get("/presets", response_model=McpPresetListResponse)
    async def list_presets(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpPresetListResponse:
        _ = current_user
        return McpPresetListResponse(
            catalog_version=MCP_PRESET_CATALOG_VERSION,
            researched_at=MCP_PRESET_CATALOG_RESEARCHED_AT,
            presets=list_mcp_service_presets(
                trusted_endpoint_urls=resolved_endpoint_policy.trusted_urls(
                    OutboundEndpointPurpose.MCP,
                ),
            ),
        )

    @router.post(
        "/presets/{preset_id}/servers",
        response_model=McpServerResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_server_from_preset(
        preset_id: str,
        request_body: McpPresetServerCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).create_server_from_preset(
                    user_id=current_user.user_id,
                    preset_id=preset_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.get("/servers", response_model=McpServerListResponse)
    async def list_servers(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                servers = await create_service(session).list_servers(
                    current_user.user_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return McpServerListResponse(servers=servers)

    @router.post(
        "/servers",
        response_model=McpServerResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_server(
        request_body: McpServerCreateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).create_server(
                    user_id=current_user.user_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.get("/servers/{server_id}", response_model=McpServerResponse)
    async def get_server(
        server_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await create_service(session).get_server(
                    user_id=current_user.user_id,
                    server_id=server_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.patch("/servers/{server_id}", response_model=McpServerResponse)
    async def update_server(
        server_id: str,
        request_body: McpServerUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).update_server(
                    user_id=current_user.user_id,
                    server_id=server_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.delete("/servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_server(
        server_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Response:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                await create_service(session).delete_server(
                    user_id=current_user.user_id,
                    server_id=server_id,
                )
            await invalidate_catalog(current_user.user_id)
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/servers/{server_id}/auth/static", response_model=McpServerResponse)
    async def store_static_auth(
        server_id: str,
        request_body: McpStaticAuthRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).store_static_auth(
                    user_id=current_user.user_id,
                    server_id=server_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.post(
        "/servers/{server_id}/auth/client-credentials",
        response_model=McpServerResponse,
    )
    async def store_client_credentials_auth(
        server_id: str,
        request_body: McpClientCredentialsAuthRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).store_client_credentials_auth(
                    user_id=current_user.user_id,
                    server_id=server_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.post(
        "/servers/{server_id}/auth/private-key-jwt",
        response_model=McpServerResponse,
    )
    async def store_private_key_jwt_auth(
        server_id: str,
        request_body: McpPrivateKeyJwtAuthRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).store_private_key_jwt_auth(
                    user_id=current_user.user_id,
                    server_id=server_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.post("/servers/{server_id}/auth/start", response_model=McpOAuthStartResponse)
    async def start_oauth(
        server_id: str,
        request_body: McpOAuthStartRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpOAuthStartResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).start_oauth(
                    user_id=current_user.user_id,
                    server_id=server_id,
                    request=request_body,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.get(
        "/servers/{server_id}/auth/callback",
        response_class=HTMLResponse,
    )
    async def complete_oauth(
        server_id: str,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        if error is not None:
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html("MCP authentication failed", error),
            )
        if code is None or state is None:
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html(
                    "MCP authentication failed",
                    "Missing OAuth callback code or state.",
                ),
            )

        try:
            signed_state = require_signed_oauth_state(
                signer=resolved_oauth_state_signer,
                state=state,
                server_id=server_id,
            )
            async with scoped_session_factory_for(signed_state.user_id)() as session:
                await create_service(session).complete_oauth(
                    server_id=server_id,
                    code=code,
                    state=state,
                )
            await invalidate_catalog(signed_state.user_id)
        except (SQLAlchemyError, UserMcpError):
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html(
                    "MCP authentication failed",
                    "Return to Voxos and start authentication again.",
                ),
            )
        return HTMLResponse(
            content=oauth_html(
                "MCP authentication complete",
                "You can close this tab and return to Voxos.",
            ),
        )

    @router.get(
        "/auth/callback",
        response_class=HTMLResponse,
    )
    async def complete_oauth_from_signed_state(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        if error is not None:
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html("MCP authentication failed", error),
            )
        if code is None or state is None:
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html(
                    "MCP authentication failed",
                    "Missing OAuth callback code or state.",
                ),
            )

        try:
            signed_state = require_signed_oauth_state(
                signer=resolved_oauth_state_signer,
                state=state,
                server_id=None,
            )
            async with scoped_session_factory_for(signed_state.user_id)() as session:
                await create_service(session).complete_oauth(
                    server_id=signed_state.server_id,
                    code=code,
                    state=state,
                )
            await invalidate_catalog(signed_state.user_id)
        except (SQLAlchemyError, UserMcpError):
            return HTMLResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=oauth_html(
                    "MCP authentication failed",
                    "Return to Voxos and start authentication again.",
                ),
            )
        return HTMLResponse(
            content=oauth_html(
                "MCP authentication complete",
                "You can close this tab and return to Voxos.",
            ),
        )

    @router.post("/servers/{server_id}/auth/revoke", response_model=McpServerResponse)
    async def revoke_auth(
        server_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).revoke_auth(
                    user_id=current_user.user_id,
                    server_id=server_id,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    @router.post("/servers/{server_id}/test", response_model=McpServerResponse)
    async def test_server(
        server_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> McpServerResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                response = await create_service(session).test_server(
                    user_id=current_user.user_id,
                    server_id=server_id,
                )
            await invalidate_catalog(current_user.user_id)
            return response
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except UserMcpError as exc:
            raise http_error(exc) from exc

    return router


def oauth_state_signer_from_settings(
    settings: McpSettings,
) -> McpOAuthStateSigner | None:
    if settings.mcp_oauth_state_signing_secret is None:
        return None
    return McpOAuthStateSigner(
        secret=settings.mcp_oauth_state_signing_secret,
        ttl_seconds=600,
        clock_skew_seconds=settings.mcp_oauth_state_clock_skew_seconds,
    )


def require_signed_oauth_state(
    *,
    signer: McpOAuthStateSigner | None,
    state: str,
    server_id: str | None,
) -> SignedMcpOAuthState:
    if signer is None:
        raise McpOAuthStateError
    return signer.verify(state, server_id=server_id)


def http_error(exc: UserMcpError) -> HTTPException:
    if isinstance(exc, McpServerNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP server was not found",
        )
    if isinstance(exc, McpPresetNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="MCP preset was not found",
        )
    if isinstance(
        exc,
        (
            McpAuthRequiredError,
            McpInvalidAuthTypeError,
            McpPresetConfigurationError,
            McpServerUrlPolicyError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if isinstance(exc, McpServerLabelConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, (McpOAuthConfigurationError, McpOAuthStateError)):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if isinstance(exc, McpConnectionTestError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="MCP server operation failed",
    )


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="MCP settings are unavailable",
    )


def oauth_html(title: str, body: str) -> str:
    safe_title = escape(title)
    safe_body = escape(body)
    return (
        "<!doctype html>"
        '<html lang="en">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{safe_title}</title>"
        "</head>"
        "<body>"
        f"<h1>{safe_title}</h1>"
        f"<p>{safe_body}</p>"
        "</body>"
        "</html>"
    )

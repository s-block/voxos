import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from voxos.api.request_body import read_bounded_request_body
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.mcp.credential_broker import (
    McpCredentialBrokerAuthorizationError,
    McpCredentialBrokerReauthenticationRequiredError,
    McpCredentialBrokerRequest,
    McpCredentialBrokerResponse,
    McpCredentialBrokerService,
    McpCredentialBrokerUpstreamError,
)
from voxos.mcp.delegation import (
    McpCredentialDelegationError,
    McpCredentialDelegationSigner,
)
from voxos.mcp.oauth import McpOAuthHttpClient

BROKER_ROUTE = "/api/internal/mcp/credential-broker/v1/access-token"
MAX_DELEGATION_BYTES = 8192
MAX_BROKER_REQUEST_BODY_BYTES = 1024


def create_mcp_credential_broker_router(
    *,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    http_client: httpx.AsyncClient,
    delegation_signer: McpCredentialDelegationSigner | None,
) -> APIRouter:
    """Create the signed, tenant-scoped credential broker route."""
    router = APIRouter(tags=["internal-mcp"])

    @router.post(
        BROKER_ROUTE,
        response_model=McpCredentialBrokerResponse,
    )
    async def issue_access_token(
        request: Request,
        response: Response,
    ) -> McpCredentialBrokerResponse:
        response.headers["Cache-Control"] = "no-store"
        if delegation_signer is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Credential delegation is unavailable",
            )
        assertion = _bearer_assertion(request)
        try:
            delegation = delegation_signer.verify(assertion)
        except McpCredentialDelegationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Credential delegation was rejected",
            ) from exc

        request_body = await _broker_request_body(request)
        scoped_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=delegation.user_id,
        )
        try:
            async with scoped_factory() as session:
                return await McpCredentialBrokerService(
                    session=session,
                    secret_vault=secret_vault,
                    oauth_client=McpOAuthHttpClient(http_client),
                ).issue_access_token(
                    delegation=delegation,
                    request=request_body,
                )
        except McpCredentialBrokerAuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Credential delegation is not authorized",
            ) from exc
        except McpCredentialBrokerReauthenticationRequiredError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Provider authorization must be completed again",
            ) from exc
        except McpCredentialBrokerUpstreamError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Provider token refresh failed",
            ) from exc
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Credential broker is temporarily unavailable",
            ) from exc

    return router


async def _broker_request_body(request: Request) -> McpCredentialBrokerRequest:
    body = await read_bounded_request_body(
        request,
        max_bytes=MAX_BROKER_REQUEST_BODY_BYTES,
        too_large_detail="Credential broker request is too large",
    )

    try:
        return McpCredentialBrokerRequest.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Credential broker request is invalid",
        ) from exc


def _bearer_assertion(request: Request) -> str:
    values = request.headers.getlist("Authorization")
    if len(values) != 1:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credential delegation was rejected",
        )
    scheme, separator, assertion = values[0].partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not assertion
        or assertion != assertion.strip()
        or len(assertion.encode()) > MAX_DELEGATION_BYTES
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credential delegation was rejected",
        )
    return assertion

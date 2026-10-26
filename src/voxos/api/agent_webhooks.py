import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from voxos.agent_identity.services import AgentIdentityNotFoundError, AgentIdentityService
from voxos.agent_runtime.models import JsonObject
from voxos.agent_runtime.run_store import AgentRunRelationshipError, SqlModelAgentRunStore
from voxos.agent_runtime.webhook_models import (
    AgentWebhook,
    AgentWebhookCreateRequest,
    AgentWebhookCredential,
    AgentWebhookDeliveryAccepted,
    AgentWebhookUpdateRequest,
    AgentWebhookView,
)
from voxos.agent_runtime.webhook_store import (
    AgentWebhookAuthorizationError,
    AgentWebhookIngressLookupService,
    AgentWebhookNotFoundError,
    AgentWebhookPayloadError,
    AgentWebhookStore,
    AgentWebhookTriggerService,
    IssuedAgentWebhook,
)
from voxos.api.request_body import read_bounded_request_body
from voxos.auth.models import AuthenticatedUser
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import (
    agent_webhook_session_factory,
    tenant_scoped_session_factory,
    tenant_user_context,
)

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
MAX_WEBHOOK_BODY_BYTES = 16 * 1024
MAX_WEBHOOK_TOKEN_BYTES = 512
MAX_IDEMPOTENCY_KEY_BYTES = 256
WEBHOOK_NOT_FOUND = "Webhook was not found"
WEBHOOK_UNAUTHORIZED = "Webhook token was rejected"
WEBHOOKS_UNAVAILABLE = "Agent webhooks are temporarily unavailable"
AMBIGUOUS_IDEMPOTENCY_KEY = "Supply only one idempotency key header"
INVALID_IDEMPOTENCY_KEY = "Idempotency key is invalid"
INVALID_JSON = "Webhook request must contain valid JSON"
JSON_OBJECT_REQUIRED = "Webhook request must contain a JSON object"
logger = logging.getLogger(__name__)


class AgentWebhookListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    webhooks: tuple[AgentWebhookView, ...] = Field(default_factory=tuple)


def create_agent_webhooks_router(
    *,
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    public_base_url: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api/agent-webhooks", tags=["agent-webhooks"])
    ingress_lookup = AgentWebhookIngressLookupService(
        session_factory=agent_webhook_session_factory(session_factory),
    )

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def store_for(user_id: str) -> AgentWebhookStore:
        scoped_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )
        return AgentWebhookStore(
            session_factory=scoped_factory,
            identity_service=AgentIdentityService(scoped_factory),
        )

    @router.get("", response_model=AgentWebhookListResponse)
    async def list_webhooks(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> AgentWebhookListResponse:
        try:
            webhooks = await store_for(current_user.user_id).list_webhooks(
                user_id=current_user.user_id,
                limit=limit,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return AgentWebhookListResponse(
            webhooks=tuple(webhook_view(webhook, public_base_url=public_base_url) for webhook in webhooks),
        )

    @router.post(
        "",
        response_model=AgentWebhookCredential,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_webhook(
        request_body: AgentWebhookCreateRequest,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentWebhookCredential:
        response.headers["Cache-Control"] = "no-store"
        try:
            issued = await store_for(current_user.user_id).create_webhook(
                user_id=current_user.user_id,
                request=request_body,
            )
        except AgentIdentityNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent was not found",
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        return credential_view(issued, public_base_url=public_base_url)

    @router.get("/{webhook_id}", response_model=AgentWebhookView)
    async def get_webhook(
        webhook_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentWebhookView:
        try:
            webhook = await store_for(current_user.user_id).get_webhook(
                user_id=current_user.user_id,
                webhook_id=webhook_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if webhook is None:
            raise not_found()
        return webhook_view(webhook, public_base_url=public_base_url)

    @router.patch("/{webhook_id}", response_model=AgentWebhookView)
    async def update_webhook(
        webhook_id: str,
        request_body: AgentWebhookUpdateRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentWebhookView:
        try:
            webhook = await store_for(current_user.user_id).update_webhook(
                user_id=current_user.user_id,
                webhook_id=webhook_id,
                request=request_body,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if webhook is None:
            raise not_found()
        return webhook_view(webhook, public_base_url=public_base_url)

    @router.post("/{webhook_id}/rotate-token", response_model=AgentWebhookCredential)
    async def rotate_token(
        webhook_id: str,
        response: Response,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> AgentWebhookCredential:
        response.headers["Cache-Control"] = "no-store"
        try:
            issued = await store_for(current_user.user_id).rotate_token(
                user_id=current_user.user_id,
                webhook_id=webhook_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if issued is None:
            raise not_found()
        return credential_view(issued, public_base_url=public_base_url)

    @router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_webhook(
        webhook_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> None:
        try:
            webhook = await store_for(current_user.user_id).delete_webhook(
                user_id=current_user.user_id,
                webhook_id=webhook_id,
            )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if webhook is None:
            raise not_found()

    @router.post(
        "/{webhook_id}/deliver",
        response_model=AgentWebhookDeliveryAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def deliver_webhook(
        webhook_id: str,
        request: Request,
        response: Response,
    ) -> AgentWebhookDeliveryAccepted:
        response.headers["Cache-Control"] = "no-store"
        token = webhook_token(request)
        try:
            webhook = await ingress_lookup.authorize(webhook_id=webhook_id, token=token)
        except AgentWebhookNotFoundError as exc:
            raise not_found() from exc
        except AgentWebhookAuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=WEBHOOK_UNAUTHORIZED,
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        require_json_content_type(request)
        body = await read_bounded_request_body(
            request,
            max_bytes=MAX_WEBHOOK_BODY_BYTES,
            too_large_detail="Webhook request is too large",
        )
        event = parse_webhook_event(body)
        idempotency_key = request_idempotency_key(request)
        scoped_factory = tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=webhook.user_id,
        )
        try:
            with tenant_user_context(webhook.user_id):
                run = await AgentWebhookTriggerService(
                    session_factory=scoped_factory,
                    run_store=SqlModelAgentRunStore(scoped_factory),
                    identity_service=AgentIdentityService(scoped_factory),
                ).trigger(
                    webhook=webhook,
                    event=event,
                    idempotency_key=idempotency_key,
                )
        except (AgentIdentityNotFoundError, AgentRunRelationshipError) as exc:
            raise not_found() from exc
        except AgentWebhookPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Webhook request is too large after rendering",
            ) from exc
        except SQLAlchemyError as exc:
            raise unavailable() from exc

        return AgentWebhookDeliveryAccepted(webhook_id=webhook.id, run_id=run.id)

    return router


def webhook_token(request: Request) -> str:
    if request.query_params.getlist("token"):
        raise unauthorized()
    authorization_values = request.headers.getlist("Authorization")
    token_values = request.headers.getlist("X-Voxos-Webhook-Token")
    if bool(authorization_values) == bool(token_values):
        raise unauthorized()
    if authorization_values:
        if len(authorization_values) != 1:
            raise unauthorized()
        scheme, separator, token = authorization_values[0].partition(" ")
        if not separator or scheme.lower() != "bearer":
            raise unauthorized()
    else:
        if len(token_values) != 1:
            raise unauthorized()
        token = token_values[0]
    if not token or not token.isascii() or token != token.strip() or len(token.encode()) > MAX_WEBHOOK_TOKEN_BYTES:
        raise unauthorized()
    return token


def request_idempotency_key(request: Request) -> str | None:
    standard_values = request.headers.getlist("Idempotency-Key")
    voxos_values = request.headers.getlist("X-Voxos-Idempotency-Key")
    if standard_values and voxos_values:
        raise invalid_request(AMBIGUOUS_IDEMPOTENCY_KEY)
    values = standard_values or voxos_values
    if not values:
        return None
    if len(values) != 1:
        raise invalid_request(INVALID_IDEMPOTENCY_KEY)
    value = values[0]
    if not value or value != value.strip() or len(value.encode()) > MAX_IDEMPOTENCY_KEY_BYTES:
        raise invalid_request(INVALID_IDEMPOTENCY_KEY)
    return value


def require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Webhook request must use application/json",
        )


def parse_webhook_event(body: bytes) -> JsonObject:
    try:
        parsed = orjson.loads(body)
    except orjson.JSONDecodeError as exc:
        raise invalid_request(INVALID_JSON) from exc
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise invalid_request(JSON_OBJECT_REQUIRED)
    return parsed


def webhook_view(webhook: AgentWebhook, *, public_base_url: str | None) -> AgentWebhookView:
    path = f"/api/agent-webhooks/{webhook.id}/deliver"
    return AgentWebhookView(
        **webhook.model_dump(),
        trigger_path=path,
        trigger_url=f"{public_base_url}{path}" if public_base_url is not None else None,
    )


def credential_view(
    issued: IssuedAgentWebhook,
    *,
    public_base_url: str | None,
) -> AgentWebhookCredential:
    return AgentWebhookCredential(
        **webhook_view(issued.webhook, public_base_url=public_base_url).model_dump(),
        token=issued.token,
    )


def not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=WEBHOOK_NOT_FOUND)


def unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=WEBHOOK_UNAUTHORIZED)


def invalid_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=WEBHOOKS_UNAVAILABLE,
    )

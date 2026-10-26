from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.inbound_sms import InboundSmsIngressService
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.services import UserIntegrationService
from voxos.whatsapp.errors import (
    WhatsAppChallengeNotReadyError,
    WhatsAppFeatureDisabledError,
    WhatsAppIntegrationConflictError,
    WhatsAppIntegrationError,
    WhatsAppIntegrationNotFoundError,
    WhatsAppMobileRegistrationNotFoundError,
    WhatsAppSourceResourceError,
)
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)
from voxos.whatsapp.models import (
    WhatsAppChallengeRequest,
    WhatsAppChallengeResponse,
    WhatsAppLinkRequest,
    WhatsAppMobileRegistrationCandidateRequest,
    WhatsAppMobileRegistrationCompleteRequest,
    WhatsAppMobileRegistrationRequest,
    WhatsAppMobileRegistrationStatus,
    WhatsAppSetupOptionsResponse,
    WhatsAppStatusResponse,
)
from voxos.whatsapp.service import WhatsAppIntegrationService
from voxos.whatsapp.settings import WhatsAppWorkerSettings

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
MOBILE_REGISTRATION_ALREADY_ACTIVE = "A WhatsApp mobile registration is already active."


def create_whatsapp_router(
    *,
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    settings: WhatsAppWorkerSettings,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/integrations/whatsapp",
        tags=["integrations"],
    )
    provider_registry = IntegrationProviderRegistry(
        telnyx=TelnyxIntegrationProvider(),
        twilio=TwilioIntegrationProvider(),
    )

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_factory(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    def whatsapp_service(
        *,
        user_id: str,
        session: AsyncSession,
    ) -> WhatsAppIntegrationService:
        factory = scoped_factory(user_id)
        return WhatsAppIntegrationService(
            session=session,
            session_factory=factory,
            secret_vault=secret_vault,
            settings=settings,
        )

    def sms_ingress(
        *,
        user_id: str,
        session: AsyncSession,
    ) -> InboundSmsIngressService:
        factory = scoped_factory(user_id)
        return InboundSmsIngressService(
            UserIntegrationService(
                session=session,
                secret_vault=secret_vault,
                public_base_url=None,
                provider_registry=provider_registry,
                session_factory=factory,
                authorization_service=SqlModelToolAuthorizationService(
                    session_factory=factory,
                ),
            ),
        )

    @router.get("/options", response_model=WhatsAppSetupOptionsResponse)
    async def list_options(
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppSetupOptionsResponse:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).list_setup_options(user_id=current_user.user_id)
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error

    @router.post(
        "/link",
        response_model=WhatsAppStatusResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_link(
        request_body: WhatsAppLinkRequest,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppStatusResponse:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).start_link(
                    user_id=current_user.user_id,
                    source_resource_id=request_body.source_resource_id,
                    method=request_body.method,
                )
        except IntegrityError as error:
            raise whatsapp_http_error(
                WhatsAppIntegrationConflictError(),
            ) from error
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.post(
        "/mobile-registration",
        response_model=WhatsAppMobileRegistrationStatus,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_mobile_registration(
        request_body: WhatsAppMobileRegistrationRequest,
        response: Response,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppMobileRegistrationStatus:
        no_store(response)
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppMobileRegistrationService(
                    session=session,
                    secret_vault=secret_vault,
                ).start(
                    user_id=current_user.user_id,
                    source_resource_id=request_body.source_resource_id,
                    channel=request_body.channel,
                    integration_service=whatsapp_service(
                        user_id=current_user.user_id,
                        session=session,
                    ),
                )
        except IntegrityError as error:
            raise whatsapp_http_error(
                WhatsAppIntegrationConflictError(
                    MOBILE_REGISTRATION_ALREADY_ACTIVE,
                ),
            ) from error
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.get(
        "/mobile-registration/{registration_id}",
        response_model=WhatsAppMobileRegistrationStatus,
    )
    async def get_mobile_registration(
        registration_id: str,
        response: Response,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppMobileRegistrationStatus:
        no_store(response)
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppMobileRegistrationService(
                    session=session,
                    secret_vault=secret_vault,
                ).get_status(
                    user_id=current_user.user_id,
                    registration_id=registration_id,
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.post(
        "/mobile-registration/{registration_id}/reject",
        response_model=WhatsAppMobileRegistrationStatus,
    )
    async def reject_mobile_registration_candidate(
        registration_id: str,
        request_body: WhatsAppMobileRegistrationCandidateRequest,
        response: Response,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppMobileRegistrationStatus:
        no_store(response)
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppMobileRegistrationService(
                    session=session,
                    secret_vault=secret_vault,
                ).reject_candidate(
                    user_id=current_user.user_id,
                    registration_id=registration_id,
                    candidate_id=request_body.candidate_id,
                    ingress=sms_ingress(
                        user_id=current_user.user_id,
                        session=session,
                    ),
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.post(
        "/mobile-registration/{registration_id}/complete",
        response_model=WhatsAppStatusResponse,
    )
    async def complete_mobile_registration(
        registration_id: str,
        request_body: WhatsAppMobileRegistrationCompleteRequest,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppStatusResponse:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppMobileRegistrationService(
                    session=session,
                    secret_vault=secret_vault,
                ).complete(
                    user_id=current_user.user_id,
                    registration_id=registration_id,
                    candidate_id=request_body.candidate_id,
                    method=request_body.method,
                    ingress=sms_ingress(
                        user_id=current_user.user_id,
                        session=session,
                    ),
                    integration_service=whatsapp_service(
                        user_id=current_user.user_id,
                        session=session,
                    ),
                )
        except IntegrityError as error:
            raise whatsapp_http_error(
                WhatsAppIntegrationConflictError(),
            ) from error
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.delete(
        "/mobile-registration/{registration_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def cancel_mobile_registration(
        registration_id: str,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> Response:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                await WhatsAppMobileRegistrationService(
                    session=session,
                    secret_vault=secret_vault,
                ).cancel(
                    user_id=current_user.user_id,
                    registration_id=registration_id,
                    ingress=sms_ingress(
                        user_id=current_user.user_id,
                        session=session,
                    ),
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get(
        "/{integration_id}/status",
        response_model=WhatsAppStatusResponse,
    )
    async def get_status(
        integration_id: str,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppStatusResponse:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).get_status(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.get(
        "/{integration_id}/challenge",
        response_model=WhatsAppChallengeResponse,
    )
    async def get_challenge(
        integration_id: str,
        response: Response,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppChallengeResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).get_challenge(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.post(
        "/{integration_id}/challenge",
        response_model=WhatsAppChallengeResponse,
    )
    async def request_challenge(
        integration_id: str,
        request_body: WhatsAppChallengeRequest,
        response: Response,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppChallengeResponse:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).request_challenge(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    method=request_body.method,
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    @router.post(
        "/{integration_id}/relink",
        response_model=WhatsAppStatusResponse,
    )
    async def relink(
        integration_id: str,
        request_body: WhatsAppChallengeRequest,
        current_user: Annotated[
            AuthenticatedUser,
            Depends(authenticated_user),
        ],
    ) -> WhatsAppStatusResponse:
        try:
            factory = scoped_factory(current_user.user_id)
            async with factory() as session:
                return await WhatsAppIntegrationService(
                    session=session,
                    session_factory=factory,
                    secret_vault=secret_vault,
                    settings=settings,
                ).relink(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    method=request_body.method,
                )
        except SQLAlchemyError as error:
            raise whatsapp_database_unavailable() from error
        except WhatsAppIntegrationError as error:
            raise whatsapp_http_error(error) from error

    return router


def whatsapp_http_error(error: WhatsAppIntegrationError) -> HTTPException:
    if isinstance(
        error,
        (
            WhatsAppIntegrationNotFoundError,
            WhatsAppMobileRegistrationNotFoundError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        )
    if isinstance(error, WhatsAppChallengeNotReadyError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="challenge_not_ready: WhatsApp link code is not ready yet.",
        )
    if isinstance(error, WhatsAppIntegrationConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        )
    if isinstance(error, WhatsAppSourceResourceError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        )
    if isinstance(error, WhatsAppFeatureDisabledError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        )
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(error),
    )


def whatsapp_database_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="WhatsApp integration storage is temporarily unavailable.",
    )


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

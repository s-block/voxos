import logging
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import parse_qsl

import orjson
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, status
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.api.settings import ApiSettings
from voxos.auth.models import AuthenticatedUser
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import (
    integration_webhook_session_factory,
    tenant_scoped_session_factory,
)
from voxos.integrations.errors import (
    IntegrationCredentialError,
    IntegrationError,
    IntegrationNotFoundError,
    IntegrationResourceError,
    IntegrationToolPermissionError,
    IntegrationUpstreamError,
    IntegrationWebhookError,
)
from voxos.integrations.models import (
    CommunicationOutboundCallRequest,
    CommunicationOutboundSmsRequest,
    IntegrationActionResponse,
    IntegrationCallActivityListResponse,
    IntegrationCallLatencyReport,
    IntegrationListResponse,
    IntegrationMessageActivityListResponse,
    IntegrationProviderType,
    IntegrationResourceListResponse,
    IntegrationWebhookPurpose,
    TelnyxPhoneSetupRequest,
    TelnyxPhoneSetupResponse,
    TelnyxReadinessResponse,
    TelnyxRepairRequest,
    TwilioCredentialSetupRequest,
    TwilioCredentialSetupResponse,
    TwilioOutboundCallRequest,
    TwilioOutboundSmsRequest,
    TwilioResourceConnectRequest,
    TwilioResourceConnectResponse,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.telnyx.media_streams import (
    TelnyxMediaStreamBridge,
    livekit_telnyx_audio_bridge_factory,
)
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.telnyx.webhooks import (
    TELNYX_SIGNATURE_HEADER_NAME,
    TELNYX_TIMESTAMP_HEADER_NAME,
)
from voxos.integrations.providers.twilio.constants import TWILIO_SIGNATURE_HEADER_NAME
from voxos.integrations.providers.twilio.media_streams import (
    TwilioMediaStreamBridge,
    livekit_audio_bridge_factory,
    missing_livekit_error,
)
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.services import (
    OUTBOUND_CALL_SESSION_QUERY_PARAMETER,
    IntegrationWebhookRouteContext,
    IntegrationWebhookRouteLookupService,
    UserIntegrationService,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandCompleter,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
)
from voxos.integrations.telnyx_webhooks import TelnyxWebhookProcessor
from voxos.integrations.webhooks import TwilioWebhookProcessor
from voxos.voice.call_routing import (
    LiveKitCallDemandCoordinator,
    LiveKitCallPreflight,
    LiveKitCallRouter,
)
from voxos.voice.livekit_settings import UserLiveKitSettingsService
from voxos.voice.livekit_tokens import (
    LiveKitVoiceTokenService,
    ResolvedLiveKitCredentials,
)
from voxos.voice.settings import VoiceAgentSettings
from voxos.whatsapp.errors import WhatsAppIntegrationNotFoundError
from voxos.whatsapp.service import WhatsAppIntegrationService
from voxos.whatsapp.settings import WhatsAppWorkerSettings

AuthenticatedUserDependency = Callable[[Request], Awaitable[AuthenticatedUser]]
logger = logging.getLogger(__name__)


def create_integrations_router(
    auth_dependency: AuthenticatedUserDependency,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    api_settings: ApiSettings,
    voice_settings: VoiceAgentSettings,
    livekit_token_service: LiveKitVoiceTokenService,
    whatsapp_settings: WhatsAppWorkerSettings | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/integrations", tags=["integrations"])
    provider_registry = IntegrationProviderRegistry(
        telnyx=TelnyxIntegrationProvider(),
        twilio=TwilioIntegrationProvider(),
    )
    livekit_call_router = LiveKitCallRouter(
        session_factory=session_factory,
        settings=voice_settings,
        token_template=livekit_token_service,
    )
    livekit_call_preflight = LiveKitCallPreflight(
        settings=voice_settings,
        secret_vault=secret_vault,
    )
    request_voice_worker = LiveKitCallDemandCoordinator(
        call_router=livekit_call_router,
        secret_vault=secret_vault,
    )
    resolved_whatsapp_settings = whatsapp_settings or WhatsAppWorkerSettings()

    async def authenticated_user(request: Request) -> AuthenticatedUser:
        return await auth_dependency(request)

    def scoped_session_factory_for(user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=user_id,
        )

    def service_for_session(user_id: str, session: AsyncSession) -> UserIntegrationService:
        return UserIntegrationService(
            session=session,
            secret_vault=secret_vault,
            public_base_url=api_settings.api_public_base_url,
            provider_registry=provider_registry,
            session_factory=scoped_session_factory_for(user_id),
            authorization_service=SqlModelToolAuthorizationService(
                session_factory=scoped_session_factory_for(user_id),
            ),
            voice_worker_demand_requester=request_voice_worker,
            voice_worker_readiness_waiter=request_voice_worker.wait,
            voice_agent_dispatcher=request_voice_worker.dispatch,
            voice_agent_readiness_waiter=request_voice_worker.wait_for_agent_ready,
            voice_worker_demand_canceller=request_voice_worker.cancel,
            voice_worker_demand_completer=request_voice_worker.complete,
            voice_call_preflight=livekit_call_preflight,
        )

    async def resolve_livekit_settings(user_id: str) -> ResolvedLiveKitCredentials:
        async with scoped_session_factory_for(user_id)() as session:
            return await UserLiveKitSettingsService(
                session=session,
                secret_vault=secret_vault,
            ).resolve_settings(user_id)

    @router.get("", response_model=IntegrationListResponse)
    async def list_integrations(
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).list_integrations(current_user.user_id)
        except SQLAlchemyError as exc:
            raise unavailable() from exc

    @router.get(
        "/{integration_id}/resources",
        response_model=IntegrationResourceListResponse,
    )
    async def list_resources(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationResourceListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).list_resources(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.get(
        "/{integration_id}/telnyx/readiness",
        response_model=TelnyxReadinessResponse,
    )
    async def verify_telnyx_readiness(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TelnyxReadinessResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).verify_telnyx_phone_configuration(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/{integration_id}/telnyx/repair",
        response_model=TelnyxReadinessResponse,
    )
    async def repair_telnyx_readiness(
        integration_id: str,
        request_body: TelnyxRepairRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TelnyxReadinessResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).repair_telnyx_phone_configuration(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    confirm_provider_changes=request_body.confirm_provider_changes,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.get(
        "/{integration_id}/messages",
        response_model=IntegrationMessageActivityListResponse,
    )
    async def list_message_activity(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> IntegrationMessageActivityListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).list_message_activity(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    limit=limit,
                    offset=offset,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.get(
        "/{integration_id}/calls",
        response_model=IntegrationCallActivityListResponse,
    )
    async def list_call_activity(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> IntegrationCallActivityListResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).list_call_activity(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    limit=limit,
                    offset=offset,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.get(
        "/{integration_id}/calls/latency",
        response_model=IntegrationCallLatencyReport,
    )
    async def call_latency_report(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
        provider: Annotated[IntegrationProviderType | None, Query()] = None,
    ) -> IntegrationCallLatencyReport:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).call_latency_report(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    provider=provider,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/twilio/setup",
        response_model=TwilioCredentialSetupResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def setup_twilio(
        request_body: TwilioCredentialSetupRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TwilioCredentialSetupResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).setup_twilio_credentials(
                    user_id=current_user.user_id,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/telnyx/setup",
        response_model=TelnyxPhoneSetupResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def setup_telnyx(
        request_body: TelnyxPhoneSetupRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TelnyxPhoneSetupResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).setup_telnyx_phone(
                    user_id=current_user.user_id,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/{integration_id}/twilio/resources",
        response_model=TwilioResourceConnectResponse,
    )
    async def connect_twilio_resources(
        integration_id: str,
        request_body: TwilioResourceConnectRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> TwilioResourceConnectResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).connect_twilio_resources(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.delete("/{integration_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def disconnect_integration(
        integration_id: str,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> Response:
        try:
            scoped_factory = scoped_session_factory_for(current_user.user_id)
            async with scoped_factory() as session:
                try:
                    whatsapp_disconnected = await WhatsAppIntegrationService(
                        session=session,
                        session_factory=scoped_factory,
                        secret_vault=secret_vault,
                        settings=resolved_whatsapp_settings,
                    ).begin_disconnect_if_whatsapp(
                        user_id=current_user.user_id,
                        integration_id=integration_id,
                    )
                except WhatsAppIntegrationNotFoundError:
                    whatsapp_disconnected = False
                if whatsapp_disconnected:
                    return Response(
                        status_code=status.HTTP_204_NO_CONTENT,
                    )
                await service_for_session(
                    current_user.user_id,
                    session,
                ).disconnect_integration(
                    user_id=current_user.user_id,
                    integration_id=integration_id,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/twilio/actions/sms",
        response_model=IntegrationActionResponse,
    )
    async def send_twilio_sms(
        request_body: TwilioOutboundSmsRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationActionResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).send_twilio_sms(user_id=current_user.user_id, request=request_body)
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/twilio/actions/call",
        response_model=IntegrationActionResponse,
    )
    async def start_twilio_call(
        request_body: TwilioOutboundCallRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationActionResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).start_twilio_outbound_call(
                    user_id=current_user.user_id,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc
        except Exception as exc:
            if not missing_livekit_error(exc):
                raise
            raise voice_routing_unavailable() from exc

    @router.post(
        "/telnyx/actions/sms",
        response_model=IntegrationActionResponse,
    )
    async def send_telnyx_sms(
        request_body: CommunicationOutboundSmsRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationActionResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).send_sms(
                    user_id=current_user.user_id,
                    provider=IntegrationProviderType.TELNYX,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc

    @router.post(
        "/telnyx/actions/call",
        response_model=IntegrationActionResponse,
    )
    async def start_telnyx_call(
        request_body: CommunicationOutboundCallRequest,
        current_user: Annotated[AuthenticatedUser, Depends(authenticated_user)],
    ) -> IntegrationActionResponse:
        try:
            async with scoped_session_factory_for(current_user.user_id)() as session:
                return await service_for_session(
                    current_user.user_id,
                    session,
                ).start_outbound_call(
                    user_id=current_user.user_id,
                    provider=IntegrationProviderType.TELNYX,
                    request=request_body,
                )
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        except IntegrationError as exc:
            raise integration_http_error(exc) from exc
        except Exception as exc:
            if not missing_livekit_error(exc):
                raise
            raise voice_routing_unavailable() from exc

    @router.post(
        "/webhooks/communications/{provider}/voice/{route_key}",
        include_in_schema=False,
    )
    async def communication_voice_webhook(
        provider: IntegrationProviderType,
        route_key: str,
        request: Request,
    ) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=provider,
            purpose=IntegrationWebhookPurpose.VOICE,
            route_key=route_key,
            request=request,
            voice_worker_demand_requester=request_voice_worker,
            voice_worker_readiness_waiter=request_voice_worker.wait,
            voice_worker_demand_canceller=request_voice_worker.cancel,
            voice_worker_demand_completer=request_voice_worker.complete,
        )

    @router.post(
        "/webhooks/communications/{provider}/sms/{route_key}",
        include_in_schema=False,
    )
    async def communication_sms_webhook(
        provider: IntegrationProviderType,
        route_key: str,
        request: Request,
    ) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=provider,
            purpose=IntegrationWebhookPurpose.SMS,
            route_key=route_key,
            request=request,
        )

    @router.post(
        "/webhooks/communications/{provider}/status/{route_key}",
        include_in_schema=False,
    )
    async def communication_status_webhook(
        provider: IntegrationProviderType,
        route_key: str,
        request: Request,
    ) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=provider,
            purpose=IntegrationWebhookPurpose.STATUS,
            route_key=route_key,
            request=request,
            voice_worker_demand_canceller=request_voice_worker.cancel,
            voice_worker_demand_completer=request_voice_worker.complete,
        )

    @router.post("/webhooks/twilio/voice/{route_key}", include_in_schema=False)
    async def twilio_voice_webhook(route_key: str, request: Request) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=IntegrationProviderType.TWILIO,
            purpose=IntegrationWebhookPurpose.VOICE,
            route_key=route_key,
            request=request,
            voice_worker_demand_requester=request_voice_worker,
            voice_worker_readiness_waiter=request_voice_worker.wait,
            voice_worker_demand_canceller=request_voice_worker.cancel,
            voice_worker_demand_completer=request_voice_worker.complete,
        )

    @router.post("/webhooks/twilio/sms/{route_key}", include_in_schema=False)
    async def twilio_sms_webhook(route_key: str, request: Request) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=IntegrationProviderType.TWILIO,
            purpose=IntegrationWebhookPurpose.SMS,
            route_key=route_key,
            request=request,
        )

    @router.post("/webhooks/twilio/status/{route_key}", include_in_schema=False)
    async def twilio_status_webhook(route_key: str, request: Request) -> Response:
        return await handle_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            api_settings=api_settings,
            provider=IntegrationProviderType.TWILIO,
            purpose=IntegrationWebhookPurpose.STATUS,
            route_key=route_key,
            request=request,
            voice_worker_demand_canceller=request_voice_worker.cancel,
            voice_worker_demand_completer=request_voice_worker.complete,
        )

    async def handle_twilio_media_stream(websocket: WebSocket, route_key: str) -> None:
        try:
            async with integration_webhook_session_factory(session_factory)() as lookup_session:
                route_context = await IntegrationWebhookRouteLookupService(
                    lookup_session,
                ).resolve(
                    provider=IntegrationProviderType.TWILIO,
                    purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
                    route_key=route_key,
                )
        except IntegrationWebhookError:
            await websocket.close(code=1008)
            return
        async with scoped_session_factory_for(route_context.route.user_id)() as session:
            service = UserIntegrationService(
                session=session,
                secret_vault=secret_vault,
                public_base_url=require_public_base_url(api_settings),
                provider_registry=provider_registry,
                session_factory=scoped_session_factory_for(route_context.route.user_id),
                voice_worker_demand_requester=request_voice_worker,
                voice_worker_readiness_waiter=request_voice_worker.wait,
                voice_worker_demand_canceller=request_voice_worker.cancel,
                voice_worker_demand_completer=request_voice_worker.complete,
            )
            bridge = TwilioMediaStreamBridge(
                service=service,
                audio_bridge_factory=livekit_audio_bridge_factory(
                    call_router=livekit_call_router,
                    livekit_settings_resolver=resolve_livekit_settings,
                    voice_agent_name=voice_settings.voice_agent_name,
                ),
            )
            try:
                await bridge.handle_websocket(
                    websocket=websocket,
                    route_context=route_context,
                    signature=websocket.headers.get(TWILIO_SIGNATURE_HEADER_NAME),
                )
            except (IntegrationWebhookError, SQLAlchemyError) as exc:
                await websocket.close(code=1011)
                if not isinstance(exc, SQLAlchemyError):
                    return
                raise
            except Exception as exc:
                if missing_livekit_error(exc):
                    logger.warning(
                        "Closing Twilio media stream because LiveKit voice routing is not configured",
                        extra={
                            "route_id": route_context.route.id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    await websocket.close(code=1011)
                    return
                raise

    async def handle_telnyx_media_stream(websocket: WebSocket, route_key: str) -> None:
        try:
            async with integration_webhook_session_factory(session_factory)() as lookup_session:
                route_context = await IntegrationWebhookRouteLookupService(
                    lookup_session,
                ).resolve(
                    provider=IntegrationProviderType.TELNYX,
                    purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
                    route_key=route_key,
                )
        except IntegrationWebhookError:
            await websocket.close(code=1008)
            return
        async with scoped_session_factory_for(route_context.route.user_id)() as session:
            service = UserIntegrationService(
                session=session,
                secret_vault=secret_vault,
                public_base_url=require_public_base_url(api_settings),
                provider_registry=provider_registry,
                session_factory=scoped_session_factory_for(route_context.route.user_id),
                voice_worker_demand_requester=request_voice_worker,
                voice_worker_readiness_waiter=request_voice_worker.wait,
                voice_worker_demand_canceller=request_voice_worker.cancel,
                voice_worker_demand_completer=request_voice_worker.complete,
            )
            bridge = TelnyxMediaStreamBridge(
                service=service,
                audio_bridge_factory=livekit_telnyx_audio_bridge_factory(
                    call_router=livekit_call_router,
                    livekit_settings_resolver=resolve_livekit_settings,
                    voice_agent_name=voice_settings.voice_agent_name,
                ),
            )
            try:
                await bridge.handle_websocket(
                    websocket=websocket,
                    route_context=route_context,
                    outbound_call_session_id=websocket.query_params.get(
                        OUTBOUND_CALL_SESSION_QUERY_PARAMETER,
                    ),
                )
            except (IntegrationWebhookError, SQLAlchemyError) as exc:
                await websocket.close(code=1011)
                if not isinstance(exc, SQLAlchemyError):
                    return
                raise
            except Exception as exc:
                if missing_livekit_error(exc):
                    logger.warning(
                        "Closing Telnyx media stream because LiveKit voice routing is not configured",
                        extra={
                            "route_id": route_context.route.id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    await websocket.close(code=1011)
                    return
                raise

    @router.websocket("/streams/communications/{provider}/media/{route_key}")
    async def communication_media_stream(
        websocket: WebSocket,
        provider: IntegrationProviderType,
        route_key: str,
    ) -> None:
        if provider == IntegrationProviderType.TWILIO:
            await handle_twilio_media_stream(websocket=websocket, route_key=route_key)
            return
        if provider == IntegrationProviderType.TELNYX:
            await handle_telnyx_media_stream(websocket=websocket, route_key=route_key)
            return

    @router.websocket("/streams/twilio/media/{route_key}")
    async def twilio_media_stream(websocket: WebSocket, route_key: str) -> None:
        await handle_twilio_media_stream(websocket=websocket, route_key=route_key)

    @router.websocket("/streams/telnyx/media/{route_key}")
    async def telnyx_media_stream(websocket: WebSocket, route_key: str) -> None:
        await handle_telnyx_media_stream(websocket=websocket, route_key=route_key)

    return router


async def handle_communication_webhook(
    *,
    provider_registry: IntegrationProviderRegistry,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    api_settings: ApiSettings,
    provider: IntegrationProviderType,
    purpose: IntegrationWebhookPurpose,
    route_key: str,
    request: Request,
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None,
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None,
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None,
    voice_worker_demand_completer: VoiceWorkerDemandCompleter | None = None,
) -> Response:
    if provider not in {IntegrationProviderType.TWILIO, IntegrationProviderType.TELNYX}:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook provider is not configured.",
        )
    try:
        async with integration_webhook_session_factory(session_factory)() as lookup_session:
            route_context = await IntegrationWebhookRouteLookupService(lookup_session).resolve(
                provider=provider,
                purpose=purpose,
                route_key=route_key,
            )
    except SQLAlchemyError as exc:
        log_communication_webhook_database_failure(
            error=exc,
            provider=provider,
            purpose=purpose,
        )
        raise unavailable() from exc
    except IntegrationWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook route was not found.") from exc

    public_base_url = require_public_base_url(api_settings)
    if provider == IntegrationProviderType.TELNYX:
        return await handle_telnyx_communication_webhook(
            provider_registry=provider_registry,
            session_factory=session_factory,
            secret_vault=secret_vault,
            public_base_url=public_base_url,
            purpose=purpose,
            route_context=route_context,
            request=request,
            voice_worker_demand_requester=voice_worker_demand_requester,
            voice_worker_readiness_waiter=voice_worker_readiness_waiter,
            voice_worker_demand_canceller=voice_worker_demand_canceller,
            voice_worker_demand_completer=voice_worker_demand_completer,
        )

    form_params = await twilio_form_params(request)
    params = dict(form_params)
    signature = request.headers.get(TWILIO_SIGNATURE_HEADER_NAME)
    outbound_call_session_id = request.query_params.get(
        OUTBOUND_CALL_SESSION_QUERY_PARAMETER,
    )
    try:
        async with tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=route_context.route.user_id,
        )() as tenant_session:
            processor = TwilioWebhookProcessor(
                session=tenant_session,
                secret_vault=secret_vault,
                public_base_url=public_base_url,
                provider_registry=provider_registry,
                session_factory=tenant_scoped_session_factory(
                    session_factory=session_factory,
                    user_id=route_context.route.user_id,
                ),
                authorization_service=SqlModelToolAuthorizationService(
                    session_factory=tenant_scoped_session_factory(
                        session_factory=session_factory,
                        user_id=route_context.route.user_id,
                    ),
                ),
                voice_worker_demand_requester=voice_worker_demand_requester,
                voice_worker_readiness_waiter=voice_worker_readiness_waiter,
                voice_worker_demand_canceller=voice_worker_demand_canceller,
                voice_worker_demand_completer=voice_worker_demand_completer,
            )
            if purpose == IntegrationWebhookPurpose.VOICE:
                twiml = await processor.handle_voice(
                    route_context=route_context,
                    params=params,
                    signature_params=form_params,
                    signature=signature,
                    outbound_call_session_id=outbound_call_session_id,
                )
            elif purpose == IntegrationWebhookPurpose.SMS:
                twiml = await processor.handle_sms(
                    route_context=route_context,
                    params=params,
                    signature_params=form_params,
                    signature=signature,
                )
            else:
                twiml = await processor.handle_status(
                    route_context=route_context,
                    params=params,
                    signature_params=form_params,
                    signature=signature,
                    outbound_call_session_id=outbound_call_session_id,
                )
    except SQLAlchemyError as exc:
        log_communication_webhook_database_failure(
            error=exc,
            provider=provider,
            purpose=purpose,
            route_context=route_context,
        )
        raise unavailable() from exc
    except IntegrationUpstreamError as exc:
        raise integration_http_error(exc) from exc
    except IntegrationWebhookError as exc:
        async with tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=route_context.route.user_id,
        )() as tenant_session:
            processor = TwilioWebhookProcessor(
                session=tenant_session,
                secret_vault=secret_vault,
                public_base_url=public_base_url,
                provider_registry=provider_registry,
                session_factory=tenant_scoped_session_factory(
                    session_factory=session_factory,
                    user_id=route_context.route.user_id,
                ),
                authorization_service=SqlModelToolAuthorizationService(
                    session_factory=tenant_scoped_session_factory(
                        session_factory=session_factory,
                        user_id=route_context.route.user_id,
                    ),
                ),
                voice_worker_demand_requester=voice_worker_demand_requester,
                voice_worker_readiness_waiter=voice_worker_readiness_waiter,
                voice_worker_demand_canceller=voice_worker_demand_canceller,
                voice_worker_demand_completer=voice_worker_demand_completer,
            )
            await processor.reject_after_route(route_context=route_context, reason=str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook rejected.") from exc
    except Exception as exc:
        if not missing_livekit_error(exc):
            raise
        raise voice_routing_unavailable() from exc

    return Response(content=twiml, media_type="application/xml")


async def handle_telnyx_communication_webhook(
    *,
    provider_registry: IntegrationProviderRegistry,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    public_base_url: str,
    purpose: IntegrationWebhookPurpose,
    route_context: IntegrationWebhookRouteContext,
    request: Request,
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None,
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None,
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None,
    voice_worker_demand_completer: VoiceWorkerDemandCompleter | None = None,
) -> Response:
    body = await request.body()
    try:
        parsed = orjson.loads(body)
    except orjson.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook body.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook body.")
    try:
        async with tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=route_context.route.user_id,
        )() as tenant_session:
            processor = TelnyxWebhookProcessor(
                session=tenant_session,
                secret_vault=secret_vault,
                public_base_url=public_base_url,
                provider_registry=provider_registry,
                session_factory=tenant_scoped_session_factory(
                    session_factory=session_factory,
                    user_id=route_context.route.user_id,
                ),
                authorization_service=SqlModelToolAuthorizationService(
                    session_factory=tenant_scoped_session_factory(
                        session_factory=session_factory,
                        user_id=route_context.route.user_id,
                    ),
                ),
                voice_worker_demand_requester=voice_worker_demand_requester,
                voice_worker_readiness_waiter=voice_worker_readiness_waiter,
                voice_worker_demand_canceller=voice_worker_demand_canceller,
                voice_worker_demand_completer=voice_worker_demand_completer,
            )
            if purpose == IntegrationWebhookPurpose.VOICE:
                result = await processor.handle_voice(
                    route_context=route_context,
                    body=body,
                    payload=parsed,
                    signature=request.headers.get(TELNYX_SIGNATURE_HEADER_NAME),
                    timestamp=request.headers.get(TELNYX_TIMESTAMP_HEADER_NAME),
                )
            elif purpose == IntegrationWebhookPurpose.SMS:
                result = await processor.handle_sms(
                    route_context=route_context,
                    body=body,
                    payload=parsed,
                    signature=request.headers.get(TELNYX_SIGNATURE_HEADER_NAME),
                    timestamp=request.headers.get(TELNYX_TIMESTAMP_HEADER_NAME),
                )
            else:
                result = await processor.handle_status(
                    route_context=route_context,
                    body=body,
                    payload=parsed,
                    signature=request.headers.get(TELNYX_SIGNATURE_HEADER_NAME),
                    timestamp=request.headers.get(TELNYX_TIMESTAMP_HEADER_NAME),
                )
    except SQLAlchemyError as exc:
        log_communication_webhook_database_failure(
            error=exc,
            provider=IntegrationProviderType.TELNYX,
            purpose=purpose,
            route_context=route_context,
        )
        raise unavailable() from exc
    except IntegrationUpstreamError as exc:
        raise integration_http_error(exc) from exc
    except IntegrationWebhookError as exc:
        async with tenant_scoped_session_factory(
            session_factory=session_factory,
            user_id=route_context.route.user_id,
        )() as tenant_session:
            processor = TelnyxWebhookProcessor(
                session=tenant_session,
                secret_vault=secret_vault,
                public_base_url=public_base_url,
                provider_registry=provider_registry,
                session_factory=tenant_scoped_session_factory(
                    session_factory=session_factory,
                    user_id=route_context.route.user_id,
                ),
                authorization_service=SqlModelToolAuthorizationService(
                    session_factory=tenant_scoped_session_factory(
                        session_factory=session_factory,
                        user_id=route_context.route.user_id,
                    ),
                ),
                voice_worker_demand_requester=voice_worker_demand_requester,
                voice_worker_readiness_waiter=voice_worker_readiness_waiter,
                voice_worker_demand_canceller=voice_worker_demand_canceller,
                voice_worker_demand_completer=voice_worker_demand_completer,
            )
            await processor.reject_after_route(route_context=route_context, reason=str(exc))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook rejected.") from exc
    except Exception as exc:
        if not missing_livekit_error(exc):
            raise
        raise voice_routing_unavailable() from exc

    return Response(content=orjson.dumps(result), media_type="application/json")


async def twilio_form_params(request: Request) -> tuple[tuple[str, str], ...]:
    body = await request.body()
    decoded = body.decode("utf-8")
    return tuple(parse_qsl(decoded, keep_blank_values=True))


def require_public_base_url(api_settings: ApiSettings) -> str:
    if api_settings.api_public_base_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_PUBLIC_BASE_URL is required for integrations.",
        )
    return api_settings.api_public_base_url


def voice_routing_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="LiveKit voice routing is temporarily unavailable.",
    )


def integration_http_error(exc: IntegrationError) -> HTTPException:
    if isinstance(exc, IntegrationNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, IntegrationCredentialError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, IntegrationUpstreamError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integration provider is temporarily unavailable.",
        )
    if isinstance(exc, IntegrationResourceError | IntegrationWebhookError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, IntegrationToolPermissionError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Integrations are unavailable.",
    )


def log_communication_webhook_database_failure(
    *,
    error: SQLAlchemyError,
    provider: IntegrationProviderType,
    purpose: IntegrationWebhookPurpose,
    route_context: IntegrationWebhookRouteContext | None = None,
) -> None:
    route = route_context.route if route_context is not None else None
    sanitized_error = SQLAlchemyError(
        f"{type(error).__name__} details redacted",
    )
    logger.exception(
        "Communication webhook database operation failed",
        extra={
            "integration_provider": provider.value,
            "webhook_purpose": purpose.value,
            "integration_id": route.integration_id if route is not None else None,
            "resource_id": route.resource_id if route is not None else None,
            "webhook_route_id": route.id if route is not None else None,
            "database_error_type": type(error).__name__,
        },
        exc_info=(SQLAlchemyError, sanitized_error, error.__traceback__),
    )

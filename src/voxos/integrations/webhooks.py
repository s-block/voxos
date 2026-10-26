from dataclasses import dataclass

from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from twilio.twiml.voice_response import VoiceResponse

from voxos.agent_runtime.tool_policy import ToolAuthorizationService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.errors import IntegrationResourceError, IntegrationWebhookError
from voxos.integrations.inbound_sms import InboundSmsEnvelope, InboundSmsIngressService
from voxos.integrations.models import (
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationCallStatus,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationWebhookPurpose,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    EventNormalizationRequest,
    WebhookVerificationRequest,
)
from voxos.integrations.providers.twilio.webhooks import (
    TWILIO_SIGNATURE_INVALID_ERROR,
    TwilioEventNormalizer,
    TwilioSignatureParams,
    TwilioWebhookVerifier,
    twilio_validator_params,
)
from voxos.integrations.records import (
    IntegrationCallSessionRecord,
    IntegrationMessageRecord,
    IntegrationResourceRecord,
)
from voxos.integrations.reply_dispatch import update_reply_delivery_status
from voxos.integrations.services import (
    IntegrationWebhookRouteContext,
    UserIntegrationService,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandCompleter,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
    media_stream_url_for_resource,
    normalize_twilio_call_status,
    normalize_twilio_message_status,
    outbound_call_callback_url,
    status_callback_url_for_resource,
    voice_url_for_resource,
)
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)

__all__ = ("TwilioWebhookProcessor", "twilio_validator_params")

TWILIO_STATUS_CALLBACK_MISSING_ERROR = "Twilio status callback URL is not configured."
WEBHOOK_ROUTE_RESOURCE_MISSING_ERROR = "Webhook route is not bound to a resource."
WEBHOOK_RESOURCE_NOT_FOUND_ERROR = "Webhook resource was not found."
TWILIO_ACCOUNT_MISMATCH_ERROR = "Twilio AccountSid does not match the integration."
TWILIO_NUMBER_MISMATCH_ERROR = "Twilio webhook does not match the configured number."
TWILIO_SMS_WEBHOOK_MISSING_ERROR = "Twilio SMS webhook URL is not configured."
TWILIO_EVENT_ID_MISSING_ERROR = "Twilio event ID is missing."


@dataclass(frozen=True, slots=True)
class TwilioWebhookProcessor:
    session: AsyncSession
    secret_vault: UserSecretVaultService
    public_base_url: str
    provider_registry: IntegrationProviderRegistry
    session_factory: AsyncSessionFactory
    authorization_service: ToolAuthorizationService | None = None
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None
    voice_worker_demand_completer: VoiceWorkerDemandCompleter | None = None

    async def handle_voice(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        params: dict[str, str],
        signature: str | None,
        signature_params: TwilioSignatureParams | None = None,
        outbound_call_session_id: str | None = None,
    ) -> str:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_twilio_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        self._validate_signature(
            expected_url=(
                outbound_call_callback_url(
                    voice_url_for_resource(resource),
                    outbound_call_session_id,
                )
                if outbound_call_session_id is not None
                else voice_url_for_resource(resource)
            ),
            purpose=IntegrationWebhookPurpose.VOICE,
            params=signature_params if signature_params is not None else params,
            signature=signature,
            auth_token=credentials.auth_token,
        )
        self._verify_account(params=params, account_sid=credentials.account_sid)
        self._verify_resource_number(params=params, resource=resource)
        normalized = await TwilioEventNormalizer().normalize_event(
            EventNormalizationRequest(
                provider=IntegrationProviderType.TWILIO,
                purpose=IntegrationWebhookPurpose.VOICE,
                params=params,
                provider_account_id=credentials.account_sid,
                resource_display_name=resource.display_name,
            ),
        )
        call_sid = required_normalized_event_id(normalized.external_event_id)
        if outbound_call_session_id is None:
            call_session = await service.start_twilio_call_session(
                user_id=route_context.route.user_id,
                integration_id=route_context.route.integration_id,
                resource_id=resource.id,
                direction=normalized.direction,
                call_sid=call_sid,
                from_number=params.get("From") or params.get("Caller"),
                to_number=params.get("To") or params.get("Called"),
                payload_json=dict(params),
            )
        else:
            try:
                call_session = await service.bind_outbound_call_session(
                    user_id=route_context.route.user_id,
                    integration_id=route_context.route.integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.TWILIO,
                    call_session_id=outbound_call_session_id,
                    external_call_id=call_sid,
                    payload_json=dict(params),
                    answered=True,
                )
            except IntegrationResourceError as exc:
                raise IntegrationWebhookError(str(exc)) from exc
        stream_url = media_stream_url_for_resource(resource)
        await service.record_communication_event(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=normalized.provider,
            event_type=normalized.event_type,
            direction=normalized.direction,
            external_event_id=normalized.external_event_id,
            idempotency_key=normalized.idempotency_key,
            payload_json=normalized.payload_json,
            safe_summary_json=normalized.safe_summary_json,
        )
        await service.record_audit(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            action=IntegrationAuditAction.EVENT_ACCEPTED,
            actor_type=IntegrationAuditActorType.WEBHOOK,
            actor_id=call_sid,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"event_type": "voice_start", "call_session_id": call_session.id},
        )
        await self.session.commit()
        try:
            await service.wait_for_voice_worker(call_session)
        except BaseException:
            await service.fail_call_route(
                call_session,
                failure_code="worker_admission_failed",
            )
            raise
        return twilio_stream_response(stream_url)

    async def handle_sms(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        params: dict[str, str],
        signature: str | None,
        signature_params: TwilioSignatureParams | None = None,
    ) -> str:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_twilio_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        self._validate_signature(
            expected_url=sms_url_for_resource(resource),
            purpose=IntegrationWebhookPurpose.SMS,
            params=signature_params if signature_params is not None else params,
            signature=signature,
            auth_token=credentials.auth_token,
        )
        self._verify_account(params=params, account_sid=credentials.account_sid)
        self._verify_resource_number(params=params, resource=resource)
        normalized = await TwilioEventNormalizer().normalize_event(
            EventNormalizationRequest(
                provider=IntegrationProviderType.TWILIO,
                purpose=IntegrationWebhookPurpose.SMS,
                params=params,
                provider_account_id=credentials.account_sid,
                resource_display_name=resource.display_name,
            ),
        )
        message_sid = required_normalized_event_id(normalized.external_event_id)
        envelope = InboundSmsEnvelope(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=normalized.provider,
            external_message_id=message_sid,
            external_event_id=normalized.external_event_id,
            event_type=normalized.event_type,
            idempotency_key=normalized.idempotency_key,
            from_number=required_param(params, "From"),
            to_number=required_param(params, "To"),
            body=params.get("Body", ""),
            segments=int_or_none(params.get("NumSegments")),
            received_at=utc_now(),
            payload_json=dict(params),
            event_payload_json=normalized.payload_json,
            safe_summary_json=normalized.safe_summary_json,
        )
        captured = await WhatsAppMobileRegistrationService(
            session=self.session,
            secret_vault=self.secret_vault,
        ).capture(envelope)
        if not captured:
            await InboundSmsIngressService(service).process(envelope)
        return empty_twilio_response()

    async def handle_status(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        params: dict[str, str],
        signature: str | None,
        signature_params: TwilioSignatureParams | None = None,
        outbound_call_session_id: str | None = None,
    ) -> str:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_twilio_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        status_url = status_callback_url_for_resource(resource)
        if status_url is None:
            raise IntegrationWebhookError(TWILIO_STATUS_CALLBACK_MISSING_ERROR)
        expected_status_url = (
            outbound_call_callback_url(status_url, outbound_call_session_id)
            if outbound_call_session_id is not None
            else status_url
        )
        self._validate_signature(
            expected_url=expected_status_url,
            purpose=IntegrationWebhookPurpose.STATUS,
            params=signature_params if signature_params is not None else params,
            signature=signature,
            auth_token=credentials.auth_token,
        )
        self._verify_account(params=params, account_sid=credentials.account_sid)
        normalized = await TwilioEventNormalizer().normalize_event(
            EventNormalizationRequest(
                provider=IntegrationProviderType.TWILIO,
                purpose=IntegrationWebhookPurpose.STATUS,
                params=params,
                provider_account_id=credentials.account_sid,
                resource_display_name=resource.display_name,
            ),
        )
        call_sid = params.get("CallSid")
        message_sid = params.get("MessageSid") or params.get("SmsSid")
        if call_sid is not None:
            if outbound_call_session_id is not None:
                try:
                    await service.bind_outbound_call_session(
                        user_id=route_context.route.user_id,
                        integration_id=route_context.route.integration_id,
                        resource_id=resource.id,
                        provider=IntegrationProviderType.TWILIO,
                        call_session_id=outbound_call_session_id,
                        external_call_id=call_sid,
                        payload_json=dict(params),
                    )
                except IntegrationResourceError as exc:
                    raise IntegrationWebhookError(str(exc)) from exc
            await self._update_call_status(
                user_id=route_context.route.user_id,
                call_sid=call_sid,
                status=params.get("CallStatus"),
            )
        if message_sid is not None:
            await self._update_message_status(
                user_id=route_context.route.user_id,
                message_sid=message_sid,
                status=params.get("MessageStatus") or params.get("SmsStatus"),
            )
        await service.record_communication_event(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=normalized.provider,
            event_type=normalized.event_type,
            direction=normalized.direction,
            external_event_id=normalized.external_event_id,
            idempotency_key=normalized.idempotency_key,
            payload_json=normalized.payload_json,
            safe_summary_json=normalized.safe_summary_json,
        )
        await self.session.commit()
        return empty_twilio_response()

    async def reject_after_route(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        reason: str,
    ) -> None:
        service = self._service()
        await service.record_audit(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            action=IntegrationAuditAction.WEBHOOK_REJECTED,
            actor_type=IntegrationAuditActorType.WEBHOOK,
            actor_id=None,
            outcome=IntegrationAuditOutcome.DENIED,
            safe_metadata={"reason": reason},
        )
        await self.session.commit()

    def _service(self) -> UserIntegrationService:
        return UserIntegrationService(
            session=self.session,
            secret_vault=self.secret_vault,
            public_base_url=self.public_base_url,
            provider_registry=self.provider_registry,
            session_factory=self.session_factory,
            authorization_service=self.authorization_service,
            voice_worker_demand_requester=self.voice_worker_demand_requester,
            voice_worker_readiness_waiter=self.voice_worker_readiness_waiter,
            voice_worker_demand_canceller=self.voice_worker_demand_canceller,
            voice_worker_demand_completer=self.voice_worker_demand_completer,
        )

    async def _resource(
        self,
        route_context: IntegrationWebhookRouteContext,
    ) -> IntegrationResourceRecord:
        service = self._service()
        active_integration = await service.integration_is_connected(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        if not active_integration:
            raise IntegrationWebhookError(WEBHOOK_RESOURCE_NOT_FOUND_ERROR)
        resource_id = route_context.route.resource_id
        if resource_id is None:
            raise IntegrationWebhookError(WEBHOOK_ROUTE_RESOURCE_MISSING_ERROR)
        resource = await self.session.get(IntegrationResourceRecord, resource_id)
        if (
            resource is None
            or resource.user_id != route_context.route.user_id
            or resource.integration_id != route_context.route.integration_id
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise IntegrationWebhookError(WEBHOOK_RESOURCE_NOT_FOUND_ERROR)
        return resource

    async def _update_call_status(
        self,
        *,
        user_id: str,
        call_sid: str,
        status: str | None,
    ) -> None:
        statement = select(IntegrationCallSessionRecord).where(
            col(IntegrationCallSessionRecord.user_id) == user_id,
            col(IntegrationCallSessionRecord.provider) == IntegrationProviderType.TWILIO.value,
            col(IntegrationCallSessionRecord.external_call_id) == call_sid,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            return
        normalized = normalize_twilio_call_status(status)
        record.status = normalized.value
        if normalized in {
            IntegrationCallStatus.COMPLETED,
            IntegrationCallStatus.FAILED,
            IntegrationCallStatus.REJECTED,
        }:
            record.ended_at = record.ended_at or utc_now()
            service = self._service()
            if normalized == IntegrationCallStatus.COMPLETED:
                await service.complete_voice_worker(record)
            else:
                await service.cancel_voice_worker(
                    record,
                    failure_code=f"twilio_call_{normalized.value}",
                )
        if normalized == IntegrationCallStatus.IN_PROGRESS:
            record.answered_at = record.answered_at or utc_now()
        record.updated_at = utc_now()

    async def _update_message_status(
        self,
        *,
        user_id: str,
        message_sid: str,
        status: str | None,
    ) -> None:
        statement = select(IntegrationMessageRecord).where(
            col(IntegrationMessageRecord.user_id) == user_id,
            col(IntegrationMessageRecord.provider) == IntegrationProviderType.TWILIO.value,
            col(IntegrationMessageRecord.external_message_id) == message_sid,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            return
        normalized = normalize_twilio_message_status(status)
        record.status = normalized.value
        record.updated_at = utc_now()
        await update_reply_delivery_status(
            session=self.session,
            user_id=user_id,
            outbound_message_id=record.id,
            message_status=normalized,
        )

    def _validate_signature(
        self,
        *,
        expected_url: str,
        purpose: IntegrationWebhookPurpose,
        params: TwilioSignatureParams,
        signature: str | None,
        auth_token: SecretStr,
    ) -> None:
        result = TwilioWebhookVerifier().validate_webhook(
            WebhookVerificationRequest(
                provider=IntegrationProviderType.TWILIO,
                purpose=purpose,
                expected_url=expected_url,
                params=params,
                signature=signature,
                signing_secret=auth_token,
            ),
        )
        if not result.valid:
            raise IntegrationWebhookError(result.reason or TWILIO_SIGNATURE_INVALID_ERROR)

    def _verify_account(self, *, params: dict[str, str], account_sid: str) -> None:
        if params.get("AccountSid") != account_sid:
            raise IntegrationWebhookError(TWILIO_ACCOUNT_MISMATCH_ERROR)

    def _verify_resource_number(
        self,
        *,
        params: dict[str, str],
        resource: IntegrationResourceRecord,
    ) -> None:
        candidates = {
            value
            for value in (
                params.get("To"),
                params.get("Called"),
                params.get("From"),
                params.get("Caller"),
            )
            if isinstance(value, str)
        }
        if resource.display_name not in candidates:
            raise IntegrationWebhookError(TWILIO_NUMBER_MISMATCH_ERROR)


def sms_url_for_resource(resource: IntegrationResourceRecord) -> str:
    current = resource.retained_metadata_json.get("current_webhooks")
    if not isinstance(current, dict):
        raise IntegrationWebhookError(TWILIO_SMS_WEBHOOK_MISSING_ERROR)
    value = current.get("sms_url")
    if not isinstance(value, str) or not value.strip():
        raise IntegrationWebhookError(TWILIO_SMS_WEBHOOK_MISSING_ERROR)
    return value


def twilio_stream_response(stream_url: str) -> str:
    response = VoiceResponse()
    connect = response.connect()
    connect.stream(url=stream_url)
    return str(response)


def empty_twilio_response() -> str:
    return str(VoiceResponse())


def required_param(params: dict[str, str], key: str) -> str:
    value = params.get(key)
    if value is None or not value.strip():
        message = f"Twilio parameter is missing: {key}"
        raise IntegrationWebhookError(message)
    return value.strip()


def int_or_none(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def required_normalized_event_id(external_event_id: str | None) -> str:
    if external_event_id is None or not external_event_id.strip():
        raise IntegrationWebhookError(TWILIO_EVENT_ID_MISSING_ERROR)
    return external_event_id.strip()

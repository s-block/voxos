import base64
import binascii
import re
from dataclasses import dataclass
from typing import cast

import orjson
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.tool_policy import ToolAuthorizationService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.errors import (
    IntegrationResourceError,
    IntegrationUpstreamError,
    IntegrationWebhookError,
)
from voxos.integrations.inbound_sms import InboundSmsEnvelope, InboundSmsIngressService
from voxos.integrations.models import (
    CommunicationChannel,
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationCallStatus,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationResourceStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import TelnyxCredentialBundle
from voxos.integrations.providers.telnyx.provider import TelnyxProviderError
from voxos.integrations.providers.telnyx.webhooks import (
    TELNYX_SIGNATURE_INVALID_ERROR,
    TelnyxWebhookVerificationRequest,
    TelnyxWebhookVerifier,
)
from voxos.integrations.records import (
    IntegrationCallSessionRecord,
    IntegrationMessageRecord,
    IntegrationResourceRecord,
)
from voxos.integrations.reply_dispatch import update_reply_delivery_status
from voxos.integrations.services import (
    OUTBOUND_CALL_SESSION_QUERY_PARAMETER,
    IntegrationWebhookRouteContext,
    UserIntegrationService,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandCompleter,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
    media_stream_url_for_resource,
    status_callback_url_for_resource,
    voice_url_for_resource,
)
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)

TELNYX_EVENT_DATA_MISSING_ERROR = "Telnyx webhook event data is missing."
TELNYX_EVENT_PAYLOAD_MISSING_ERROR = "Telnyx webhook payload is missing."
TELNYX_EVENT_TYPE_MISSING_ERROR = "Telnyx webhook event type is missing."
TELNYX_EVENT_ID_MISSING_ERROR = "Telnyx webhook event ID is missing."
TELNYX_CALL_CONTROL_ID_MISSING_ERROR = "Telnyx call control ID is missing."
TELNYX_MESSAGE_ID_MISSING_ERROR = "Telnyx message ID is missing."
TELNYX_FROM_NUMBER_MISSING_ERROR = "Telnyx from number is missing."
TELNYX_TO_NUMBER_MISSING_ERROR = "Telnyx to number is missing."
TELNYX_NUMBER_MISMATCH_ERROR = "Telnyx webhook does not match the configured number."
TELNYX_CONNECTION_ID_MISSING_ERROR = "Telnyx Voice API application is not configured."
TELNYX_MESSAGING_PROFILE_MISSING_ERROR = "Telnyx messaging profile is not configured."
TELNYX_EVENT_PROCESSING_ERROR = "Telnyx voice event processing is still in progress."
TELNYX_CLIENT_STATE_INVALID_ERROR = "Telnyx call correlation state is invalid."
TELNYX_EVENT_PROCESSING_LEASE_SECONDS = 30.0
TELNYX_TRANSCRIPTION_EVENT_TYPE = "call.transcription"
WHATSAPP_REGISTRATION_CALL_PURPOSE = "whatsapp_mobile_registration"
CALL_PURPOSE_PAYLOAD_KEY = "voxos_call_purpose"
WHATSAPP_REGISTRATION_ID_PAYLOAD_KEY = "whatsapp_mobile_registration_id"
WEBHOOK_ROUTE_RESOURCE_MISSING_ERROR = "Webhook route is not bound to a resource."
WEBHOOK_RESOURCE_NOT_FOUND_ERROR = "Webhook resource was not found."
WHATSAPP_DTMF_PROMPT_PATTERN = re.compile(
    r"\bpress\s+(?:the\s+)?(?P<digit>[0-9]|zero|one|two|to|too|three|four|for|or|five|six|seven|eight|ate|nine)\b",
    re.IGNORECASE,
)
WHATSAPP_DTMF_DIGITS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "to": "2",
    "too": "2",
    "three": "3",
    "four": "4",
    "for": "4",
    "or": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
    "nine": "9",
}


@dataclass(frozen=True, slots=True)
class TelnyxWebhookProcessor:
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
        body: bytes,
        payload: dict[str, object],
        signature: str | None,
        timestamp: str | None,
    ) -> dict[str, object]:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_telnyx_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        self._validate_signature(
            body=body,
            signature=signature,
            timestamp=timestamp,
            public_key=credentials.webhook_public_key,
        )
        event = telnyx_event(payload)
        event_type = telnyx_event_type(event)
        event_payload = telnyx_event_payload(event)
        call_control_id = required_payload_string(
            event_payload,
            "call_control_id",
            TELNYX_CALL_CONTROL_ID_MISSING_ERROR,
        )
        event_id = telnyx_event_id(event)
        if event_type == TELNYX_TRANSCRIPTION_EVENT_TYPE:
            return await self._handle_call_transcription(
                route_context=route_context,
                resource=resource,
                connection_id=required_telnyx_metadata(
                    credentials.connection_id,
                    TELNYX_CONNECTION_ID_MISSING_ERROR,
                ),
                credentials=credentials,
                event_id=event_id,
                event_payload=event_payload,
                call_control_id=call_control_id,
            )

        self._verify_resource_number(resource=resource, payload=event_payload)
        direction = call_direction(payload=event_payload, resource=resource)
        idempotency_key = telnyx_idempotency_key(
            account_id=required_telnyx_metadata(
                credentials.connection_id,
                TELNYX_CONNECTION_ID_MISSING_ERROR,
            ),
            event_type=event_type,
            event_id=event_id,
        )
        is_inbound_call_start = event_type == "call.initiated" and direction == IntegrationDirection.INBOUND
        event_record, created = await service.claim_communication_event(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
            event_type=event_type,
            direction=direction,
            external_event_id=event_id,
            idempotency_key=idempotency_key,
            payload_json=safe_payload(event_payload),
            safe_summary_json={
                "event_type": event_type,
                "call_control_id": call_control_id,
                "from": first_phone_number(event_payload.get("from")),
                "to": first_phone_number(event_payload.get("to")),
                "direction": direction.value,
            },
            status=(IntegrationEventStatus.PROCESSING if is_inbound_call_start else IntegrationEventStatus.ACCEPTED),
            retry_failed=is_inbound_call_start,
            retry_processing_after_seconds=(TELNYX_EVENT_PROCESSING_LEASE_SECONDS if is_inbound_call_start else None),
        )
        if not created:
            if is_inbound_call_start and event_record.status == IntegrationEventStatus.PROCESSING.value:
                raise IntegrationUpstreamError(TELNYX_EVENT_PROCESSING_ERROR)
            return {"ok": True}
        voice_registration = (
            await WhatsAppMobileRegistrationService(
                session=self.session,
                secret_vault=self.secret_vault,
            ).active_voice_registration(
                user_id=route_context.route.user_id,
                integration_id=route_context.route.integration_id,
                resource_id=resource.id,
            )
            if is_inbound_call_start
            else None
        )
        outbound_call_session_id = outbound_call_session_id_from_payload(
            event_payload,
        )
        if outbound_call_session_id is None:
            call_session = None
            if not is_inbound_call_start:
                try:
                    call_session = await service.communication_call_session(
                        user_id=route_context.route.user_id,
                        integration_id=route_context.route.integration_id,
                        resource_id=resource.id,
                        provider=IntegrationProviderType.TELNYX,
                        external_call_id=call_control_id,
                    )
                except IntegrationResourceError as exc:
                    raise IntegrationWebhookError(str(exc)) from exc
            if call_session is None:
                call_payload = safe_payload(event_payload)
                if voice_registration is not None:
                    call_payload = {
                        **call_payload,
                        CALL_PURPOSE_PAYLOAD_KEY: WHATSAPP_REGISTRATION_CALL_PURPOSE,
                        WHATSAPP_REGISTRATION_ID_PAYLOAD_KEY: voice_registration.id,
                    }
                call_session = await service.start_telnyx_call_session(
                    user_id=route_context.route.user_id,
                    integration_id=route_context.route.integration_id,
                    resource_id=resource.id,
                    direction=direction,
                    call_control_id=call_control_id,
                    from_number=first_phone_number(event_payload.get("from")),
                    to_number=first_phone_number(event_payload.get("to")),
                    payload_json=call_payload,
                    request_voice_worker=voice_registration is None,
                    transcript_enabled_override=(False if voice_registration is not None else None),
                )
        else:
            if direction != IntegrationDirection.OUTBOUND:
                raise IntegrationWebhookError(TELNYX_CLIENT_STATE_INVALID_ERROR)
            try:
                call_session = await service.bind_outbound_call_session(
                    user_id=route_context.route.user_id,
                    integration_id=route_context.route.integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.TELNYX,
                    call_session_id=outbound_call_session_id,
                    external_call_id=call_control_id,
                    payload_json=safe_payload(event_payload),
                )
            except IntegrationResourceError as exc:
                raise IntegrationWebhookError(str(exc)) from exc
        if is_inbound_call_start:
            await self.session.commit()
            try:
                if voice_registration is not None:
                    await self.provider_registry.telnyx_provider_for().answer_verification_call(
                        credentials=credentials,
                        call_control_id=call_control_id,
                        webhook_url=voice_url_for_resource(resource),
                        command_id=f"answer-{event_id[:29]}",
                    )
                else:
                    await service.wait_for_voice_worker(call_session)
                    await self.provider_registry.telnyx_provider_for().answer_call(
                        credentials=credentials,
                        call_control_id=call_control_id,
                        stream_url=media_stream_url_for_resource(resource),
                        webhook_url=status_callback_url_for_resource(resource),
                        command_id=f"answer-{event_id[:29]}",
                    )
                service.mark_call_answered(call_session)
            except TelnyxProviderError as exc:
                event_record.status = IntegrationEventStatus.FAILED.value
                event_record.error_type = type(exc).__name__
                event_record.error_message = str(exc)
                event_record.processed_at = utc_now()
                await service.fail_call_route(
                    call_session,
                    failure_code="provider_call_answer_failed",
                )
                await self.session.commit()
                raise IntegrationUpstreamError(str(exc)) from exc
            except BaseException:
                event_record.status = IntegrationEventStatus.FAILED.value
                event_record.error_type = "VoiceWorkerAdmissionError"
                event_record.error_message = "LiveKit voice worker admission failed."
                event_record.processed_at = utc_now()
                await service.fail_call_route(
                    call_session,
                    failure_code="worker_admission_failed",
                )
                raise
        else:
            await self._update_call_status(
                user_id=route_context.route.user_id,
                call_control_id=call_control_id,
                status=event_type,
            )
        event_record.safe_summary_json = {
            **event_record.safe_summary_json,
            "call_session_id": call_session.id,
            "call_purpose": (WHATSAPP_REGISTRATION_CALL_PURPOSE if voice_registration is not None else "agent"),
        }
        event_record.status = IntegrationEventStatus.PROCESSED.value
        event_record.processed_at = utc_now()
        await service.record_audit(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.EVENT_ACCEPTED,
            actor_type=IntegrationAuditActorType.WEBHOOK,
            actor_id=call_control_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"event_type": event_type, "call_session_id": call_session.id},
        )
        await self.session.commit()
        return {"ok": True}

    async def _handle_call_transcription(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        resource: IntegrationResourceRecord,
        connection_id: str,
        credentials: TelnyxCredentialBundle,
        event_id: str,
        event_payload: dict[str, object],
        call_control_id: str,
    ) -> dict[str, object]:
        service = self._service()
        try:
            call_session = await service.communication_call_session(
                user_id=route_context.route.user_id,
                integration_id=route_context.route.integration_id,
                resource_id=resource.id,
                provider=IntegrationProviderType.TELNYX,
                external_call_id=call_control_id,
            )
        except IntegrationResourceError as exc:
            raise IntegrationWebhookError(str(exc)) from exc
        if call_session is None:
            raise IntegrationWebhookError(TELNYX_CLIENT_STATE_INVALID_ERROR)

        transcription = transcription_data(event_payload)
        is_final = transcription.get("is_final") is True
        transcript = string_value(transcription, "transcript")
        direction = IntegrationDirection(call_session.direction)
        event_record, created = await service.claim_communication_event(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
            event_type=TELNYX_TRANSCRIPTION_EVENT_TYPE,
            direction=direction,
            external_event_id=event_id,
            idempotency_key=telnyx_idempotency_key(
                account_id=connection_id,
                event_type=TELNYX_TRANSCRIPTION_EVENT_TYPE,
                event_id=event_id,
            ),
            payload_json={
                "call_control_id": call_control_id,
                "is_final": is_final,
            },
            safe_summary_json={
                "event_type": TELNYX_TRANSCRIPTION_EVENT_TYPE,
                "call_session_id": call_session.id,
                "has_transcript": transcript is not None,
                "is_final": is_final,
            },
            status=IntegrationEventStatus.PROCESSING,
            retry_failed=True,
            retry_processing_after_seconds=TELNYX_EVENT_PROCESSING_LEASE_SECONDS,
        )
        if not created:
            return {"ok": True}

        registration_id = call_session.payload_json.get(
            WHATSAPP_REGISTRATION_ID_PAYLOAD_KEY,
        )
        captured = False
        dtmf_sent = False
        if (
            call_session.payload_json.get(CALL_PURPOSE_PAYLOAD_KEY) == WHATSAPP_REGISTRATION_CALL_PURPOSE
            and isinstance(registration_id, str)
            and transcript is not None
            and is_final
        ):
            prompt_digit = whatsapp_verification_prompt_digit(transcript)
            if prompt_digit is not None:
                try:
                    await self.provider_registry.telnyx_provider_for().send_call_dtmf(
                        credentials=credentials,
                        call_control_id=call_control_id,
                        digits=prompt_digit,
                        command_id=f"verify-dtmf-{call_session.id[:24]}",
                    )
                    dtmf_sent = True
                except TelnyxProviderError as exc:
                    event_record.status = IntegrationEventStatus.FAILED.value
                    event_record.error_type = type(exc).__name__
                    event_record.error_message = str(exc)
                    event_record.processed_at = utc_now()
                    await self.session.commit()
                    raise IntegrationUpstreamError(str(exc)) from exc
            captured = await WhatsAppMobileRegistrationService(
                session=self.session,
                secret_vault=self.secret_vault,
            ).capture_voice_transcript(
                user_id=route_context.route.user_id,
                registration_id=registration_id,
                integration_id=route_context.route.integration_id,
                resource_id=resource.id,
                call_control_id=call_control_id,
                event_id=event_id,
                transcript=transcript,
                from_number=call_session.from_number,
                received_at=utc_now(),
            )

        event_record.safe_summary_json = {
            **event_record.safe_summary_json,
            "verification_capture_handled": captured,
            "verification_dtmf_sent": dtmf_sent,
        }
        event_record.status = IntegrationEventStatus.PROCESSED.value
        event_record.processed_at = utc_now()
        await service.record_audit(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.EVENT_ACCEPTED,
            actor_type=IntegrationAuditActorType.WEBHOOK,
            actor_id=call_control_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "event_type": TELNYX_TRANSCRIPTION_EVENT_TYPE,
                "call_session_id": call_session.id,
                "verification_capture_handled": captured,
                "verification_dtmf_sent": dtmf_sent,
            },
        )
        await self.session.commit()
        return {"ok": True}

    async def handle_sms(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        body: bytes,
        payload: dict[str, object],
        signature: str | None,
        timestamp: str | None,
    ) -> dict[str, object]:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_telnyx_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        self._validate_signature(
            body=body,
            signature=signature,
            timestamp=timestamp,
            public_key=credentials.webhook_public_key,
        )
        event = telnyx_event(payload)
        event_type = telnyx_event_type(event)
        if event_type != "message.received":
            return await self.handle_status(
                route_context=route_context,
                body=body,
                payload=payload,
                signature=signature,
                timestamp=timestamp,
                already_verified=True,
            )
        event_payload = telnyx_event_payload(event)
        self._verify_resource_number(resource=resource, payload=event_payload)
        message_id = required_message_id(event=event, payload=event_payload)
        from_number = first_phone_number_required(
            event_payload.get("from"),
            TELNYX_FROM_NUMBER_MISSING_ERROR,
        )
        to_number = first_phone_number_required(
            event_payload.get("to"),
            TELNYX_TO_NUMBER_MISSING_ERROR,
        )
        body_text = string_value(event_payload, "text") or ""
        event_id = telnyx_event_id(event)
        envelope = InboundSmsEnvelope(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
            external_message_id=message_id,
            external_event_id=event_id,
            event_type=event_type,
            idempotency_key=telnyx_idempotency_key(
                account_id=required_telnyx_metadata(
                    credentials.messaging_profile_id,
                    TELNYX_MESSAGING_PROFILE_MISSING_ERROR,
                ),
                event_type=event_type,
                event_id=message_id,
            ),
            from_number=from_number,
            to_number=to_number,
            body=body_text,
            received_at=utc_now(),
            payload_json=safe_payload(event_payload),
            event_payload_json=safe_payload(event_payload),
            safe_summary_json={
                "event_type": event_type,
                "message_id": message_id,
                "from": from_number,
                "to": to_number,
            },
        )
        captured = await WhatsAppMobileRegistrationService(
            session=self.session,
            secret_vault=self.secret_vault,
        ).capture(envelope)
        if captured:
            return {"ok": True, "captured_for_whatsapp_registration": True}
        run_id = await InboundSmsIngressService(service).process(envelope)
        return {"ok": True, "agent_run_id": run_id}

    async def handle_status(
        self,
        *,
        route_context: IntegrationWebhookRouteContext,
        body: bytes,
        payload: dict[str, object],
        signature: str | None,
        timestamp: str | None,
        already_verified: bool = False,
    ) -> dict[str, object]:
        service = self._service()
        resource = await self._resource(route_context)
        credentials = await service.load_telnyx_credentials(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
        )
        if not already_verified:
            self._validate_signature(
                body=body,
                signature=signature,
                timestamp=timestamp,
                public_key=credentials.webhook_public_key,
            )
        event = telnyx_event(payload)
        event_type = telnyx_event_type(event)
        event_payload = telnyx_event_payload(event)
        external_id = telnyx_event_id(event)
        call_control_id = string_value(event_payload, "call_control_id")
        message_id = string_value(event_payload, "id") or string_value(event, "id")
        if call_control_id is not None:
            outbound_call_session_id = outbound_call_session_id_from_payload(
                event_payload,
            )
            if outbound_call_session_id is not None:
                try:
                    await service.bind_outbound_call_session(
                        user_id=route_context.route.user_id,
                        integration_id=route_context.route.integration_id,
                        resource_id=resource.id,
                        provider=IntegrationProviderType.TELNYX,
                        call_session_id=outbound_call_session_id,
                        external_call_id=call_control_id,
                        payload_json=safe_payload(event_payload),
                    )
                except IntegrationResourceError as exc:
                    raise IntegrationWebhookError(str(exc)) from exc
            await self._update_call_status(
                user_id=route_context.route.user_id,
                call_control_id=call_control_id,
                status=event_type,
            )
            external_id = call_control_id
        if message_id is not None:
            await self._update_message_status(
                user_id=route_context.route.user_id,
                message_id=message_id,
                status=message_status(event_type=event_type, payload=event_payload),
                payload=event_payload,
            )
            external_id = message_id
        await service.record_communication_event(
            user_id=route_context.route.user_id,
            integration_id=route_context.route.integration_id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
            event_type=event_type,
            direction=IntegrationDirection.UNKNOWN,
            external_event_id=external_id,
            idempotency_key=telnyx_idempotency_key(
                account_id=required_telnyx_metadata(
                    credentials.connection_id,
                    TELNYX_CONNECTION_ID_MISSING_ERROR,
                ),
                event_type=event_type,
                event_id=telnyx_event_id(event),
            ),
            payload_json=safe_payload(event_payload),
            safe_summary_json={
                "event_type": event_type,
                "call_control_id": call_control_id,
                "message_id": message_id,
            },
        )
        await self.session.commit()
        return {"ok": True}

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
            provider=IntegrationProviderType.TELNYX,
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
            or resource.provider != IntegrationProviderType.TELNYX.value
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise IntegrationWebhookError(WEBHOOK_RESOURCE_NOT_FOUND_ERROR)
        return resource

    async def _update_call_status(
        self,
        *,
        user_id: str,
        call_control_id: str,
        status: str,
    ) -> None:
        statement = select(IntegrationCallSessionRecord).where(
            col(IntegrationCallSessionRecord.user_id) == user_id,
            col(IntegrationCallSessionRecord.provider) == IntegrationProviderType.TELNYX.value,
            col(IntegrationCallSessionRecord.external_call_id) == call_control_id,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            return
        if status == "streaming.started":
            self._service().mark_call_media_ready(record)
            return
        normalized = normalize_telnyx_call_status(status)
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
                record.payload_json = {
                    **record.payload_json,
                    "failure_code": f"telnyx_call_{normalized.value}",
                }
                await service.cancel_voice_worker(
                    record,
                    failure_code=f"telnyx_call_{normalized.value}",
                )
        if status in {"call.answered", "call.bridged"}:
            record.answered_at = record.answered_at or utc_now()
        record.updated_at = utc_now()

    async def _update_message_status(
        self,
        *,
        user_id: str,
        message_id: str,
        status: str,
        payload: dict[str, object],
    ) -> None:
        statement = select(IntegrationMessageRecord).where(
            col(IntegrationMessageRecord.user_id) == user_id,
            col(IntegrationMessageRecord.provider) == IntegrationProviderType.TELNYX.value,
            col(IntegrationMessageRecord.external_message_id) == message_id,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            return
        normalized = normalize_telnyx_message_status(status)
        record.status = normalized.value
        record.payload_json = {
            **record.payload_json,
            "latest_status": safe_message_status_payload(payload),
        }
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
        body: bytes,
        signature: str | None,
        timestamp: str | None,
        public_key: str,
    ) -> None:
        result = TelnyxWebhookVerifier().validate_webhook(
            TelnyxWebhookVerificationRequest(
                body=body,
                signature=signature,
                timestamp=timestamp,
                public_key=public_key,
            ),
        )
        if not result.valid:
            raise IntegrationWebhookError(result.reason or TELNYX_SIGNATURE_INVALID_ERROR)

    def _verify_resource_number(
        self,
        *,
        resource: IntegrationResourceRecord,
        payload: dict[str, object],
    ) -> None:
        candidates = set(phone_numbers_from_value(payload.get("from"))) | set(
            phone_numbers_from_value(payload.get("to")),
        )
        if resource.display_name not in candidates:
            raise IntegrationWebhookError(TELNYX_NUMBER_MISMATCH_ERROR)


def telnyx_event(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise IntegrationWebhookError(TELNYX_EVENT_DATA_MISSING_ERROR)
    return data


def telnyx_event_payload(event: dict[str, object]) -> dict[str, object]:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        raise IntegrationWebhookError(TELNYX_EVENT_PAYLOAD_MISSING_ERROR)
    return payload


def telnyx_event_type(event: dict[str, object]) -> str:
    event_type = string_value(event, "event_type")
    if event_type is None:
        raise IntegrationWebhookError(TELNYX_EVENT_TYPE_MISSING_ERROR)
    return event_type


def transcription_data(payload: dict[str, object]) -> dict[str, object]:
    value = payload.get("transcription_data")
    return value if isinstance(value, dict) else {}


def whatsapp_verification_prompt_digit(transcript: str) -> str | None:
    match = WHATSAPP_DTMF_PROMPT_PATTERN.search(transcript)
    if match is None:
        return None
    digit = match.group("digit").lower()
    return digit if digit.isdigit() else WHATSAPP_DTMF_DIGITS[digit]


def telnyx_event_id(event: dict[str, object]) -> str:
    event_id = string_value(event, "id")
    if event_id is None:
        raise IntegrationWebhookError(TELNYX_EVENT_ID_MISSING_ERROR)
    return event_id


def required_message_id(
    *,
    event: dict[str, object],
    payload: dict[str, object],
) -> str:
    message_id = string_value(payload, "id") or string_value(event, "id")
    if message_id is None:
        raise IntegrationWebhookError(TELNYX_MESSAGE_ID_MISSING_ERROR)
    return message_id


def required_payload_string(
    payload: dict[str, object],
    key: str,
    error_message: str,
) -> str:
    value = string_value(payload, key)
    if value is None:
        raise IntegrationWebhookError(error_message)
    return value


def string_value(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def first_phone_number_required(value: object, error_message: str) -> str:
    phone_number = first_phone_number(value)
    if phone_number is None:
        raise IntegrationWebhookError(error_message)
    return phone_number


def first_phone_number(value: object) -> str | None:
    for phone_number in phone_numbers_from_value(value):
        return phone_number
    return None


def phone_numbers_from_value(value: object) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    if isinstance(value, dict):
        for key in ("phone_number", "number", "caller_id"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return (item.strip(),)
        return ()
    if isinstance(value, list):
        resolved: list[str] = []
        for item in value:
            resolved.extend(phone_numbers_from_value(item))
        return tuple(resolved)
    return ()


def call_direction(
    *,
    payload: dict[str, object],
    resource: IntegrationResourceRecord,
) -> IntegrationDirection:
    direction = string_value(payload, "direction")
    if direction in {"incoming", "inbound"}:
        return IntegrationDirection.INBOUND
    if direction in {"outgoing", "outbound"}:
        return IntegrationDirection.OUTBOUND
    from_number = first_phone_number(payload.get("from"))
    to_number = first_phone_number(payload.get("to"))
    if to_number == resource.display_name:
        return IntegrationDirection.INBOUND
    if from_number == resource.display_name:
        return IntegrationDirection.OUTBOUND
    return IntegrationDirection.UNKNOWN


def safe_payload(payload: dict[str, object]) -> dict[str, object]:
    return cast(dict[str, object], orjson.loads(orjson.dumps(payload)))


def telnyx_idempotency_key(
    *,
    account_id: str,
    event_type: str,
    event_id: str,
) -> str:
    return f"telnyx:{account_id}:{event_type}:{event_id}"


def outbound_call_session_id_from_payload(
    payload: dict[str, object],
) -> str | None:
    encoded_state = string_value(payload, "client_state")
    if encoded_state is None:
        return None
    try:
        decoded_state = base64.b64decode(
            encoded_state.encode("ascii"),
            validate=True,
        )
        state = orjson.loads(decoded_state)
    except (UnicodeEncodeError, binascii.Error, orjson.JSONDecodeError) as exc:
        raise IntegrationWebhookError(TELNYX_CLIENT_STATE_INVALID_ERROR) from exc
    if not isinstance(state, dict):
        raise IntegrationWebhookError(TELNYX_CLIENT_STATE_INVALID_ERROR)
    call_session_id = state.get(OUTBOUND_CALL_SESSION_QUERY_PARAMETER)
    if not isinstance(call_session_id, str) or not call_session_id.strip() or len(call_session_id) > 36:
        raise IntegrationWebhookError(TELNYX_CLIENT_STATE_INVALID_ERROR)
    return call_session_id.strip()


def required_telnyx_metadata(value: str | None, error_message: str) -> str:
    if value is None or not value.strip():
        raise IntegrationWebhookError(error_message)
    return value.strip()


def normalize_telnyx_call_status(status: str) -> IntegrationCallStatus:
    if status in {"call.answered", "call.bridged"}:
        return IntegrationCallStatus.IN_PROGRESS
    if status == "streaming.started":
        return IntegrationCallStatus.INITIATING
    if status == "call.hangup":
        return IntegrationCallStatus.COMPLETED
    if status in {"call.initiated", "call.dialing"}:
        return IntegrationCallStatus.INITIATING
    if status in {"call.ringing"}:
        return IntegrationCallStatus.RINGING
    if "failed" in status or status in {"call.machine.detection.failed"}:
        return IntegrationCallStatus.FAILED
    return IntegrationCallStatus.IN_PROGRESS


def normalize_telnyx_message_status(status: str) -> IntegrationMessageStatus:
    if status in {"received", "message.received"}:
        return IntegrationMessageStatus.RECEIVED
    if status in {"queued", "message.queued", "sending"}:
        return IntegrationMessageStatus.QUEUED
    if status in {"sent", "message.sent"}:
        return IntegrationMessageStatus.SENT
    if status == "delivered":
        return IntegrationMessageStatus.DELIVERED
    if status == "delivery_unconfirmed":
        return IntegrationMessageStatus.DELIVERY_UNCONFIRMED
    if "failed" in status or status in {"gw_timeout", "dlr_timeout"}:
        return IntegrationMessageStatus.FAILED
    return IntegrationMessageStatus.UNKNOWN


def message_status(*, event_type: str, payload: dict[str, object]) -> str:
    to_values = payload.get("to")
    if isinstance(to_values, list):
        for value in to_values:
            if isinstance(value, dict):
                status = string_value(value, "status")
                if status is not None:
                    return status
    status = string_value(payload, "status")
    return status or event_type


def safe_message_status_payload(payload: dict[str, object]) -> dict[str, object]:
    statuses: list[str] = []
    to_values = payload.get("to")
    if isinstance(to_values, list):
        for value in to_values:
            if isinstance(value, dict):
                status = string_value(value, "status")
                if status is not None:
                    statuses.append(status)
    safe_errors: list[dict[str, object]] = []
    errors = payload.get("errors")
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                continue
            code = error.get("code")
            title = error.get("title")
            safe_errors.append(
                {
                    "code": str(code)[:128] if isinstance(code, (str, int)) else None,
                    "title": (title.strip()[:256] if isinstance(title, str) and title.strip() else None),
                },
            )
    return {
        "statuses": statuses,
        "errors": safe_errors,
    }


def channel_for_event(event_type: str) -> CommunicationChannel:
    return CommunicationChannel.SMS if event_type.startswith("message.") else CommunicationChannel.VOICE

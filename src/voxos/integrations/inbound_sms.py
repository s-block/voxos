from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from voxos.db.models import utc_now
from voxos.integrations.models import (
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationMessageStatus,
    IntegrationProviderType,
)
from voxos.integrations.services import UserIntegrationService

TELNYX_SMS_EVENT_PROCESSING_LEASE_SECONDS = 30.0


class InboundSmsEnvelope(BaseModel):
    """Authenticated, normalized inbound SMS data safe to encrypt and replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1, max_length=256)
    integration_id: str = Field(min_length=1, max_length=36)
    resource_id: str = Field(min_length=1, max_length=36)
    provider: IntegrationProviderType
    external_message_id: str = Field(min_length=1, max_length=256)
    external_event_id: str | None = Field(default=None, max_length=256)
    event_type: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=512)
    from_number: str = Field(min_length=1, max_length=64)
    to_number: str = Field(min_length=1, max_length=64)
    body: str = Field(max_length=100_000)
    segments: int | None = Field(default=None, ge=0)
    received_at: datetime
    payload_json: dict[str, object] = Field(default_factory=dict)
    event_payload_json: dict[str, object] = Field(default_factory=dict)
    safe_summary_json: dict[str, object] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InboundSmsIngressService:
    """Persist a normalized SMS using the provider's existing reply policy."""

    integration_service: UserIntegrationService

    async def process(self, envelope: InboundSmsEnvelope) -> str | None:
        if envelope.provider == IntegrationProviderType.TELNYX:
            return await self._process_telnyx(envelope)
        await self._process_twilio(envelope)
        return None

    async def _process_twilio(self, envelope: InboundSmsEnvelope) -> None:
        service = self.integration_service
        await service.record_communication_message(
            user_id=envelope.user_id,
            integration_id=envelope.integration_id,
            resource_id=envelope.resource_id,
            provider=envelope.provider,
            direction=IntegrationDirection.INBOUND,
            external_message_id=envelope.external_message_id,
            from_number=envelope.from_number,
            to_number=envelope.to_number,
            body=envelope.body,
            status=IntegrationMessageStatus.RECEIVED,
            segments=envelope.segments,
            received_at=envelope.received_at,
            payload_json=envelope.event_payload_json,
        )
        await service.record_communication_event(
            user_id=envelope.user_id,
            integration_id=envelope.integration_id,
            resource_id=envelope.resource_id,
            provider=envelope.provider,
            event_type=envelope.event_type,
            direction=IntegrationDirection.INBOUND,
            external_event_id=envelope.external_event_id,
            idempotency_key=envelope.idempotency_key,
            payload_json=envelope.event_payload_json,
            safe_summary_json=envelope.safe_summary_json,
        )
        await service.record_audit(
            user_id=envelope.user_id,
            integration_id=envelope.integration_id,
            provider=envelope.provider,
            action=IntegrationAuditAction.EVENT_ACCEPTED,
            actor_type=IntegrationAuditActorType.WEBHOOK,
            actor_id=envelope.external_message_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"event_type": "sms_inbound"},
        )
        await service.session.commit()

    async def _process_telnyx(
        self,
        envelope: InboundSmsEnvelope,
    ) -> str | None:
        service = self.integration_service
        event_record, created = await service.claim_communication_event(
            user_id=envelope.user_id,
            integration_id=envelope.integration_id,
            resource_id=envelope.resource_id,
            provider=envelope.provider,
            event_type=envelope.event_type,
            direction=IntegrationDirection.INBOUND,
            external_event_id=envelope.external_event_id,
            idempotency_key=envelope.idempotency_key,
            payload_json=envelope.payload_json,
            safe_summary_json=envelope.safe_summary_json,
            status=IntegrationEventStatus.PROCESSING,
            retry_failed=True,
            retry_processing_after_seconds=TELNYX_SMS_EVENT_PROCESSING_LEASE_SECONDS,
        )
        if not created:
            run_id = event_record.safe_summary_json.get("agent_run_id")
            return run_id if isinstance(run_id, str) else None

        try:
            message_record = await service.record_communication_message(
                user_id=envelope.user_id,
                integration_id=envelope.integration_id,
                resource_id=envelope.resource_id,
                provider=envelope.provider,
                direction=IntegrationDirection.INBOUND,
                external_message_id=envelope.external_message_id,
                from_number=envelope.from_number,
                to_number=envelope.to_number,
                body=envelope.body,
                status=IntegrationMessageStatus.RECEIVED,
                segments=envelope.segments,
                received_at=envelope.received_at,
                payload_json=envelope.payload_json,
            )
            disposition = await service.enqueue_inbound_sms_agent_reply(
                user_id=envelope.user_id,
                integration_id=envelope.integration_id,
                resource_id=envelope.resource_id,
                provider=envelope.provider,
                inbound_message_id=message_record.id,
                external_message_id=envelope.external_message_id,
                from_number=envelope.from_number,
            )
            event_record.safe_summary_json = {
                **event_record.safe_summary_json,
                **disposition.safe_metadata(),
            }
            event_record.status = IntegrationEventStatus.PROCESSED.value
            event_record.processed_at = utc_now()
            await service.record_audit(
                user_id=envelope.user_id,
                integration_id=envelope.integration_id,
                provider=envelope.provider,
                action=IntegrationAuditAction.EVENT_ACCEPTED,
                actor_type=IntegrationAuditActorType.WEBHOOK,
                actor_id=envelope.external_message_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={
                    "event_type": envelope.event_type,
                    **disposition.safe_metadata(),
                },
            )
            await service.session.commit()
            return disposition.run_id
        except Exception as exc:
            await service.session.rollback()
            failed_event = await service.session.get(
                type(event_record),
                event_record.id,
            )
            if failed_event is not None:
                failed_event.status = IntegrationEventStatus.FAILED.value
                failed_event.error_type = type(exc).__name__[:128]
                failed_event.error_message = "Inbound SMS reply processing failed."
                failed_event.processed_at = utc_now()
                await service.session.commit()
            raise

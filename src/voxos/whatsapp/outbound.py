from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.integrations.models import (
    IntegrationCapability,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationStatus,
)
from voxos.integrations.records import IntegrationResourceRecord, UserIntegrationRecord
from voxos.integrations.whatsapp_jids import whatsapp_pn_jid_from_phone_number
from voxos.whatsapp.models import (
    WHATSAPP_MAX_TEXT_BYTES,
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import WhatsAppOutboxRecord, WhatsAppSessionRecord

WHATSAPP_BODY_REQUIRED_ERROR = "WhatsApp message body is required."
WHATSAPP_BODY_TOO_LARGE_ERROR = "WhatsApp message exceeds the supported text limit."
WHATSAPP_CONTACT_PHONE_INVALID_ERROR = "Contact phone number is invalid."


class WhatsAppOutboundMessageError(ValueError):
    pass


class WhatsAppOutboundUnavailableError(WhatsAppOutboundMessageError):
    def __init__(self) -> None:
        super().__init__("WhatsApp integration is not connected.")


class QueuedWhatsAppMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outbox_id: str = Field(min_length=1, max_length=36)
    status: WhatsAppOutboxStatus


@dataclass(frozen=True, slots=True)
class WhatsAppOutboundMessageService:
    session: AsyncSession

    async def enqueue_message(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        to_number: str,
        body: str,
    ) -> QueuedWhatsAppMessage:
        prepared_body = body.strip()
        if not prepared_body:
            raise WhatsAppOutboundMessageError(WHATSAPP_BODY_REQUIRED_ERROR)
        if len(prepared_body.encode("utf-8")) > WHATSAPP_MAX_TEXT_BYTES:
            raise WhatsAppOutboundMessageError(WHATSAPP_BODY_TOO_LARGE_ERROR)

        try:
            target_jid = whatsapp_pn_jid_from_phone_number(to_number)
        except ValueError as exc:
            raise WhatsAppOutboundMessageError(WHATSAPP_CONTACT_PHONE_INVALID_ERROR) from exc

        integration = (
            await self.session.exec(
                select(UserIntegrationRecord)
                .where(
                    col(UserIntegrationRecord.id) == integration_id,
                    col(UserIntegrationRecord.user_id) == user_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        resource = (
            await self.session.exec(
                select(IntegrationResourceRecord)
                .where(
                    col(IntegrationResourceRecord.id) == resource_id,
                    col(IntegrationResourceRecord.user_id) == user_id,
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        whatsapp_session = (
            await self.session.exec(
                select(WhatsAppSessionRecord)
                .where(
                    col(WhatsAppSessionRecord.user_id) == user_id,
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                    col(WhatsAppSessionRecord.resource_id) == resource_id,
                )
                .with_for_update(),
            )
        ).one_or_none()
        if (
            integration is None
            or integration.provider != IntegrationProviderType.WHATSAPP.value
            or not integration.enabled
            or integration.status != IntegrationStatus.CONNECTED.value
            or integration.deleted_at is not None
            or resource is None
            or resource.provider != IntegrationProviderType.WHATSAPP.value
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
            or IntegrationCapability.WHATSAPP_SEND.value not in resource.capabilities
            or whatsapp_session is None
            or whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value
            or whatsapp_session.state != WhatsAppSessionState.CONNECTED.value
            or whatsapp_session.validated_at is None
        ):
            raise WhatsAppOutboundUnavailableError

        outbox = WhatsAppOutboxRecord(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            target_jid=target_jid,
            body=prepared_body,
        )
        self.session.add(outbox)
        await self.session.commit()
        await self.session.refresh(outbox)
        return QueuedWhatsAppMessage(
            outbox_id=outbox.id,
            status=WhatsAppOutboxStatus(outbox.status),
        )

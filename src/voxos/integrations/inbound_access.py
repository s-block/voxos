from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.contacts.models import Contact
from voxos.contacts.services import UserContactService
from voxos.integrations.models import IntegrationReplyStatus
from voxos.integrations.records import IntegrationMessageRecord, IntegrationReplyRecord

UNKNOWN_CONTACT_SUPPRESSION_REASON = "unknown_contact"


class InboundCommunicationAccess(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contact_id: str | None = Field(default=None, min_length=1, max_length=36)
    contact_display_name: str | None = Field(default=None, min_length=1, max_length=256)
    contact_is_admin: bool = False
    shared_audience: bool = False

    @model_validator(mode="after")
    def validate_contact_snapshot(self) -> "InboundCommunicationAccess":
        if self.contact_id is None and (self.contact_display_name is not None or self.contact_is_admin):
            message = "Contact metadata requires a matched contact."
            raise ValueError(message)
        return self

    @property
    def reply_allowed(self) -> bool:
        return self.contact_id is not None

    @property
    def admin_tools_allowed(self) -> bool:
        return self.contact_is_admin and not self.shared_audience


class InboundCommunicationReplyDisposition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reply_id: str = Field(min_length=1, max_length=36)
    status: IntegrationReplyStatus
    run_id: str | None = Field(default=None, min_length=1, max_length=36)
    suppression_reason: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_disposition(self) -> "InboundCommunicationReplyDisposition":
        if self.status == IntegrationReplyStatus.SUPPRESSED:
            if self.run_id is not None or self.suppression_reason is None:
                message = "Suppressed replies require a reason and cannot have an agent run."
                raise ValueError(message)
        elif self.run_id is None:
            message = "Non-suppressed replies require an agent run."
            raise ValueError(message)
        return self

    def safe_metadata(self) -> dict[str, object]:
        metadata: dict[str, object] = {"reply_status": self.status.value}
        if self.run_id is not None:
            metadata["agent_run_id"] = self.run_id
        if self.suppression_reason is not None:
            metadata["suppression_reason"] = self.suppression_reason
        return metadata


@dataclass(frozen=True, slots=True)
class InboundCommunicationAccessResolver:
    session: AsyncSession

    async def resolve(
        self,
        *,
        user_id: str,
        inbound_message: IntegrationMessageRecord,
        remote_peer: str,
        shared_audience: bool,
    ) -> InboundCommunicationAccess:
        contact_service = UserContactService(self.session)
        contact = await self._contact_from_snapshot(
            contact_service=contact_service,
            user_id=user_id,
            inbound_message=inbound_message,
            remote_peer=remote_peer,
        )
        if contact is None:
            return InboundCommunicationAccess(shared_audience=shared_audience)
        return InboundCommunicationAccess(
            contact_id=contact.id,
            contact_display_name=contact.display_name,
            contact_is_admin=contact.is_admin,
            shared_audience=shared_audience,
        )

    async def _contact_from_snapshot(
        self,
        *,
        contact_service: UserContactService,
        user_id: str,
        inbound_message: IntegrationMessageRecord,
        remote_peer: str,
    ) -> Contact | None:
        if inbound_message.contact_id is None:
            return await contact_service.get_by_phone_number(
                user_id=user_id,
                phone_number=remote_peer,
            )
        contact = await contact_service.get_contact(
            user_id=user_id,
            contact_id=inbound_message.contact_id,
        )
        if contact is None:
            return None
        matched_by_phone = await contact_service.get_by_phone_number(
            user_id=user_id,
            phone_number=remote_peer,
        )
        if matched_by_phone is None or matched_by_phone.id != contact.id:
            return None
        return contact


def disposition_from_reply(
    reply: IntegrationReplyRecord,
) -> InboundCommunicationReplyDisposition:
    return InboundCommunicationReplyDisposition(
        reply_id=reply.id,
        status=IntegrationReplyStatus(reply.status),
        run_id=reply.agent_run_id,
        suppression_reason=reply.suppression_reason,
    )

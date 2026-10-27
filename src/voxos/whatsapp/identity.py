from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.contacts.models import Contact
from voxos.contacts.services import UserContactService
from voxos.db.models import utc_now
from voxos.integrations.whatsapp_jids import (
    normalize_whatsapp_lid_jid,
    normalize_whatsapp_pn_jid,
    phone_number_from_whatsapp_pn_jid,
)
from voxos.whatsapp.models import WhatsAppContactResolutionState
from voxos.whatsapp.records import WhatsAppContactIdentityRecord


class WhatsAppIdentityConflictError(ValueError):
    def __init__(self) -> None:
        super().__init__("WhatsApp identity aliases conflict.")


class WhatsAppIdentityObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pn_jid: str | None = Field(default=None, min_length=1, max_length=128)
    lid_jid: str | None = Field(default=None, min_length=1, max_length=128)
    username: str | None = Field(default=None, min_length=1, max_length=256)
    display_name: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("username", "display_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        return normalized or None


@dataclass(frozen=True, slots=True)
class WhatsAppContactIdentityResolution:
    identity_id: str
    state: WhatsAppContactResolutionState
    phone_number: str | None
    pn_jid: str | None
    lid_jid: str | None
    username: str | None
    contact_id: str | None
    contact_display_name: str | None


@dataclass(frozen=True, slots=True)
class WhatsAppContactIdentityService:
    session: AsyncSession

    async def observe(
        self,
        *,
        user_id: str,
        integration_id: str,
        observation: WhatsAppIdentityObservation,
    ) -> WhatsAppContactIdentityResolution:
        for attempt in range(2):
            try:
                return await self._observe(
                    user_id=user_id,
                    integration_id=integration_id,
                    observation=observation,
                )
            except IntegrityError:
                await self.session.rollback()
                if attempt == 1:
                    raise
        message = "identity observation retry was not exhausted"
        raise AssertionError(message)

    async def _observe(
        self,
        *,
        user_id: str,
        integration_id: str,
        observation: WhatsAppIdentityObservation,
    ) -> WhatsAppContactIdentityResolution:
        pn_jid = _validated_pn_jid(observation.pn_jid)
        lid_jid = _validated_lid_jid(observation.lid_jid)
        username = observation.username
        if pn_jid is None and lid_jid is None and username is None:
            message = "At least one valid WhatsApp identity is required."
            raise ValueError(message)
        phone_number = phone_number_from_whatsapp_pn_jid(pn_jid)
        rows = await self._matching_rows(
            user_id=user_id,
            integration_id=integration_id,
            pn_jid=pn_jid,
            lid_jid=lid_jid,
            username=username,
        )
        now = utc_now()
        if _aliases_conflict(
            rows=rows,
            pn_jid=pn_jid,
            lid_jid=lid_jid,
            username=username,
            phone_number=phone_number,
        ):
            return await self._mark_conflict(rows=rows, now=now)

        identity = await self._canonical_identity(
            rows=rows,
            user_id=user_id,
            integration_id=integration_id,
            now=now,
        )
        identity.pn_jid = pn_jid or identity.pn_jid
        identity.lid_jid = lid_jid or identity.lid_jid
        identity.username = username or identity.username
        identity.phone_number = phone_number or identity.phone_number
        if observation.display_name is not None:
            identity.display_name = observation.display_name
        identity.last_seen_at = now
        identity.updated_at = now

        contact = await self._matching_contact(
            user_id=user_id,
            identity=identity,
        )
        identity.contact_id = contact.id if contact is not None else None
        identity.resolution_state = (
            WhatsAppContactResolutionState.LINKED.value
            if contact is not None
            else WhatsAppContactResolutionState.UNLINKED.value
        )
        await self.session.flush()
        return _resolution(identity=identity, contact=contact)

    async def _matching_rows(
        self,
        *,
        user_id: str,
        integration_id: str,
        pn_jid: str | None,
        lid_jid: str | None,
        username: str | None,
    ) -> list[WhatsAppContactIdentityRecord]:
        predicates = []
        if pn_jid is not None:
            predicates.append(col(WhatsAppContactIdentityRecord.pn_jid) == pn_jid)
        if lid_jid is not None:
            predicates.append(col(WhatsAppContactIdentityRecord.lid_jid) == lid_jid)
        if username is not None:
            predicates.append(col(WhatsAppContactIdentityRecord.username) == username)
        statement = (
            select(WhatsAppContactIdentityRecord)
            .where(
                col(WhatsAppContactIdentityRecord.user_id) == user_id,
                col(WhatsAppContactIdentityRecord.integration_id) == integration_id,
                or_(*predicates),
            )
            .order_by(
                col(WhatsAppContactIdentityRecord.created_at).asc(),
                col(WhatsAppContactIdentityRecord.id).asc(),
            )
            .with_for_update()
        )
        return list((await self.session.exec(statement)).all())

    async def _canonical_identity(
        self,
        *,
        rows: list[WhatsAppContactIdentityRecord],
        user_id: str,
        integration_id: str,
        now: datetime,
    ) -> WhatsAppContactIdentityRecord:
        if not rows:
            identity = WhatsAppContactIdentityRecord(
                user_id=user_id,
                integration_id=integration_id,
                first_seen_at=now,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            self.session.add(identity)
            return identity
        identity = rows[0]
        for redundant in rows[1:]:
            identity.pn_jid = identity.pn_jid or redundant.pn_jid
            identity.lid_jid = identity.lid_jid or redundant.lid_jid
            identity.username = identity.username or redundant.username
            identity.phone_number = identity.phone_number or redundant.phone_number
            identity.contact_id = identity.contact_id or redundant.contact_id
            identity.display_name = identity.display_name or redundant.display_name
            identity.first_seen_at = min(identity.first_seen_at, redundant.first_seen_at)
            await self.session.delete(redundant)
        if len(rows) > 1:
            await self.session.flush()
        return identity

    async def _matching_contact(
        self,
        *,
        user_id: str,
        identity: WhatsAppContactIdentityRecord,
    ) -> Contact | None:
        phone_number = identity.phone_number
        if phone_number is None:
            return None
        current = (
            await UserContactService(self.session).get_contact(
                user_id=user_id,
                contact_id=identity.contact_id,
            )
            if identity.contact_id is not None
            else None
        )
        if current is not None and current.phone_number == phone_number:
            return current
        return await UserContactService(self.session).get_by_phone_number(
            user_id=user_id,
            phone_number=phone_number,
        )

    async def _mark_conflict(
        self,
        *,
        rows: list[WhatsAppContactIdentityRecord],
        now: datetime,
    ) -> WhatsAppContactIdentityResolution:
        if not rows:
            raise WhatsAppIdentityConflictError
        for identity in rows:
            identity.contact_id = None
            identity.resolution_state = WhatsAppContactResolutionState.CONFLICT.value
            identity.last_seen_at = now
            identity.updated_at = now
        await self.session.flush()
        return _resolution(identity=rows[0], contact=None)


def _validated_pn_jid(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_whatsapp_pn_jid(value)
    if normalized is None:
        message = "WhatsApp PN JID is invalid."
        raise ValueError(message)
    return normalized


def _validated_lid_jid(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = normalize_whatsapp_lid_jid(value)
    if normalized is None:
        message = "WhatsApp LID JID is invalid."
        raise ValueError(message)
    return normalized


def _aliases_conflict(
    *,
    rows: list[WhatsAppContactIdentityRecord],
    pn_jid: str | None,
    lid_jid: str | None,
    username: str | None,
    phone_number: str | None,
) -> bool:
    return any(
        len(values) > 1
        for values in (
            _non_null_values(*(row.pn_jid for row in rows), pn_jid),
            _non_null_values(*(row.lid_jid for row in rows), lid_jid),
            _non_null_values(*(row.username for row in rows), username),
            _non_null_values(*(row.phone_number for row in rows), phone_number),
            _non_null_values(*(row.contact_id for row in rows)),
        )
    )


def _non_null_values(*values: str | None) -> set[str]:
    return {value for value in values if value is not None}


def _resolution(
    *,
    identity: WhatsAppContactIdentityRecord,
    contact: Contact | None,
) -> WhatsAppContactIdentityResolution:
    return WhatsAppContactIdentityResolution(
        identity_id=identity.id,
        state=WhatsAppContactResolutionState(identity.resolution_state),
        phone_number=identity.phone_number,
        pn_jid=identity.pn_jid,
        lid_jid=identity.lid_jid,
        username=identity.username,
        contact_id=contact.id if contact is not None else None,
        contact_display_name=contact.display_name if contact is not None else None,
    )

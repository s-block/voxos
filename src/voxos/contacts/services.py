from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.contacts.models import Contact, ContactCreateRequest, ContactUpdateRequest
from voxos.contacts.records import UserContactRecord
from voxos.db.models import utc_now
from voxos.utils.phone_numbers import normalize_e164_phone_number

PHONE_NUMBER_CONSTRAINT = "uq_user_contacts_user_phone_number"


class ContactPhoneNumberConflictError(ValueError):
    def __init__(self) -> None:
        super().__init__("A contact with this phone number already exists.")


@dataclass(frozen=True, slots=True)
class UserContactService:
    session: AsyncSession

    async def create_contact(
        self,
        *,
        user_id: str,
        request: ContactCreateRequest,
    ) -> Contact:
        record = UserContactRecord(
            user_id=user_id,
            display_name=request.display_name,
            phone_number=request.phone_number,
            email=str(request.email) if request.email is not None else None,
            notes=request.notes,
            is_admin=request.is_admin,
        )
        self.session.add(record)
        await self._commit_with_phone_conflict_handling()
        await self.session.refresh(record)
        return contact_from_record(record)

    async def list_contacts(self, *, user_id: str) -> tuple[Contact, ...]:
        statement = (
            select(UserContactRecord)
            .where(col(UserContactRecord.user_id) == user_id)
            .order_by(
                col(UserContactRecord.display_name).asc(),
                col(UserContactRecord.created_at).asc(),
                col(UserContactRecord.id).asc(),
            )
        )
        result = await self.session.exec(statement)
        return tuple(contact_from_record(record) for record in result.all())

    async def search_contacts(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 20,
    ) -> tuple[Contact, ...]:
        normalized_query = query.strip()
        if not normalized_query:
            return ()
        if limit < 1 or limit > 100:
            message = "limit must be between 1 and 100"
            raise ValueError(message)
        search_pattern = escaped_like_pattern(normalized_query)
        statement = (
            select(UserContactRecord)
            .where(
                col(UserContactRecord.user_id) == user_id,
                or_(
                    col(UserContactRecord.display_name).ilike(
                        search_pattern,
                        escape="\\",
                    ),
                    col(UserContactRecord.phone_number).ilike(
                        search_pattern,
                        escape="\\",
                    ),
                    col(UserContactRecord.email).ilike(
                        search_pattern,
                        escape="\\",
                    ),
                ),
            )
            .order_by(
                col(UserContactRecord.display_name).asc(),
                col(UserContactRecord.created_at).asc(),
                col(UserContactRecord.id).asc(),
            )
            .limit(limit)
        )
        result = await self.session.exec(statement)
        return tuple(contact_from_record(record) for record in result.all())

    async def get_contact(
        self,
        *,
        user_id: str,
        contact_id: str,
    ) -> Contact | None:
        record = await self._contact_record(user_id=user_id, contact_id=contact_id)
        return contact_from_record(record) if record is not None else None

    async def get_by_phone_number(
        self,
        *,
        user_id: str,
        phone_number: str,
    ) -> Contact | None:
        try:
            normalized_phone_number = normalize_e164_phone_number(phone_number)
        except ValueError:
            return None
        statement = select(UserContactRecord).where(
            col(UserContactRecord.user_id) == user_id,
            col(UserContactRecord.phone_number) == normalized_phone_number,
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        return contact_from_record(record) if record is not None else None

    async def update_contact(
        self,
        *,
        user_id: str,
        contact_id: str,
        request: ContactUpdateRequest,
    ) -> Contact | None:
        record = await self._contact_record(user_id=user_id, contact_id=contact_id)
        if record is None:
            return None
        if "display_name" in request.model_fields_set and request.display_name is not None:
            record.display_name = request.display_name
        if "phone_number" in request.model_fields_set and request.phone_number is not None:
            record.phone_number = request.phone_number
        if "email" in request.model_fields_set:
            record.email = str(request.email) if request.email is not None else None
        if "notes" in request.model_fields_set:
            record.notes = request.notes
        if "is_admin" in request.model_fields_set and request.is_admin is not None:
            record.is_admin = request.is_admin
        record.updated_at = utc_now()
        await self._commit_with_phone_conflict_handling()
        await self.session.refresh(record)
        return contact_from_record(record)

    async def delete_contact(self, *, user_id: str, contact_id: str) -> bool:
        record = await self._contact_record(user_id=user_id, contact_id=contact_id)
        if record is None:
            return False
        await self.session.delete(record)
        await self.session.commit()
        return True

    async def _contact_record(
        self,
        *,
        user_id: str,
        contact_id: str,
    ) -> UserContactRecord | None:
        statement = select(UserContactRecord).where(
            col(UserContactRecord.id) == contact_id,
            col(UserContactRecord.user_id) == user_id,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def _commit_with_phone_conflict_handling(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if is_phone_number_conflict(exc):
                raise ContactPhoneNumberConflictError from exc
            raise


def contact_from_record(record: UserContactRecord) -> Contact:
    return Contact(
        id=record.id,
        user_id=record.user_id,
        display_name=record.display_name,
        phone_number=record.phone_number,
        email=record.email,
        notes=record.notes,
        is_admin=record.is_admin,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def escaped_like_pattern(value: str) -> str:
    escaped_value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped_value}%"


def is_phone_number_conflict(exc: IntegrityError) -> bool:
    original_exception = exc.orig
    constraint_name = getattr(original_exception, "constraint_name", None)
    if constraint_name == PHONE_NUMBER_CONSTRAINT:
        return True
    diagnostic = getattr(original_exception, "diag", None)
    if getattr(diagnostic, "constraint_name", None) == PHONE_NUMBER_CONSTRAINT:
        return True
    cause = getattr(original_exception, "__cause__", None)
    if getattr(cause, "constraint_name", None) == PHONE_NUMBER_CONSTRAINT:
        return True
    message = str(original_exception)
    return PHONE_NUMBER_CONSTRAINT in message or "user_contacts.user_id, user_contacts.phone_number" in message

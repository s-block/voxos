import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from voxos.contacts.models import ContactCreateRequest, ContactUpdateRequest
from voxos.contacts.services import (
    ContactPhoneNumberConflictError,
    UserContactService,
    is_phone_number_conflict,
)
from voxos.db.session import AsyncSessionFactory


@pytest.mark.asyncio
async def test_contact_service_crud_lookup_and_search(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserContactService(session)
        contact = await service.create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="  Alice Example  ",
                phone_number="  +447700900123  ",
                email="  Alice@Example.com  ",
                notes="  Prefers afternoon calls.  ",
            ),
        )

        assert contact.user_id == "user-1"
        assert contact.display_name == "Alice Example"
        assert contact.phone_number == "+447700900123"
        assert str(contact.email) == "Alice@example.com"
        assert contact.notes == "Prefers afternoon calls."
        assert contact.is_admin is False
        assert await service.get_contact(user_id="user-1", contact_id=contact.id) == contact
        assert (
            await service.get_by_phone_number(
                user_id="user-1",
                phone_number=" +447700900123 ",
            )
            == contact
        )
        assert (
            await service.get_by_phone_number(
                user_id="user-1",
                phone_number="07700900123",
            )
            is None
        )
        assert await service.search_contacts(
            user_id="user-1",
            query="alice",
        ) == (contact,)
        assert await service.search_contacts(
            user_id="user-1",
            query="900123",
        ) == (contact,)
        assert await service.search_contacts(user_id="user-1", query="%") == ()

        updated = await service.update_contact(
            user_id="user-1",
            contact_id=contact.id,
            request=ContactUpdateRequest(
                display_name="Alice Updated",
                email="",
                notes="",
                is_admin=True,
            ),
        )

        assert updated is not None
        assert updated.display_name == "Alice Updated"
        assert updated.email is None
        assert updated.notes is None
        assert updated.is_admin is True
        assert updated.updated_at >= contact.updated_at
        assert await service.list_contacts(user_id="user-1") == (updated,)
        assert await service.delete_contact(user_id="user-1", contact_id=contact.id)
        assert await service.list_contacts(user_id="user-1") == ()


@pytest.mark.asyncio
async def test_contact_phone_number_is_unique_per_user(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserContactService(session)
        request = ContactCreateRequest(
            display_name="First",
            phone_number="+12025550123",
        )
        await service.create_contact(user_id="user-1", request=request)

        with pytest.raises(ContactPhoneNumberConflictError):
            await service.create_contact(
                user_id="user-1",
                request=ContactCreateRequest(
                    display_name="Duplicate",
                    phone_number="+12025550123",
                ),
            )

        other_user_contact = await service.create_contact(
            user_id="user-2",
            request=request,
        )

    assert other_user_contact.user_id == "user-2"


@pytest.mark.asyncio
async def test_contact_service_enforces_explicit_user_ownership(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        service = UserContactService(session)
        contact = await service.create_contact(
            user_id="owner-user",
            request=ContactCreateRequest(
                display_name="Owner contact",
                phone_number="+12025550124",
            ),
        )

        assert await service.list_contacts(user_id="other-user") == ()
        assert (
            await service.get_contact(
                user_id="other-user",
                contact_id=contact.id,
            )
            is None
        )
        assert (
            await service.update_contact(
                user_id="other-user",
                contact_id=contact.id,
                request=ContactUpdateRequest(display_name="Forbidden"),
            )
            is None
        )
        assert not await service.delete_contact(
            user_id="other-user",
            contact_id=contact.id,
        )
        assert (
            await service.get_contact(
                user_id="owner-user",
                contact_id=contact.id,
            )
            == contact
        )


def test_contact_requests_validate_required_fields() -> None:
    with pytest.raises(ValidationError, match="E.164"):
        ContactCreateRequest(display_name="Alice", phone_number="07700900123")
    with pytest.raises(ValidationError, match="valid email address"):
        ContactCreateRequest(
            display_name="Alice",
            phone_number="+447700900123",
            email="not-an-email",
        )
    with pytest.raises(ValidationError, match="At least one"):
        ContactUpdateRequest()
    with pytest.raises(ValidationError, match="is_admin cannot be null"):
        ContactUpdateRequest(is_admin=None)


def test_unrelated_integrity_error_is_not_a_phone_conflict() -> None:
    error = IntegrityError(
        "INSERT INTO user_contacts ...",
        {},
        Exception("UNIQUE constraint failed: user_contacts.id"),
    )

    assert not is_phone_number_conflict(error)

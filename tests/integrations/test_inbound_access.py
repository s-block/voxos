import pytest

from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.inbound_access import InboundCommunicationAccessResolver
from voxos.integrations.models import (
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
)
from voxos.integrations.records import IntegrationMessageRecord


@pytest.mark.asyncio
async def test_resolver_matches_exact_contact_and_limits_group_admin(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    async with scoped_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Admin Contact",
                phone_number="+15551234567",
                is_admin=True,
            ),
        )
        inbound = inbound_message(contact_id=contact.id)
        session.add(inbound)
        await session.commit()
        resolver = InboundCommunicationAccessResolver(session)

        direct = await resolver.resolve(
            user_id="user-1",
            inbound_message=inbound,
            remote_peer=inbound.from_number,
            shared_audience=False,
        )
        group = await resolver.resolve(
            user_id="user-1",
            inbound_message=inbound,
            remote_peer=inbound.from_number,
            shared_audience=True,
        )

    assert direct.reply_allowed is True
    assert direct.contact_id == contact.id
    assert direct.contact_display_name == "Admin Contact"
    assert direct.admin_tools_allowed is True
    assert group.reply_allowed is True
    assert group.admin_tools_allowed is False


@pytest.mark.asyncio
async def test_resolver_fails_closed_for_mismatched_contact_snapshot(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    async with scoped_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Different Number",
                phone_number="+15550000000",
                is_admin=True,
            ),
        )
        inbound = inbound_message(contact_id=contact.id)
        session.add(inbound)
        await session.commit()

        access = await InboundCommunicationAccessResolver(session).resolve(
            user_id="user-1",
            inbound_message=inbound,
            remote_peer=inbound.from_number,
            shared_audience=False,
        )

    assert access.reply_allowed is False
    assert access.contact_id is None
    assert access.admin_tools_allowed is False


@pytest.mark.asyncio
async def test_resolver_matches_sms_without_a_contact_snapshot(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    async with scoped_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Saved Sender",
                phone_number="+15551234567",
            ),
        )
        inbound = inbound_message(contact_id=None)
        session.add(inbound)
        await session.commit()

        access = await InboundCommunicationAccessResolver(session).resolve(
            user_id="user-1",
            inbound_message=inbound,
            remote_peer=inbound.from_number,
            shared_audience=False,
        )

    assert access.reply_allowed is True
    assert access.contact_id == contact.id
    assert access.contact_display_name == "Saved Sender"
    assert access.admin_tools_allowed is False


def inbound_message(*, contact_id: str | None) -> IntegrationMessageRecord:
    return IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.TELNYX.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="message-1",
        from_number="+15551234567",
        to_number="+15557654321",
        contact_id=contact_id,
        body="hello",
        status=IntegrationMessageStatus.RECEIVED.value,
    )

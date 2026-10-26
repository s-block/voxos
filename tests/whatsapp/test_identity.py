import pytest
from sqlmodel import col, select

from voxos.contacts.models import ContactCreateRequest, ContactUpdateRequest
from voxos.contacts.services import UserContactService
from voxos.db.session import AsyncSessionFactory
from voxos.whatsapp.identity import (
    WhatsAppContactIdentityService,
    WhatsAppIdentityObservation,
)
from voxos.whatsapp.models import WhatsAppContactResolutionState
from voxos.whatsapp.records import WhatsAppContactIdentityRecord


@pytest.mark.asyncio
async def test_direct_identity_links_existing_contact_and_reuses_lid(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-one",
            request=ContactCreateRequest(
                display_name="Customer One",
                phone_number="+447700900123",
            ),
        )
        service = WhatsAppContactIdentityService(session)
        first = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900123@s.whatsapp.net",
                lid_jid="100000000001@lid",
                username="customer.one",
                display_name="WhatsApp Customer",
            ),
        )
        await session.commit()

        later_group_participant = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                lid_jid="100000000001@lid",
            ),
        )

    assert first.state == WhatsAppContactResolutionState.LINKED
    assert first.contact_id == contact.id
    assert first.phone_number == "+447700900123"
    assert later_group_participant.identity_id == first.identity_id
    assert later_group_participant.contact_id == contact.id


@pytest.mark.asyncio
async def test_lid_only_identity_is_enriched_by_later_direct_message(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-one",
            request=ContactCreateRequest(
                display_name="Customer One",
                phone_number="+447700900123",
            ),
        )
        service = WhatsAppContactIdentityService(session)
        lid_only = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                lid_jid="100000000001@lid",
            ),
        )
        await session.commit()
        enriched = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900123@s.whatsapp.net",
                lid_jid="100000000001@lid",
            ),
        )

    assert lid_only.state == WhatsAppContactResolutionState.UNLINKED
    assert enriched.identity_id == lid_only.identity_id
    assert enriched.contact_id == contact.id
    assert enriched.state == WhatsAppContactResolutionState.LINKED


@pytest.mark.asyncio
async def test_conflicting_aliases_are_unlinked_and_fail_closed(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        contacts = UserContactService(session)
        await contacts.create_contact(
            user_id="user-one",
            request=ContactCreateRequest(
                display_name="Customer One",
                phone_number="+447700900123",
            ),
        )
        await contacts.create_contact(
            user_id="user-one",
            request=ContactCreateRequest(
                display_name="Customer Two",
                phone_number="+447700900124",
            ),
        )
        service = WhatsAppContactIdentityService(session)
        await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900123@s.whatsapp.net",
                lid_jid="100000000001@lid",
            ),
        )
        await session.commit()
        await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900124@s.whatsapp.net",
                lid_jid="100000000002@lid",
            ),
        )
        await session.commit()

        conflict = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900123@s.whatsapp.net",
                lid_jid="100000000002@lid",
            ),
        )
        identities = (
            await session.exec(
                select(WhatsAppContactIdentityRecord).where(
                    col(WhatsAppContactIdentityRecord.user_id) == "user-one",
                ),
            )
        ).all()

    assert conflict.state == WhatsAppContactResolutionState.CONFLICT
    assert conflict.contact_id is None
    assert len(identities) == 2
    assert all(identity.contact_id is None for identity in identities)
    assert all(identity.resolution_state == WhatsAppContactResolutionState.CONFLICT.value for identity in identities)


@pytest.mark.asyncio
async def test_identity_scope_isolated_by_tenant_and_integration(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        service = WhatsAppContactIdentityService(session)
        observation = WhatsAppIdentityObservation(
            pn_jid="447700900123@s.whatsapp.net",
            lid_jid="100000000001@lid",
        )
        first = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=observation,
        )
        await session.commit()
        other_tenant = await service.observe(
            user_id="user-two",
            integration_id="integration-one",
            observation=observation,
        )
        await session.commit()
        other_integration = await service.observe(
            user_id="user-one",
            integration_id="integration-two",
            observation=observation,
        )

    assert len({first.identity_id, other_tenant.identity_id, other_integration.identity_id}) == 3


@pytest.mark.asyncio
async def test_contact_phone_change_invalidates_identity_link(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        contacts = UserContactService(session)
        contact = await contacts.create_contact(
            user_id="user-one",
            request=ContactCreateRequest(
                display_name="Customer One",
                phone_number="+447700900123",
            ),
        )
        service = WhatsAppContactIdentityService(session)
        linked = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                pn_jid="447700900123@s.whatsapp.net",
                lid_jid="100000000001@lid",
            ),
        )
        await session.commit()
        await contacts.update_contact(
            user_id="user-one",
            contact_id=contact.id,
            request=ContactUpdateRequest(phone_number="+447700900999"),
        )
        unlinked = await service.observe(
            user_id="user-one",
            integration_id="integration-one",
            observation=WhatsAppIdentityObservation(
                lid_jid="100000000001@lid",
            ),
        )

    assert linked.contact_id == contact.id
    assert unlinked.contact_id is None
    assert unlinked.state == WhatsAppContactResolutionState.UNLINKED


@pytest.mark.asyncio
async def test_invalid_jid_is_rejected(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        with pytest.raises(ValueError, match="PN JID is invalid"):
            await WhatsAppContactIdentityService(session).observe(
                user_id="user-one",
                integration_id="integration-one",
                observation=WhatsAppIdentityObservation(
                    pn_jid="not-a-phone@lid",
                ),
            )

import pytest
from sqlmodel import select

from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.records import IntegrationResourceRecord, UserIntegrationRecord
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.outbound import (
    WhatsAppOutboundMessageService,
    WhatsAppOutboundUnavailableError,
)
from voxos.whatsapp.records import WhatsAppOutboxRecord, WhatsAppSessionRecord

USER_ID = "user-123"
INTEGRATION_ID = "integration-123"
RESOURCE_ID = "resource-123"


async def seed_connected_whatsapp(session_factory: AsyncSessionFactory) -> None:
    now = utc_now()
    async with session_factory() as session:
        session.add(
            UserIntegrationRecord(
                id=INTEGRATION_ID,
                user_id=USER_ID,
                provider=IntegrationProviderType.WHATSAPP.value,
                label="WhatsApp",
                status=IntegrationStatus.CONNECTED.value,
                enabled=True,
                auth_type=IntegrationAuthType.LINKED_DEVICE.value,
                setup_state=IntegrationStatus.CONNECTED.value,
            ),
        )
        session.add(
            IntegrationResourceRecord(
                id=RESOURCE_ID,
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="whatsapp:source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.CONNECTED.value,
                capabilities=[IntegrationCapability.WHATSAPP_SEND.value],
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id=RESOURCE_ID,
                source_resource_id="source-resource-123",
                credential_id="credential-123",
                desired_state=WhatsAppDesiredState.CONNECTED.value,
                state=WhatsAppSessionState.CONNECTED.value,
                phone_number="+447700900456",
                validated_at=now,
                connected_at=now,
            ),
        )
        await session.commit()


@pytest.mark.asyncio
async def test_enqueue_direct_whatsapp_message_uses_e164_contact_number(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await seed_connected_whatsapp(sqlite_session_factory)

    async with sqlite_session_factory() as session:
        queued = await WhatsAppOutboundMessageService(session).enqueue_message(
            user_id=USER_ID,
            integration_id=INTEGRATION_ID,
            resource_id=RESOURCE_ID,
            to_number="+447700900123",
            body="  Hello Josh  ",
        )

    assert queued.status == WhatsAppOutboxStatus.QUEUED
    async with sqlite_session_factory() as session:
        outbox = (await session.exec(select(WhatsAppOutboxRecord))).one()
    assert outbox.id == queued.outbox_id
    assert outbox.reply_id is None
    assert outbox.target_jid == "447700900123@s.whatsapp.net"
    assert outbox.body == "Hello Josh"


@pytest.mark.asyncio
async def test_enqueue_direct_whatsapp_message_requires_connected_session(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    await seed_connected_whatsapp(sqlite_session_factory)
    async with sqlite_session_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        whatsapp_session.state = WhatsAppSessionState.FAILED.value
        await session.commit()

    async with sqlite_session_factory() as session:
        with pytest.raises(WhatsAppOutboundUnavailableError, match="not connected"):
            await WhatsAppOutboundMessageService(session).enqueue_message(
                user_id=USER_ID,
                integration_id=INTEGRATION_ID,
                resource_id=RESOURCE_ID,
                to_number="+447700900123",
                body="Hello Josh",
            )

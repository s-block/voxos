from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.whatsapp.models import WhatsAppSessionState
from voxos.whatsapp.records import (
    WhatsAppSessionKeyRecord,
    WhatsAppSessionLeaseRecord,
    WhatsAppSessionRecord,
)


def session_record(
    *,
    user_id: str = "user-123",
    integration_id: str = "integration-123",
    resource_id: str = "resource-123",
    source_resource_id: str = "source-resource-123",
) -> WhatsAppSessionRecord:
    return WhatsAppSessionRecord(
        user_id=user_id,
        integration_id=integration_id,
        resource_id=resource_id,
        source_resource_id=source_resource_id,
        credential_id="credential-123",
        phone_number="+447418606704",
    )


@pytest.mark.asyncio
async def test_session_and_encrypted_key_rows_persist(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    session_row = session_record()
    key_row = WhatsAppSessionKeyRecord(
        user_id=session_row.user_id,
        integration_id=session_row.integration_id,
        key_category="creds",
        logical_key_id="noise-key",
        ciphertext=b"ciphertext",
        nonce=b"012345678901",
    )
    lease = WhatsAppSessionLeaseRecord(
        integration_id=session_row.integration_id,
        user_id=session_row.user_id,
        owner_id="worker-123",
        lease_expires_at=utc_now() + timedelta(seconds=30),
    )

    async with sqlite_session_factory() as session:
        session.add(session_row)
        session.add(key_row)
        session.add(lease)
        await session.commit()

    async with sqlite_session_factory() as session:
        stored_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        stored_key = (await session.exec(select(WhatsAppSessionKeyRecord))).one()

    assert stored_session.state == WhatsAppSessionState.AWAITING_LINK.value
    assert stored_key.ciphertext == b"ciphertext"
    assert stored_key.logical_key_id == "noise-key"


@pytest.mark.asyncio
async def test_one_whatsapp_session_per_user_is_enforced(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    async with sqlite_session_factory() as session:
        session.add(session_record())
        session.add(
            session_record(
                integration_id="integration-456",
                resource_id="resource-456",
                source_resource_id="source-resource-456",
            ),
        )
        with pytest.raises(IntegrityError):
            await session.commit()

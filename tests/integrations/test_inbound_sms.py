import pytest
from sqlmodel import select

from voxos.agent_runtime.records import AgentRunRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.inbound_sms import InboundSmsEnvelope, InboundSmsIngressService
from voxos.integrations.models import (
    IntegrationEventStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.records import (
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
)
from voxos.integrations.services import UserIntegrationService


@pytest.mark.asyncio
async def test_unknown_telnyx_sender_is_durably_suppressed(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    async with scoped_factory() as session:
        ingress = InboundSmsIngressService(
            integration_service=UserIntegrationService(
                session=session,
                secret_vault=user_secret_vault,
                public_base_url="https://api.example.com",
                provider_registry=IntegrationProviderRegistry(),
                session_factory=scoped_factory,
                authorization_service=None,
            ),
        )
        envelope = InboundSmsEnvelope(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            external_message_id="message-1",
            external_event_id="event-1",
            event_type="message.received",
            idempotency_key="telnyx:message.received:event-1",
            from_number="+15551234567",
            to_number="+15557654321",
            body="Hello",
            received_at=utc_now(),
        )

        first_run_id = await ingress.process(envelope)
        repeated_run_id = await ingress.process(envelope)
        events = (await session.exec(select(IntegrationEventRecord))).all()
        messages = (await session.exec(select(IntegrationMessageRecord))).all()
        replies = (await session.exec(select(IntegrationReplyRecord))).all()
        runs = (await session.exec(select(AgentRunRecord))).all()

    assert first_run_id is None
    assert repeated_run_id is None
    assert len(events) == 1
    assert events[0].status == IntegrationEventStatus.PROCESSED.value
    assert events[0].safe_summary_json["reply_status"] == "suppressed"
    assert events[0].safe_summary_json["suppression_reason"] == "unknown_contact"
    assert "agent_run_id" not in events[0].safe_summary_json
    assert len(messages) == 1
    assert len(replies) == 1
    assert replies[0].status == IntegrationReplyStatus.SUPPRESSED.value
    assert replies[0].agent_run_id is None
    assert replies[0].suppression_reason == "unknown_contact"
    assert runs == []

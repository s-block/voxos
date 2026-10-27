from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import select

from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_core.time_context import ensure_aware_utc
from voxos.agent_runtime.models import AgentSessionStatus
from voxos.agent_runtime.records import AgentSessionRecord
from voxos.integrations.conversations import IntegrationConversationService
from voxos.integrations.models import IntegrationProviderType


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


@pytest.mark.asyncio
async def test_sms_conversation_is_reused_for_same_tenant_resource_and_peer(
    sqlite_session_factory,
) -> None:
    clock = MutableClock()
    async with sqlite_session_factory() as session:
        service = IntegrationConversationService(session=session, clock=clock)

        first = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )
        clock.current += timedelta(minutes=10)
        second = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )
        runtime_session = await session.get(
            AgentSessionRecord,
            second.runtime_session_id,
        )

    assert second.id == first.id
    assert second.runtime_session_id == first.runtime_session_id
    assert ensure_aware_utc(second.expires_at) == clock.current + timedelta(hours=24)
    assert runtime_session is not None
    assert runtime_session.title == "SMS from +447700900123"


@pytest.mark.asyncio
async def test_sms_conversation_isolated_by_peer_and_resource(
    sqlite_session_factory,
) -> None:
    async with sqlite_session_factory() as session:
        service = IntegrationConversationService(session=session)
        first = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )
        other_peer = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900124",
            agent_id="agent-1",
        )
        other_resource = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-2",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )

    assert len({first.runtime_session_id, other_peer.runtime_session_id, other_resource.runtime_session_id}) == 3


@pytest.mark.asyncio
async def test_expired_sms_conversation_rolls_runtime_session(
    sqlite_session_factory,
) -> None:
    clock = MutableClock()
    async with sqlite_session_factory() as session:
        service = IntegrationConversationService(session=session, clock=clock)
        first = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )
        first_session_id = first.runtime_session_id
        clock.current += timedelta(hours=25)

        rolled = await service.resolve_sms_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.TELNYX,
            remote_peer="+447700900123",
            agent_id="agent-1",
        )
        previous_session = await session.get(AgentSessionRecord, first_session_id)
        sessions = (await session.exec(select(AgentSessionRecord))).all()

    assert rolled.id == first.id
    assert rolled.runtime_session_id != first_session_id
    assert len(sessions) == 2
    assert previous_session is not None
    assert previous_session.status == AgentSessionStatus.ENDED.value
    assert previous_session.close_reason == "idle_rollover"


@pytest.mark.asyncio
async def test_whatsapp_conversation_uses_whatsapp_runtime_profile(
    sqlite_session_factory,
) -> None:
    async with sqlite_session_factory() as session:
        conversation = await IntegrationConversationService(
            session=session,
        ).resolve_conversation(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=IntegrationProviderType.WHATSAPP,
            medium=CommunicationMedium.WHATSAPP,
            remote_peer="+447700900002",
            agent_id="agent-1",
        )
        runtime_session = await session.get(
            AgentSessionRecord,
            conversation.runtime_session_id,
        )

    assert conversation.medium == CommunicationMedium.WHATSAPP.value
    assert runtime_session is not None
    assert runtime_session.channel == CommunicationMedium.WHATSAPP.value
    assert runtime_session.title == "WhatsApp conversation"
    assert runtime_session.room_metadata == {"response_profile": "whatsapp:v1"}
    assert runtime_session.prompt_snapshot.endswith("Response profile: whatsapp:v1.")

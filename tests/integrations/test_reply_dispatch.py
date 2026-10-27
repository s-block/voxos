from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import pytest
from sqlmodel import col, select

from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.records import AgentRunRecord, AgentSessionRecord
from voxos.agent_runtime.run_models import AgentRunKind
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrant,
    ToolGrantScope,
    ToolGrantStatus,
    ToolRisk,
)
from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.errors import IntegrationResourceError
from voxos.integrations.models import (
    IntegrationDirection,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.records import (
    IntegrationConversationRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.integrations.reply_dispatch import (
    SMS_OVERFLOW_FALLBACK,
    AmbiguousCommunicationReplyError,
    CommunicationReplyDispatcher,
    CommunicationReplyDispatchError,
    inbound_transcript_text,
    prepare_sms_body,
    update_reply_delivery_status,
)
from voxos.integrations.services import UserIntegrationService
from voxos.mcp.records import UserMcpServerRecord
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import WhatsAppOutboxRecord, WhatsAppSessionRecord


@dataclass(frozen=True, slots=True)
class GrantReader:
    grants: tuple[ToolGrant, ...]

    async def list_effective_grants(self, scope: AgentScope) -> tuple[ToolGrant, ...]:
        del scope
        return self.grants


@pytest.mark.asyncio
async def test_prepare_sms_body_keeps_short_reply_without_repair() -> None:
    repair_calls: list[str] = []

    async def repair(body: str) -> str:
        repair_calls.append(body)
        return "unused"

    result = await prepare_sms_body("A concise reply.", repairer=repair)

    assert result == "A concise reply."
    assert repair_calls == []


@pytest.mark.asyncio
async def test_prepare_sms_body_rejects_silent_empty_output() -> None:
    async def repair(body: str) -> str:
        return body

    with pytest.raises(CommunicationReplyDispatchError, match="empty SMS reply"):
        await prepare_sms_body("   ", repairer=repair)


@pytest.mark.asyncio
async def test_prepare_sms_body_uses_one_semantic_repair_pass() -> None:
    repair_calls: list[str] = []

    async def repair(body: str) -> str:
        repair_calls.append(body)
        return "B" * 200

    result = await prepare_sms_body("A" * 400, repairer=repair)

    assert result == "B" * 200
    assert repair_calls == ["A" * 400]


@pytest.mark.asyncio
async def test_prepare_sms_body_uses_complete_fallback_instead_of_truncating() -> None:
    async def ineffective_repair(body: str) -> str:
        return body

    result = await prepare_sms_body("A" * 2_000, repairer=ineffective_repair)

    assert result == SMS_OVERFLOW_FALLBACK


@pytest.mark.asyncio
async def test_dispatching_reply_becomes_ambiguous_and_blocks_automatic_resend(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.DISPATCHING,
    )
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )

    with pytest.raises(AmbiguousCommunicationReplyError, match="automatic resend is blocked"):
        await dispatcher.accepted_result(
            user_id="user-1",
            run_id=run_id,
            inbound_message_id=inbound_message_id,
            conversation_id=conversation_id,
        )
    with pytest.raises(AmbiguousCommunicationReplyError, match="automatic resend is blocked"):
        await dispatcher.accepted_result(
            user_id="user-1",
            run_id=run_id,
            inbound_message_id=inbound_message_id,
            conversation_id=conversation_id,
        )

    async with scoped_factory() as session:
        reply = (await session.exec(select(IntegrationReplyRecord))).one()

    assert reply.status == IntegrationReplyStatus.AMBIGUOUS.value
    assert reply.error_code == "dispatch_interrupted"


@pytest.mark.asyncio
async def test_delivery_callbacks_do_not_downgrade_delivered_reply(
    sqlite_session_factory,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    _, _, _ = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PROVIDER_ACCEPTED,
        with_outbound_message=True,
    )

    async with scoped_factory() as session:
        outbound = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.direction) == IntegrationDirection.OUTBOUND.value,
                ),
            )
        ).one()
        await update_reply_delivery_status(
            session=session,
            user_id="user-1",
            outbound_message_id=outbound.id,
            message_status=IntegrationMessageStatus.DELIVERED,
        )
        await session.commit()
        await update_reply_delivery_status(
            session=session,
            user_id="user-1",
            outbound_message_id=outbound.id,
            message_status=IntegrationMessageStatus.SENT,
        )
        await session.commit()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()

    assert reply.status == IntegrationReplyStatus.DELIVERED.value


@pytest.mark.asyncio
async def test_dispatch_whatsapp_queues_one_outbox_record_from_durable_inbound_target(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PENDING,
        medium=CommunicationMedium.WHATSAPP,
        provider=IntegrationProviderType.WHATSAPP,
        inbound_payload={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )

    first = await dispatcher.dispatch_whatsapp(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        generated_body="  A durable WhatsApp reply.  ",
    )
    repeated = await dispatcher.dispatch_whatsapp(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        generated_body="A durable WhatsApp reply.",
    )
    retried = await dispatcher.accepted_result(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        communication_medium=CommunicationMedium.WHATSAPP,
    )

    async with scoped_factory() as session:
        outboxes = (await session.exec(select(WhatsAppOutboxRecord))).all()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()

    assert repeated == first
    assert retried == first
    assert first.communication_medium == CommunicationMedium.WHATSAPP
    assert first.whatsapp_outbox_status == WhatsAppOutboxStatus.QUEUED
    assert first.outbound_message_id is None
    assert len(outboxes) == 1
    assert outboxes[0].id == first.whatsapp_outbox_id
    assert outboxes[0].target_jid == "447700900002@s.whatsapp.net"
    assert outboxes[0].body == "A durable WhatsApp reply."
    assert reply.status == IntegrationReplyStatus.GENERATED.value
    assert reply.response_profile == "whatsapp:v1"
    assert reply.generated_body == outboxes[0].body


@pytest.mark.asyncio
async def test_accepted_whatsapp_result_resumes_after_outbox_was_sent(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PENDING,
        medium=CommunicationMedium.WHATSAPP,
        provider=IntegrationProviderType.WHATSAPP,
        inbound_payload={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )
    queued = await dispatcher.dispatch_whatsapp(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        generated_body="A durable WhatsApp reply.",
    )
    async with scoped_factory() as session:
        outbox = (await session.exec(select(WhatsAppOutboxRecord))).one()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()
        outbox.status = WhatsAppOutboxStatus.SENT.value
        outbox.external_message_id = "provider-outbound-one"
        reply.status = IntegrationReplyStatus.PROVIDER_ACCEPTED.value
        await session.commit()

    resumed = await dispatcher.accepted_result(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        communication_medium=CommunicationMedium.WHATSAPP,
    )

    assert resumed is not None
    assert resumed.reply_id == queued.reply_id
    assert resumed.status == IntegrationReplyStatus.PROVIDER_ACCEPTED
    assert resumed.whatsapp_outbox_status == WhatsAppOutboxStatus.SENT


@pytest.mark.asyncio
async def test_dispatch_whatsapp_rejects_enqueue_after_disconnect_begins(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PENDING,
        medium=CommunicationMedium.WHATSAPP,
        provider=IntegrationProviderType.WHATSAPP,
        inbound_payload={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    async with scoped_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        whatsapp_session.desired_state = WhatsAppDesiredState.STOPPED.value
        whatsapp_session.state = WhatsAppSessionState.DISCONNECTING.value
        await session.commit()
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )

    with pytest.raises(
        CommunicationReplyDispatchError,
        match="integration is no longer connected",
    ):
        await dispatcher.dispatch_whatsapp(
            user_id="user-1",
            run_id=run_id,
            inbound_message_id=inbound_message_id,
            conversation_id=conversation_id,
            generated_body="This must not be queued.",
        )

    async with scoped_factory() as session:
        assert (await session.exec(select(WhatsAppOutboxRecord))).all() == []


@pytest.mark.asyncio
async def test_dispatch_whatsapp_queues_during_transient_reconnect(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PENDING,
        medium=CommunicationMedium.WHATSAPP,
        provider=IntegrationProviderType.WHATSAPP,
        inbound_payload={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    async with scoped_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        whatsapp_session.state = WhatsAppSessionState.RECONNECTING.value
        await session.commit()
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )

    result = await dispatcher.dispatch_whatsapp(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        generated_body="Queue this until the socket reconnects.",
    )

    assert result.whatsapp_outbox_status == WhatsAppOutboxStatus.QUEUED
    async with scoped_factory() as session:
        outbox = (await session.exec(select(WhatsAppOutboxRecord))).one()
    assert outbox.body == "Queue this until the socket reconnects."


@pytest.mark.asyncio
async def test_dispatch_whatsapp_queues_group_reply_to_durable_chat_target(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    run_id, inbound_message_id, conversation_id = await create_reply_context(
        scoped_factory=scoped_factory,
        status=IntegrationReplyStatus.PENDING,
        medium=CommunicationMedium.WHATSAPP,
        provider=IntegrationProviderType.WHATSAPP,
        inbound_payload={
            "chat_jid": "123456789@g.us",
            "chat_kind": "group",
            "conversation_peer": "123456789@g.us",
        },
        conversation_peer="123456789@g.us",
    )
    dispatcher = CommunicationReplyDispatcher(
        session_factory=scoped_factory,
        secret_vault=user_secret_vault,
        public_base_url="https://api.example.com",
    )

    result = await dispatcher.dispatch_whatsapp(
        user_id="user-1",
        run_id=run_id,
        inbound_message_id=inbound_message_id,
        conversation_id=conversation_id,
        generated_body="Reply to the group.",
    )

    async with scoped_factory() as session:
        outboxes = (await session.exec(select(WhatsAppOutboxRecord))).all()

    assert result.whatsapp_outbox_status == WhatsAppOutboxStatus.QUEUED
    assert len(outboxes) == 1
    assert outboxes[0].target_jid == "123456789@g.us"


@pytest.mark.asyncio
async def test_enqueue_whatsapp_reply_persists_medium_profile_and_run_payload(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.WHATSAPP.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="whatsapp-message-1",
        from_number="+447700900002",
        to_number="+447418606704",
        body="hello on WhatsApp",
        status=IntegrationMessageStatus.RECEIVED.value,
        payload_json={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    async with scoped_factory() as session:
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="WhatsApp Customer",
                phone_number=inbound.from_number,
            ),
        )
        session.add(inbound)
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=None,
        )

        disposition = await service.enqueue_inbound_communication_agent_reply(
            user_id="user-1",
            integration_id=inbound.integration_id,
            resource_id=inbound.resource_id,
            provider=IntegrationProviderType.WHATSAPP,
            medium=CommunicationMedium.WHATSAPP,
            inbound_message_id=inbound.id,
            external_message_id=inbound.external_message_id,
            remote_peer=inbound.from_number,
        )
        assert disposition.run_id is not None
        run = await session.get(AgentRunRecord, disposition.run_id)
        conversation = (await session.exec(select(IntegrationConversationRecord))).one()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()
        runtime_session = await session.get(
            AgentSessionRecord,
            conversation.runtime_session_id,
        )

    assert run is not None
    assert run.payload_json["communication_medium"] == "whatsapp"
    assert run.payload_json["title"] == "Inbound WhatsApp reply"
    assert run.idempotency_key == ("inbound-whatsapp:whatsapp:integration-1:resource-1:whatsapp-message-1")
    assert conversation.medium == "whatsapp"
    assert conversation.remote_peer == inbound.from_number
    assert reply.response_profile == "whatsapp:v1"
    assert reply.agent_run_id == run.id
    assert disposition.status == IntegrationReplyStatus.PENDING
    assert runtime_session is not None
    assert runtime_session.channel == "whatsapp"


@pytest.mark.asyncio
async def test_enqueue_group_reply_uses_group_conversation_and_sender_label(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.WHATSAPP.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="whatsapp-group-message-1",
        from_number="+447700900001",
        to_number="+447418606704",
        body="hello in the group",
        status=IntegrationMessageStatus.RECEIVED.value,
        payload_json={
            "chat_jid": "120363000000001@g.us",
            "chat_kind": "group",
            "conversation_peer": "120363000000001@g.us",
            "push_name": "Customer One",
        },
    )
    async with scoped_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Customer One",
                phone_number=inbound.from_number,
                is_admin=True,
            ),
        )
        inbound.contact_id = contact.id
        inbound.contact_display_name = contact.display_name
        session.add(inbound)
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=cast(
                ToolAuthorizationService,
                GrantReader(
                    (
                        grant(name="get_current_datetime", source=ToolGrantScope.INTERNAL),
                        grant(name="search_contacts", source=ToolGrantScope.INTERNAL),
                    ),
                ),
            ),
        )

        disposition = await service.enqueue_inbound_communication_agent_reply(
            user_id="user-1",
            integration_id=inbound.integration_id,
            resource_id=inbound.resource_id,
            provider=IntegrationProviderType.WHATSAPP,
            medium=CommunicationMedium.WHATSAPP,
            inbound_message_id=inbound.id,
            external_message_id=inbound.external_message_id,
            remote_peer=inbound.from_number,
            conversation_peer="120363000000001@g.us",
        )
        assert disposition.run_id is not None
        run = await session.get(AgentRunRecord, disposition.run_id)
        conversation = (await session.exec(select(IntegrationConversationRecord))).one()

    assert run is not None
    assert conversation.remote_peer == "120363000000001@g.us"
    assert run.payload_json["is_group"] is True
    assert run.payload_json["sender_display_name"] == "Customer One"
    assert {entry["tool_name"] for entry in run.effective_tool_policy_json["entries"]} == {
        "get_current_datetime",
    }
    assert "message in the group" in run.payload_json["task_prompt"]
    assert inbound_transcript_text(inbound) == "Customer One: hello in the group"


@pytest.mark.asyncio
async def test_unknown_whatsapp_group_sender_is_suppressed(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.WHATSAPP.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="unknown-group-message-1",
        from_number="+447700900099",
        to_number="+447418606704",
        body="hello in the group",
        status=IntegrationMessageStatus.RECEIVED.value,
        payload_json={
            "chat_jid": "120363000000099@g.us",
            "chat_kind": "group",
            "conversation_peer": "120363000000099@g.us",
        },
    )
    async with scoped_factory() as session:
        session.add(inbound)
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=None,
        )

        disposition = await service.enqueue_inbound_communication_agent_reply(
            user_id="user-1",
            integration_id=inbound.integration_id,
            resource_id=inbound.resource_id,
            provider=IntegrationProviderType.WHATSAPP,
            medium=CommunicationMedium.WHATSAPP,
            inbound_message_id=inbound.id,
            external_message_id=inbound.external_message_id,
            remote_peer=inbound.from_number,
            conversation_peer="120363000000099@g.us",
        )
        conversation = (await session.exec(select(IntegrationConversationRecord))).one()
        runs = (await session.exec(select(AgentRunRecord))).all()

    assert disposition.status == IntegrationReplyStatus.SUPPRESSED
    assert disposition.run_id is None
    assert conversation.remote_peer == "120363000000099@g.us"
    assert runs == []


@pytest.mark.asyncio
async def test_unknown_sms_sender_is_suppressed_and_never_reconsidered(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.TELNYX.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="unknown-sms-1",
        from_number="+15551234567",
        to_number="+15557654321",
        body="hello",
        status=IntegrationMessageStatus.RECEIVED.value,
    )
    async with scoped_factory() as session:
        session.add(inbound)
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=None,
        )

        first = await service.enqueue_inbound_sms_agent_reply(
            user_id="user-1",
            integration_id=inbound.integration_id,
            resource_id=inbound.resource_id,
            provider=IntegrationProviderType.TELNYX,
            inbound_message_id=inbound.id,
            external_message_id=inbound.external_message_id,
            from_number=inbound.from_number,
        )
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Added Later",
                phone_number=inbound.from_number,
            ),
        )
        repeated = await service.enqueue_inbound_sms_agent_reply(
            user_id="user-1",
            integration_id=inbound.integration_id,
            resource_id=inbound.resource_id,
            provider=IntegrationProviderType.TELNYX,
            inbound_message_id=inbound.id,
            external_message_id=inbound.external_message_id,
            from_number=inbound.from_number,
        )
        replies = (await session.exec(select(IntegrationReplyRecord))).all()
        runs = (await session.exec(select(AgentRunRecord))).all()

    assert first.status == IntegrationReplyStatus.SUPPRESSED
    assert first.run_id is None
    assert first.suppression_reason == "unknown_contact"
    assert repeated == first
    assert len(replies) == 1
    assert replies[0].agent_run_id is None
    assert replies[0].suppression_reason == "unknown_contact"
    assert runs == []


@pytest.mark.asyncio
async def test_enqueue_whatsapp_reply_rejects_inbound_record_from_another_user(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=IntegrationProviderType.WHATSAPP.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="whatsapp-message-1",
        from_number="+447700900002",
        to_number="+447418606704",
        body="hello on WhatsApp",
        status=IntegrationMessageStatus.RECEIVED.value,
        payload_json={"remote_jid": "447700900002@s.whatsapp.net"},
    )
    async with scoped_factory() as session:
        session.add(inbound)
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=None,
        )

        with pytest.raises(
            IntegrationResourceError,
            match="reply context is invalid",
        ):
            await service.enqueue_inbound_communication_agent_reply(
                user_id="user-2",
                integration_id=inbound.integration_id,
                resource_id=inbound.resource_id,
                provider=IntegrationProviderType.WHATSAPP,
                medium=CommunicationMedium.WHATSAPP,
                inbound_message_id=inbound.id,
                external_message_id=inbound.external_message_id,
                remote_peer=inbound.from_number,
            )


@pytest.mark.asyncio
async def test_inbound_sms_policy_adds_contact_search_only_for_admin_peers(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    scoped_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    grants = (
        grant(name="get_current_datetime", source=ToolGrantScope.INTERNAL),
        grant(name="read_voxos_doc_page", source=ToolGrantScope.INTERNAL),
        grant(name="search_contacts", source=ToolGrantScope.INTERNAL),
        grant(name="search_voxos_docs", source=ToolGrantScope.INTERNAL),
        grant(
            name="search_places",
            source=ToolGrantScope.MCP_SERVER,
            risk=ToolRisk.MEDIUM,
            server_id="server-1",
        ),
        grant(name="gmail.search_threads", source=ToolGrantScope.MCP_SERVER, server_id="server-2"),
        grant(name="list_agent_memories", source=ToolGrantScope.INTERNAL),
        grant(
            name="create_task_plan",
            source=ToolGrantScope.INTERNAL,
            risk=ToolRisk.MEDIUM,
        ),
        grant(
            name="send_sms",
            source=ToolGrantScope.INTEGRATION,
            risk=ToolRisk.HIGH,
            integration_id="integration-1",
        ),
        grant(
            name="private_search",
            source=ToolGrantScope.MCP_SERVER,
            risk=ToolRisk.MEDIUM,
            server_id="server-2",
        ),
    )
    async with scoped_factory() as session:
        session.add(
            UserMcpServerRecord(
                id="server-1",
                user_id="user-1",
                label="Public places search",
                transport="streamable_http",
                url="https://places.example.com/mcp",
                auth_type="none",
                enabled=True,
                non_admin_tools_enabled=True,
                trust_state="trusted",
                status="connected",
                allowed_tools=["search_places"],
            ),
        )
        await session.commit()
        service = UserIntegrationService(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url="https://api.example.com",
            provider_registry=IntegrationProviderRegistry(),
            session_factory=scoped_factory,
            authorization_service=cast(
                ToolAuthorizationService,
                GrantReader(grants),
            ),
        )
        public_policy = await service._inbound_communication_policy(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            peer_is_admin=False,
        )
        admin_policy = await service._inbound_communication_policy(
            user_id="user-1",
            agent_id="agent-1",
            session_id="session-1",
            peer_is_admin=True,
        )

    assert {entry.tool_name for entry in public_policy.entries} == {
        "get_current_datetime",
        "read_voxos_doc_page",
        "search_voxos_docs",
        "search_places",
    }
    assert {entry.tool_name for entry in admin_policy.entries} == {
        "get_current_datetime",
        "read_voxos_doc_page",
        "search_contacts",
        "search_voxos_docs",
        "search_places",
    }


async def create_reply_context(
    *,
    scoped_factory: AsyncSessionFactory,
    status: IntegrationReplyStatus,
    with_outbound_message: bool = False,
    medium: CommunicationMedium = CommunicationMedium.SMS,
    provider: IntegrationProviderType = IntegrationProviderType.TELNYX,
    inbound_payload: dict[str, object] | None = None,
    conversation_peer: str | None = None,
) -> tuple[str, str, str]:
    now = utc_now()
    run_store = SqlModelAgentRunStore(session_factory=scoped_factory)
    run = await run_store.enqueue_run(
        user_id="user-1",
        agent_id="agent-1",
        kind=AgentRunKind.COMMUNICATION_REPLY,
        payload_json={"task_prompt": "Reply"},
        created_by_user_id="user-1",
    )
    inbound = IntegrationMessageRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=provider.value,
        direction=IntegrationDirection.INBOUND.value,
        external_message_id="inbound-1",
        from_number=("+447700900002" if medium == CommunicationMedium.WHATSAPP else "+15551234567"),
        to_number="+447418606704",
        body="hello",
        status=IntegrationMessageStatus.RECEIVED.value,
        payload_json=inbound_payload or {},
    )
    conversation = IntegrationConversationRecord(
        user_id="user-1",
        integration_id="integration-1",
        resource_id="resource-1",
        provider=provider.value,
        medium=medium.value,
        remote_peer=conversation_peer or inbound.from_number,
        agent_id="agent-1",
        runtime_session_id="session-1",
        expires_at=now + timedelta(hours=24),
    )
    outbound = None
    if with_outbound_message:
        outbound = IntegrationMessageRecord(
            user_id="user-1",
            integration_id="integration-1",
            resource_id="resource-1",
            provider=provider.value,
            direction=IntegrationDirection.OUTBOUND.value,
            external_message_id="outbound-1",
            from_number=inbound.to_number,
            to_number=inbound.from_number,
            body="reply",
            status=IntegrationMessageStatus.SENT.value,
        )
    reply = IntegrationReplyRecord(
        user_id="user-1",
        inbound_message_id=inbound.id,
        conversation_id=conversation.id,
        agent_run_id=run.id,
        outbound_message_id=None if outbound is None else outbound.id,
        status=status.value,
        response_profile=f"{medium.value}:v1",
        generated_body=None if outbound is None else outbound.body,
        encoding=None if outbound is None else "gsm_7",
        estimated_segments=None if outbound is None else 1,
    )
    async with scoped_factory() as session:
        if medium == CommunicationMedium.WHATSAPP:
            session.add(
                UserIntegrationRecord(
                    id="integration-1",
                    user_id="user-1",
                    provider=IntegrationProviderType.WHATSAPP.value,
                    label="WhatsApp",
                    status=IntegrationStatus.CONNECTED.value,
                    enabled=True,
                    setup_state=WhatsAppSessionState.CONNECTED.value,
                ),
            )
            session.add(
                IntegrationResourceRecord(
                    id="resource-1",
                    user_id="user-1",
                    integration_id="integration-1",
                    provider=IntegrationProviderType.WHATSAPP.value,
                    resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                    external_id="source-resource-1",
                    display_name="+447418606704",
                    status=IntegrationResourceStatus.CONNECTED.value,
                ),
            )
            session.add(
                WhatsAppSessionRecord(
                    user_id="user-1",
                    integration_id="integration-1",
                    resource_id="resource-1",
                    source_resource_id="source-resource-1",
                    credential_id="credential-1",
                    desired_state=WhatsAppDesiredState.CONNECTED.value,
                    state=WhatsAppSessionState.CONNECTED.value,
                    phone_number="+447418606704",
                    validated_at=now,
                    connected_at=now,
                ),
            )
        session.add(inbound)
        session.add(conversation)
        if outbound is not None:
            session.add(outbound)
        session.add(reply)
        await session.commit()
    return run.id, inbound.id, conversation.id


def grant(
    *,
    name: str,
    source: ToolGrantScope,
    risk: ToolRisk = ToolRisk.LOW,
    server_id: str | None = None,
    integration_id: str | None = None,
) -> ToolGrant:
    return ToolGrant(
        id=f"grant-{name}",
        user_id="user-1",
        agent_id="agent-1",
        tool_name=name,
        tool_source=source,
        mcp_server_id=server_id,
        integration_id=integration_id,
        status=ToolGrantStatus.ALLOWED,
        risk=risk,
    )

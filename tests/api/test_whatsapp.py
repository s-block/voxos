from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import col, select
from starlette.requests import Request

from voxos.agent_runtime.records import AgentRunRecord
from voxos.auth.models import AuthenticatedUser
from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.inbound_sms import InboundSmsEnvelope
from voxos.integrations.models import (
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationCredentialStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.records import (
    IntegrationConversationRecord,
    IntegrationCredentialRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppLinkMethod,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import (
    WhatsAppContactIdentityRecord,
    WhatsAppMobileRegistrationRecord,
    WhatsAppOutboxRecord,
    WhatsAppSessionRecord,
)
from voxos.whatsapp.router import create_whatsapp_router
from voxos.whatsapp.runtime import WhatsAppInboundText, WhatsAppRuntimeService
from voxos.whatsapp.service import WhatsAppIntegrationService
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.store import (
    WhatsAppSessionDataStore,
    WhatsAppWorkerStore,
)

PHONE_NUMBER = "+447700906704"
USER_ID = "user-123"
MOBILE_REGISTRATION_MISSING = "expected WhatsApp mobile registration"


@dataclass(frozen=True, slots=True)
class StaticAuthDependency:
    user_id: str

    async def __call__(self, request: Request) -> AuthenticatedUser:
        return AuthenticatedUser(
            user_id=self.user_id,
            firebase_user_id=f"firebase-{self.user_id}",
            email=f"{self.user_id}@example.com",
            email_verified=True,
        )


def create_client(
    *,
    session_factory: AsyncSessionFactory,
    vault: UserSecretVaultService,
    user_id: str = USER_ID,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_whatsapp_router(
            auth_dependency=StaticAuthDependency(user_id),
            session_factory=session_factory,
            secret_vault=vault,
            settings=WhatsAppWorkerSettings(
                whatsapp_api_challenge_wait_seconds=0.1,
            ),
        ),
    )
    return TestClient(app)


async def seed_source_phone(
    *,
    session_factory: AsyncSessionFactory,
    user_id: str = USER_ID,
    phone_number: str = PHONE_NUMBER,
    provider: IntegrationProviderType = IntegrationProviderType.TELNYX,
) -> IntegrationResourceRecord:
    integration = UserIntegrationRecord(
        user_id=user_id,
        provider=provider.value,
        label=provider.value.title(),
        status=IntegrationStatus.CONNECTED.value,
        enabled=True,
        auth_type=IntegrationAuthType.API_KEY.value,
        setup_state="connected",
    )
    resource = IntegrationResourceRecord(
        user_id=user_id,
        integration_id=integration.id,
        provider=provider.value,
        resource_type=IntegrationResourceType.PHONE_NUMBER.value,
        external_id="telnyx-phone-123",
        display_name=phone_number,
        status=IntegrationResourceStatus.CONNECTED.value,
        capabilities=[
            IntegrationCapability.SMS_READ.value,
            IntegrationCapability.SMS_SEND.value,
        ],
        safe_metadata={"line_type": "mobile"},
    )
    async with session_factory() as session:
        session.add(integration)
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
    return resource


@pytest.mark.asyncio
async def test_link_returns_tenant_phone_and_never_returns_plaintext_session_key(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )

    options = client.get("/api/integrations/whatsapp/options")
    linked = client.post(
        "/api/integrations/whatsapp/link",
        json={
            "source_resource_id": source.id,
            "method": "qr",
        },
    )

    assert options.status_code == 200
    assert options.json() == {
        "options": [
            {
                "source_resource_id": source.id,
                "provider": "telnyx",
                "phone_number": PHONE_NUMBER,
                "line_type": "mobile",
            },
        ],
    }
    assert linked.status_code == 201
    body = linked.json()
    assert body["state"] == "awaiting_link"
    assert body["source_resource_id"] == source.id
    assert body["phone_number"] == PHONE_NUMBER

    async with sqlite_session_factory() as session:
        whatsapp_session = (await session.exec(select(WhatsAppSessionRecord))).one()
        vault_record = (await session.exec(select(UserSecretRecord))).one()
        whatsapp_integration = (
            await session.exec(
                select(UserIntegrationRecord).where(
                    col(UserIntegrationRecord.provider) == IntegrationProviderType.WHATSAPP.value,
                ),
            )
        ).one()

    assert whatsapp_session.source_resource_id == source.id
    assert whatsapp_integration.auth_type == IntegrationAuthType.LINKED_DEVICE.value
    assert vault_record.purpose == "whatsapp_session_data_key"
    assert b"linked_device_session" not in vault_record.ciphertext


@pytest.mark.asyncio
async def test_inbound_direct_message_links_contact_and_persists_all_aliases(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(session_factory=sqlite_session_factory)
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    linked_response = client.post(
        "/api/integrations/whatsapp/link",
        json={"source_resource_id": source.id, "method": "qr"},
    )
    assert linked_response.status_code == 201
    integration_id = linked_response.json()["integration_id"]
    async with sqlite_session_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id=USER_ID,
            request=ContactCreateRequest(
                display_name="Saved Customer",
                phone_number="+447700900123",
            ),
        )
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
        whatsapp_session.connected_at = utc_now()
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = next(
        demand
        for demand in await worker_store.list_session_demands(
            owner_id="inbound-test-worker",
            limit=10,
        )
        if demand.integration_id == integration_id
    )
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="inbound-test-worker",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    run_id = await WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).accept_inbound_text(
        fence=fence,
        inbound=WhatsAppInboundText(
            provider_message_id="whatsapp-message-one",
            chat_jid="447700900123@s.whatsapp.net",
            chat_kind="direct",
            sender_jid="447700900123@s.whatsapp.net",
            sender_jid_alt="100000000001@lid",
            sender_username="saved.customer",
            sender_pn_jid="447700900123@s.whatsapp.net",
            sender_lid_jid="100000000001@lid",
            addressing_mode="pn",
            identity_proof="pn_and_lid",
            push_name="WhatsApp Customer",
            body="Hello from WhatsApp",
            timestamp="1784640000",
        ),
    )

    async with sqlite_session_factory() as session:
        identity = (await session.exec(select(WhatsAppContactIdentityRecord))).one()
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == "whatsapp-message-one",
                ),
            )
        ).one()
        run = await session.get(AgentRunRecord, run_id)

    assert run_id is not None
    assert identity.contact_id == contact.id
    assert identity.phone_number == "+447700900123"
    assert identity.pn_jid == "447700900123@s.whatsapp.net"
    assert identity.lid_jid == "100000000001@lid"
    assert identity.username == "saved.customer"
    assert message.contact_id == contact.id
    assert message.contact_display_name == "Saved Customer"
    assert message.payload_json["sender_jid_alt"] == "100000000001@lid"
    assert message.payload_json["sender_username"] == "saved.customer"
    assert message.payload_json["contact_identity_id"] == identity.id
    assert run is not None
    assert run.payload_json["contact_id"] == contact.id
    assert run.payload_json["contact_display_name"] == "Saved Customer"
    assert 'sender is labelled "Saved Customer"' in str(run.payload_json["task_prompt"])


@pytest.mark.asyncio
async def test_unknown_lid_only_sender_is_remembered_without_guessing_a_contact(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(session_factory=sqlite_session_factory)
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    linked_response = client.post(
        "/api/integrations/whatsapp/link",
        json={"source_resource_id": source.id, "method": "qr"},
    )
    integration_id = linked_response.json()["integration_id"]
    async with sqlite_session_factory() as session:
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
        whatsapp_session.connected_at = utc_now()
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = next(
        demand
        for demand in await worker_store.list_session_demands(
            owner_id="lid-test-worker",
            limit=10,
        )
        if demand.integration_id == integration_id
    )
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="lid-test-worker",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    run_id = await WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).accept_inbound_text(
        fence=fence,
        inbound=WhatsAppInboundText(
            provider_message_id="lid-only-message",
            chat_jid="100000000009@lid",
            chat_kind="direct",
            sender_jid="100000000009@lid",
            sender_pn_jid=None,
            sender_lid_jid="100000000009@lid",
            identity_proof="lid_only",
            body="Hello from a private identity",
        ),
    )

    async with sqlite_session_factory() as session:
        identity = (await session.exec(select(WhatsAppContactIdentityRecord))).one()
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == "lid-only-message",
                ),
            )
        ).one_or_none()
        replies = (await session.exec(select(IntegrationReplyRecord))).all()
        runs = (await session.exec(select(AgentRunRecord))).all()

    assert run_id is None
    assert identity.lid_jid == "100000000009@lid"
    assert identity.phone_number is None
    assert identity.contact_id is None
    assert message is not None
    assert message.from_number == "100000000009@lid"
    assert message.contact_id is None
    assert message.payload_json["conversation_peer"] == "100000000009@lid"
    assert len(replies) == 1
    assert replies[0].status == IntegrationReplyStatus.SUPPRESSED.value
    assert replies[0].agent_run_id is None
    assert replies[0].suppression_reason == "unknown_contact"
    assert runs == []


@pytest.mark.asyncio
async def test_group_sender_is_resolved_and_queued_in_the_group_conversation(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(session_factory=sqlite_session_factory)
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    linked_response = client.post(
        "/api/integrations/whatsapp/link",
        json={"source_resource_id": source.id, "method": "qr"},
    )
    integration_id = linked_response.json()["integration_id"]
    async with sqlite_session_factory() as session:
        contact = await UserContactService(session).create_contact(
            user_id=USER_ID,
            request=ContactCreateRequest(
                display_name="Group Customer",
                phone_number="+447700900010",
                is_admin=True,
            ),
        )
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
        whatsapp_session.connected_at = utc_now()
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = next(
        demand
        for demand in await worker_store.list_session_demands(
            owner_id="group-test-worker",
            limit=10,
        )
        if demand.integration_id == integration_id
    )
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="group-test-worker",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    run_id = await WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).accept_inbound_text(
        fence=fence,
        inbound=WhatsAppInboundText(
            provider_message_id="group-message",
            chat_jid="120363000000001@g.us",
            chat_kind="group",
            sender_jid="100000000010@lid",
            sender_jid_alt="447700900010@s.whatsapp.net",
            sender_pn_jid="447700900010@s.whatsapp.net",
            sender_lid_jid="100000000010@lid",
            identity_proof="pn_and_lid",
            push_name="Group Customer",
            body="Hello from the group",
        ),
    )
    assert run_id is not None

    async with sqlite_session_factory() as session:
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == "group-message",
                ),
            )
        ).one()
        conversation = (await session.exec(select(IntegrationConversationRecord))).one()
        run = await session.get(AgentRunRecord, run_id)

    assert message.from_number == "+447700900010"
    assert message.contact_id == contact.id
    assert message.payload_json["chat_kind"] == "group"
    assert conversation.remote_peer == "120363000000001@g.us"
    assert run is not None
    assert run.payload_json["is_group"] is True
    assert run.payload_json["sender_display_name"] == "Group Customer"


@pytest.mark.asyncio
async def test_mobile_registration_arms_voice_capture(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )

    response = client.post(
        "/api/integrations/whatsapp/mobile-registration",
        json={"source_resource_id": source.id, "channel": "voice"},
    )

    assert response.status_code == 201
    assert response.json()["state"] == "armed_voice"


@pytest.mark.asyncio
async def test_mobile_registration_captures_code_in_vault_and_then_starts_link(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )

    armed = client.post(
        "/api/integrations/whatsapp/mobile-registration",
        json={"source_resource_id": source.id},
    )
    assert armed.status_code == 201
    assert armed.headers["cache-control"] == "no-store"
    assert armed.headers["pragma"] == "no-cache"
    registration_id = armed.json()["registration_id"]

    tenant_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        USER_ID,
    )
    async with tenant_factory() as session:
        registration = await session.get(
            WhatsAppMobileRegistrationRecord,
            registration_id,
        )
        if registration is None:
            raise AssertionError(MOBILE_REGISTRATION_MISSING)
        captured = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).capture(
            InboundSmsEnvelope(
                user_id=USER_ID,
                integration_id=registration.source_integration_id,
                resource_id=registration.source_resource_id,
                provider=IntegrationProviderType.TELNYX,
                external_message_id="verification-message-1",
                external_event_id="verification-event-1",
                event_type="message.received",
                idempotency_key="telnyx:test:message.received:verification-message-1",
                from_number="+447700900000",
                to_number=PHONE_NUMBER,
                body="Your WhatsApp Business code is 123-456",
                received_at=utc_now(),
                payload_json={"text": "Your WhatsApp Business code is 123-456"},
                event_payload_json={
                    "text": "Your WhatsApp Business code is 123-456",
                },
                safe_summary_json={"event_type": "message.received"},
            ),
        )
        secret_record = (
            await session.exec(
                select(UserSecretRecord).where(
                    col(UserSecretRecord.purpose) == "whatsapp_mobile_registration_inbox",
                ),
            )
        ).one()

    assert captured is True
    assert b"123-456" not in secret_record.ciphertext
    code_status = client.get(
        f"/api/integrations/whatsapp/mobile-registration/{registration_id}",
    )
    assert code_status.status_code == 200
    assert code_status.headers["cache-control"] == "no-store"
    assert code_status.json()["candidates"] == [
        {
            "candidate_id": "verification-message-1",
            "channel": "sms",
            "code": "123456",
            "sender": "+447700900000",
            "received_at": code_status.json()["candidates"][0]["received_at"],
        },
    ]
    assert code_status.json()["phone_number"] == PHONE_NUMBER

    other_client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
        user_id="other-user",
    )
    assert (
        other_client.get(
            f"/api/integrations/whatsapp/mobile-registration/{registration_id}",
        ).status_code
        == 404
    )

    completed = client.post(
        f"/api/integrations/whatsapp/mobile-registration/{registration_id}/complete",
        json={
            "candidate_id": "verification-message-1",
            "method": "qr",
        },
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "awaiting_link"

    async with tenant_factory() as session:
        assert (
            await session.get(
                WhatsAppMobileRegistrationRecord,
                registration_id,
            )
            is None
        )
        purposes = {record.purpose for record in (await session.exec(select(UserSecretRecord))).all()}
    assert purposes == {"whatsapp_session_data_key"}


@pytest.mark.asyncio
async def test_rejected_registration_code_replays_once_through_normal_sms_flow(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
        provider=IntegrationProviderType.TWILIO,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    armed = client.post(
        "/api/integrations/whatsapp/mobile-registration",
        json={"source_resource_id": source.id},
    )
    registration_id = armed.json()["registration_id"]
    tenant_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        USER_ID,
    )
    async with tenant_factory() as session:
        registration = await session.get(
            WhatsAppMobileRegistrationRecord,
            registration_id,
        )
        if registration is None:
            raise AssertionError(MOBILE_REGISTRATION_MISSING)
        await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).capture(
            InboundSmsEnvelope(
                user_id=USER_ID,
                integration_id=registration.source_integration_id,
                resource_id=registration.source_resource_id,
                provider=IntegrationProviderType.TWILIO,
                external_message_id="unrelated-code-message",
                external_event_id="unrelated-code-message",
                event_type="sms_inbound",
                idempotency_key="twilio:test:sms:unrelated-code-message",
                from_number="+447700900001",
                to_number=PHONE_NUMBER,
                body="Your unrelated service code is 654321",
                received_at=utc_now(),
                payload_json={"Body": "Your unrelated service code is 654321"},
                event_payload_json={
                    "Body": "Your unrelated service code is 654321",
                },
                safe_summary_json={"event_type": "sms_inbound"},
            ),
        )

    released = client.post(
        f"/api/integrations/whatsapp/mobile-registration/{registration_id}/reject",
        json={"candidate_id": "unrelated-code-message"},
    )
    duplicate = client.post(
        f"/api/integrations/whatsapp/mobile-registration/{registration_id}/reject",
        json={"candidate_id": "unrelated-code-message"},
    )

    assert released.status_code == 200
    assert released.json()["state"] == "armed"
    assert released.json()["candidates"] == []
    assert duplicate.status_code == 409
    async with tenant_factory() as session:
        messages = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.external_message_id) == "unrelated-code-message",
                ),
            )
        ).all()
    assert len(messages) == 1
    assert messages[0].body == "Your unrelated service code is 654321"


@pytest.mark.asyncio
async def test_status_and_challenge_are_tenant_scoped_and_not_cached(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    linked = client.post(
        "/api/integrations/whatsapp/link",
        json={
            "source_resource_id": source.id,
            "method": "qr",
        },
    )
    integration_id = linked.json()["integration_id"]
    async with sqlite_session_factory() as session:
        record = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                ),
            )
        ).one()
    tenant_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        USER_ID,
    )
    data_store = WhatsAppSessionDataStore(
        session_factory=tenant_factory,
        vault=user_secret_vault,
        user_id=USER_ID,
    )
    cipher = await data_store.resolve_cipher(
        credential_id=record.credential_id,
    )
    await data_store.save_challenge(
        integration_id=integration_id,
        cipher=cipher,
        method=WhatsAppLinkMethod.QR,
        requested_method=WhatsAppLinkMethod.QR,
        request_revision=record.challenge_request_revision,
        value="private-qr-value",
        expires_at=utc_now() + timedelta(minutes=1),
    )

    challenge = client.get(
        f"/api/integrations/whatsapp/{integration_id}/challenge",
    )
    other_client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
        user_id="other-user",
    )
    forbidden = other_client.get(
        f"/api/integrations/whatsapp/{integration_id}/status",
    )

    assert challenge.status_code == 200
    assert challenge.json()["value"] == "private-qr-value"
    assert challenge.headers["cache-control"] == "no-store"
    assert challenge.headers["pragma"] == "no-cache"
    assert forbidden.status_code == 404


@pytest.mark.asyncio
async def test_challenge_endpoint_reports_machine_readable_not_ready(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    client = create_client(
        session_factory=sqlite_session_factory,
        vault=user_secret_vault,
    )
    linked = client.post(
        "/api/integrations/whatsapp/link",
        json={
            "source_resource_id": source.id,
            "method": "pairing_code",
        },
    )

    response = client.get(
        f"/api/integrations/whatsapp/{linked.json()['integration_id']}/challenge",
    )

    assert response.status_code == 409
    assert "challenge_not_ready" in response.json()["detail"]


@pytest.mark.asyncio
async def test_disconnect_remains_pollable_until_worker_cleanup_finishes(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    source = await seed_source_phone(
        session_factory=sqlite_session_factory,
    )
    tenant_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        USER_ID,
    )
    async with tenant_factory() as session:
        service = WhatsAppIntegrationService(
            session=session,
            session_factory=tenant_factory,
            secret_vault=user_secret_vault,
            settings=WhatsAppWorkerSettings(),
        )
        linked = await service.start_link(
            user_id=USER_ID,
            source_resource_id=source.id,
            method=WhatsAppLinkMethod.QR,
        )
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == linked.integration_id,
                ),
            )
        ).one()
        whatsapp_resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == linked.integration_id,
                    col(IntegrationResourceRecord.provider) == IntegrationProviderType.WHATSAPP.value,
                ),
            )
        ).one()
        queued_reply = IntegrationReplyRecord(
            id="queued-reply",
            user_id=USER_ID,
            inbound_message_id="queued-inbound",
            conversation_id="queued-conversation",
            status=IntegrationReplyStatus.GENERATED.value,
            response_profile="whatsapp:v1",
            generated_body="Queued reply",
        )
        submitting_reply = IntegrationReplyRecord(
            id="submitting-reply",
            user_id=USER_ID,
            inbound_message_id="submitting-inbound",
            conversation_id="submitting-conversation",
            status=IntegrationReplyStatus.GENERATED.value,
            response_profile="whatsapp:v1",
            generated_body="Submitting reply",
        )
        session.add(queued_reply)
        session.add(submitting_reply)
        session.add(
            WhatsAppOutboxRecord(
                id="queued-outbox",
                user_id=USER_ID,
                integration_id=linked.integration_id,
                resource_id=whatsapp_resource.id,
                reply_id=queued_reply.id,
                target_jid="447700900001@s.whatsapp.net",
                body="Queued reply",
            ),
        )
        session.add(
            WhatsAppOutboxRecord(
                id="submitting-outbox",
                user_id=USER_ID,
                integration_id=linked.integration_id,
                resource_id=whatsapp_resource.id,
                reply_id=submitting_reply.id,
                target_jid="447700900002@s.whatsapp.net",
                body="Submitting reply",
                status=WhatsAppOutboxStatus.SUBMITTING.value,
                owner_id="worker-one",
                lease_generation=1,
            ),
        )
        await session.commit()
        assert await service.begin_disconnect_if_whatsapp(
            user_id=USER_ID,
            integration_id=linked.integration_id,
        )
        disconnecting = await service.get_status(
            user_id=USER_ID,
            integration_id=linked.integration_id,
        )

        integration = await session.get(
            UserIntegrationRecord,
            linked.integration_id,
        )
    assert disconnecting.state == WhatsAppSessionState.DISCONNECTING
    assert integration is not None
    assert integration.deleted_at is None
    assert whatsapp_resource.deleted_at is None
    assert whatsapp_session.desired_state == WhatsAppDesiredState.STOPPED.value

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = next(
        demand
        for demand in await worker_store.list_session_demands(
            owner_id="api-test-worker",
            limit=10,
        )
        if demand.integration_id == linked.integration_id
    )
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert fence is not None
    await WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).finalize_disconnect(
        fence=fence,
        credential_id=whatsapp_session.credential_id,
    )

    async with sqlite_session_factory() as session:
        integration = await session.get(
            UserIntegrationRecord,
            linked.integration_id,
        )
        deleted_whatsapp_resource = await session.get(
            IntegrationResourceRecord,
            whatsapp_session.resource_id,
        )
        credential = await session.get(
            IntegrationCredentialRecord,
            whatsapp_session.credential_id,
        )
        remaining_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == linked.integration_id,
                ),
            )
        ).one_or_none()
        remaining_secret = (await session.exec(select(UserSecretRecord))).one_or_none()
        outboxes = {outbox.id: outbox for outbox in (await session.exec(select(WhatsAppOutboxRecord))).all()}
        replies = {reply.id: reply for reply in (await session.exec(select(IntegrationReplyRecord))).all()}

    assert integration is not None
    assert integration.deleted_at is not None
    assert deleted_whatsapp_resource is not None
    assert deleted_whatsapp_resource.deleted_at is not None
    assert credential is not None
    assert credential.status == IntegrationCredentialStatus.REVOKED.value
    assert remaining_session is None
    assert remaining_secret is None
    assert outboxes["queued-outbox"].status == WhatsAppOutboxStatus.FAILED.value
    assert outboxes["queued-outbox"].failure_code == "integration_disconnected"
    assert replies["queued-reply"].status == IntegrationReplyStatus.FAILED.value
    assert outboxes["submitting-outbox"].status == WhatsAppOutboxStatus.AMBIGUOUS.value
    assert outboxes["submitting-outbox"].failure_code == ("integration_disconnected_during_submission")
    assert replies["submitting-reply"].status == IntegrationReplyStatus.AMBIGUOUS.value

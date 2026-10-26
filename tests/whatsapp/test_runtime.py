from typing import cast

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_runtime.records import AgentToolGrantRecord
from voxos.agent_runtime.tool_policy import ToolGrantScope, ToolGrantStatus
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.records import (
    IntegrationEventRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.integrations.tool_names import (
    WHATSAPP_SEND_MESSAGE_TOOL_NAME,
    provider_integration_tool_name,
)
from voxos.whatsapp.crypto import WhatsAppSessionCipher
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import WhatsAppSessionRecord
from voxos.whatsapp.runtime import (
    WhatsAppReportedIdentity,
    WhatsAppRuntimeService,
    _record_inbound_failure,
)
from voxos.whatsapp.store import WhatsAppWorkerStore


class BrokenFailureSession:
    async def rollback(self) -> None:
        message = "database connection lost"
        raise RuntimeError(message)


@pytest.mark.asyncio
async def test_inbound_failure_rolls_back_failed_transaction_before_recording_status(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    event = IntegrationEventRecord(
        user_id="user-one",
        integration_id="integration-one",
        provider=IntegrationProviderType.WHATSAPP.value,
        event_type="message.received",
        direction=IntegrationDirection.INBOUND.value,
        idempotency_key="whatsapp:message.received:one",
        status=IntegrationEventStatus.PROCESSING.value,
    )
    async with sqlite_session_factory() as session:
        session.add(event)
        await session.commit()
        session.expunge(event)
        session.add(
            IntegrationEventRecord(
                id=event.id,
                user_id="user-one",
                integration_id="integration-one",
                provider=IntegrationProviderType.WHATSAPP.value,
                event_type="message.received",
                direction=IntegrationDirection.INBOUND.value,
                idempotency_key="whatsapp:message.received:duplicate",
            ),
        )
        with pytest.raises(IntegrityError):
            await session.flush()

        failure = RuntimeError("reply enqueue failed")
        await _record_inbound_failure(
            session=session,
            event_id=event.id,
            error=failure,
        )
        failed_event = await session.get(IntegrationEventRecord, event.id)

    assert failed_event is not None
    assert failed_event.status == IntegrationEventStatus.FAILED.value
    assert failed_event.error_type == "RuntimeError"
    assert failed_event.error_message == "Inbound WhatsApp reply processing failed."
    assert failed_event.processed_at is not None


@pytest.mark.asyncio
async def test_failure_status_persistence_cannot_mask_original_error() -> None:
    original_error = ValueError("reply processing failed")

    with pytest.raises(ValueError) as captured:
        try:
            raise original_error
        except Exception as error:
            await _record_inbound_failure(
                session=cast(AsyncSession, BrokenFailureSession()),
                event_id="event-one",
                error=error,
            )
            raise

    assert captured.value is original_error


@pytest.mark.asyncio
async def test_connected_whatsapp_preapproves_message_tool_for_default_agent(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "user-123"
    integration_id = "whatsapp-integration-123"
    resource_id = "whatsapp-resource-123"
    now = utc_now()
    async with sqlite_session_factory() as session:
        session.add(
            UserIntegrationRecord(
                id="source-integration-123",
                user_id=user_id,
                provider=IntegrationProviderType.TELNYX.value,
                label="Telnyx",
                status=IntegrationStatus.CONNECTED.value,
                enabled=True,
            ),
        )
        session.add(
            IntegrationResourceRecord(
                id="source-resource-123",
                user_id=user_id,
                integration_id="source-integration-123",
                provider=IntegrationProviderType.TELNYX.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-number-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.CONNECTED.value,
            ),
        )
        session.add(
            UserIntegrationRecord(
                id=integration_id,
                user_id=user_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                label="WhatsApp",
                status=IntegrationStatus.NEEDS_AUTH.value,
                enabled=True,
                auth_type=IntegrationAuthType.LINKED_DEVICE.value,
                setup_state=WhatsAppSessionState.AWAITING_LINK.value,
            ),
        )
        session.add(
            IntegrationResourceRecord(
                id=resource_id,
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.DISABLED.value,
                capabilities=[IntegrationCapability.WHATSAPP_SEND.value],
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource_id,
                source_resource_id="source-resource-123",
                credential_id="credential-123",
                desired_state=WhatsAppDesiredState.CONNECTED.value,
                state=WhatsAppSessionState.CONNECTING.value,
                phone_number="+447700900456",
                created_at=now,
                updated_at=now,
            ),
        )
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=1,
        )
    )[0]
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert fence is not None

    validation = await WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    ).validate_and_mark_connected(
        fence=fence,
        cipher=WhatsAppSessionCipher(b"k" * 32),
        identity=WhatsAppReportedIdentity(
            pn_jid="447700900456@s.whatsapp.net",
            proof="pn",
        ),
    )

    assert validation.valid is True
    async with sqlite_session_factory() as session:
        grants = (
            await session.exec(
                select(AgentToolGrantRecord).where(
                    col(AgentToolGrantRecord.user_id) == user_id,
                    col(AgentToolGrantRecord.status) == ToolGrantStatus.ALLOWED.value,
                ),
            )
        ).all()
    assert {(grant.tool_source, grant.tool_name) for grant in grants} == {
        (
            ToolGrantScope.INTEGRATION.value,
            provider_integration_tool_name(
                integration_id=integration_id,
                resource_id=resource_id,
                action=WHATSAPP_SEND_MESSAGE_TOOL_NAME,
            ),
        ),
        (ToolGrantScope.INTERNAL.value, "search_contacts"),
    }


@pytest.mark.asyncio
async def test_confirmed_reset_moves_disconnecting_session_to_awaiting_link(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    user_id = "user-123"
    integration_id = "whatsapp-integration-123"
    resource_id = "whatsapp-resource-123"
    now = utc_now()
    async with sqlite_session_factory() as session:
        session.add(
            UserIntegrationRecord(
                id=integration_id,
                user_id=user_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                label="WhatsApp",
                status=IntegrationStatus.NEEDS_AUTH.value,
                enabled=True,
                auth_type=IntegrationAuthType.LINKED_DEVICE.value,
                setup_state=WhatsAppSessionState.DISCONNECTING.value,
            ),
        )
        session.add(
            IntegrationResourceRecord(
                id=resource_id,
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id="source-resource-123",
                display_name="+447700900456",
                status=IntegrationResourceStatus.DISABLED.value,
                capabilities=[IntegrationCapability.WHATSAPP_SEND.value],
            ),
        )
        session.add(
            WhatsAppSessionRecord(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource_id,
                source_resource_id="source-resource-123",
                credential_id="credential-123",
                desired_state=WhatsAppDesiredState.CONNECTED.value,
                state=WhatsAppSessionState.DISCONNECTING.value,
                phone_number="+447700900456",
                created_at=now,
                updated_at=now,
            ),
        )
        await session.commit()

    worker_store = WhatsAppWorkerStore(sqlite_session_factory)
    demand = (
        await worker_store.list_session_demands(
            owner_id="worker-one",
            limit=1,
        )
    )[0]
    fence = await worker_store.claim_session(
        demand=demand,
        owner_id="worker-one",
        lease_ttl_seconds=30,
    )
    assert fence is not None

    runtime = WhatsAppRuntimeService(
        worker_store=worker_store,
        base_session_factory=sqlite_session_factory,
        secret_vault=user_secret_vault,
    )
    await runtime.mark_awaiting_link(fence=fence, reset_validation=True)
    await runtime.mark_awaiting_link(
        fence=fence,
        reset_validation=True,
        allow_disconnecting=True,
    )

    async with sqlite_session_factory() as session:
        whatsapp_session = (
            await session.exec(
                select(WhatsAppSessionRecord).where(
                    col(WhatsAppSessionRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        integration = await session.get(UserIntegrationRecord, integration_id)

    assert whatsapp_session.state == WhatsAppSessionState.AWAITING_LINK.value
    assert whatsapp_session.validated_at is None
    assert whatsapp_session.connected_at is None
    assert integration is not None
    assert integration.setup_state == WhatsAppSessionState.AWAITING_LINK.value

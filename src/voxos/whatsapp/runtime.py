import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.response_profiles import CommunicationMedium
from voxos.agent_runtime.tool_authorization import (
    SqlModelToolAuthorizationService,
)
from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.models import (
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationCredentialStatus,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationStatus,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.telnyx.provider import TelnyxIntegrationProvider
from voxos.integrations.providers.twilio.provider import TwilioIntegrationProvider
from voxos.integrations.records import (
    IntegrationCredentialRecord,
    IntegrationEventRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.integrations.services import UserIntegrationService
from voxos.integrations.whatsapp_jids import (
    is_whatsapp_chat_jid,
    phone_number_from_whatsapp_pn_jid,
)
from voxos.utils.phone_numbers import normalize_e164_phone_number
from voxos.whatsapp.crypto import WhatsAppSessionCipher
from voxos.whatsapp.errors import (
    WhatsAppInboundRetryableError,
    WhatsAppInboundUnavailableError,
    WhatsAppIntegrationNotFoundError,
)
from voxos.whatsapp.identity import (
    WhatsAppContactIdentityService,
    WhatsAppIdentityObservation,
)
from voxos.whatsapp.models import (
    WhatsAppDesiredState,
    WhatsAppOutboxStatus,
    WhatsAppSessionState,
)
from voxos.whatsapp.records import (
    WhatsAppOutboxRecord,
    WhatsAppSessionKeyRecord,
    WhatsAppSessionRecord,
)
from voxos.whatsapp.service import status_message
from voxos.whatsapp.store import (
    WhatsAppLeaseFence,
    WhatsAppWorkerStore,
)

WHATSAPP_INBOUND_PROCESSING_LEASE_SECONDS = 60.0
logger = logging.getLogger(__name__)


class WhatsAppReportedIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pn_jid: str | None = Field(default=None, max_length=256)
    lid_jid: str | None = Field(default=None, max_length=256)
    proof: str = Field(min_length=1, max_length=32)


class WhatsAppInboundText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_message_id: str = Field(min_length=1, max_length=256)
    chat_jid: str = Field(min_length=1, max_length=128)
    chat_kind: Literal["direct", "group"]
    sender_jid: str = Field(min_length=1, max_length=128)
    sender_jid_alt: str | None = Field(default=None, max_length=128)
    sender_username: str | None = Field(default=None, max_length=256)
    sender_pn_jid: str | None = Field(default=None, max_length=128)
    sender_lid_jid: str | None = Field(default=None, max_length=128)
    addressing_mode: Literal["lid", "pn"] | None = None
    identity_proof: Literal["lid_only", "pn", "pn_and_lid", "unresolved"]
    push_name: str | None = Field(default=None, max_length=256)
    body: str = Field(min_length=1, max_length=16_384)
    timestamp: str | None = Field(default=None, max_length=64)


@dataclass(frozen=True, slots=True)
class WhatsAppIdentityValidation:
    valid: bool
    failure_code: str | None = None


class WhatsAppRuntimeService:
    def __init__(
        self,
        *,
        worker_store: WhatsAppWorkerStore,
        base_session_factory: AsyncSessionFactory,
        secret_vault: UserSecretVaultService,
    ) -> None:
        self._worker_store = worker_store
        self._base_session_factory = base_session_factory
        self._secret_vault = secret_vault

    async def source_phone_number(
        self,
        *,
        fence: WhatsAppLeaseFence,
    ) -> str:
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            source_resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.source_resource_id,
            )
            if (
                source_resource is None
                or source_resource.user_id != fence.user_id
                or source_resource.deleted_at is not None
            ):
                raise WhatsAppIntegrationNotFoundError
            return normalize_e164_phone_number(source_resource.display_name)

    async def validate_and_mark_connected(
        self,
        *,
        fence: WhatsAppLeaseFence,
        cipher: WhatsAppSessionCipher,
        identity: WhatsAppReportedIdentity,
    ) -> WhatsAppIdentityValidation:
        reported_number = phone_number_from_pn_jid(identity.pn_jid)
        if identity.proof not in {"pn", "pn_and_lid"} or reported_number is None:
            return WhatsAppIdentityValidation(
                valid=False,
                failure_code="pn_identity_unavailable",
            )
        resource_for_grant: IntegrationResourceRecord | None = None
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if (
                whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value
                or whatsapp_session.state == WhatsAppSessionState.DISCONNECTING.value
            ):
                return WhatsAppIdentityValidation(
                    valid=False,
                    failure_code="disconnect_requested",
                )
            source_resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.source_resource_id,
            )
            if (
                source_resource is None
                or source_resource.user_id != fence.user_id
                or source_resource.deleted_at is not None
            ):
                raise WhatsAppIntegrationNotFoundError
            try:
                expected_number = normalize_e164_phone_number(
                    source_resource.display_name,
                )
            except ValueError:
                return WhatsAppIdentityValidation(
                    valid=False,
                    failure_code="source_number_invalid",
                )
            if reported_number != expected_number:
                return WhatsAppIdentityValidation(
                    valid=False,
                    failure_code="number_mismatch",
                )
            now = utc_now()
            first_validation = whatsapp_session.validated_at is None
            whatsapp_session.state = WhatsAppSessionState.CONNECTED.value
            whatsapp_session.validation_fingerprint = cipher.identity_fingerprint(expected_number)
            whatsapp_session.validated_at = now
            whatsapp_session.connected_at = now
            whatsapp_session.last_seen_at = now
            whatsapp_session.failure_code = None
            whatsapp_session.challenge_ciphertext = None
            whatsapp_session.challenge_nonce = None
            whatsapp_session.challenge_expires_at = None
            whatsapp_session.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            integration.enabled = True
            integration.status = IntegrationStatus.CONNECTED.value
            integration.setup_state = WhatsAppSessionState.CONNECTED.value
            integration.status_message = status_message(
                WhatsAppSessionState.CONNECTED,
            )
            integration.last_checked_at = now
            integration.updated_at = now
            resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.resource_id,
            )
            if resource is None or resource.user_id != fence.user_id:
                raise WhatsAppIntegrationNotFoundError
            resource.status = IntegrationResourceStatus.CONNECTED.value
            resource.updated_at = now
            if first_validation:
                await _integration_service(
                    session=session,
                    tenant_factory=self._tenant_factory(fence.user_id),
                    secret_vault=self._secret_vault,
                ).record_audit(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    provider=IntegrationProviderType.WHATSAPP,
                    action=IntegrationAuditAction.WHATSAPP_LINKED,
                    actor_type=IntegrationAuditActorType.SYSTEM,
                    actor_id=None,
                    outcome=IntegrationAuditOutcome.SUCCEEDED,
                    safe_metadata={"resource_id": resource.id},
                )
            await session.commit()
            resource_for_grant = resource
        if resource_for_grant is not None:
            tenant_factory = self._tenant_factory(fence.user_id)
            async with tenant_factory() as tenant_session:
                await _integration_service(
                    session=tenant_session,
                    tenant_factory=tenant_factory,
                    secret_vault=self._secret_vault,
                ).preapprove_resource_tools(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    resource=resource_for_grant,
                )
        return WhatsAppIdentityValidation(valid=True)

    async def mark_state(
        self,
        *,
        fence: WhatsAppLeaseFence,
        state: WhatsAppSessionState,
        failure_code: str | None = None,
    ) -> None:
        if state == WhatsAppSessionState.CONNECTED:
            message = "connected state requires identity validation"
            raise ValueError(message)
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if whatsapp_session.state == WhatsAppSessionState.DISCONNECTING.value:
                return
            previous_state = whatsapp_session.state
            now = utc_now()
            whatsapp_session.state = state.value
            whatsapp_session.failure_code = failure_code
            whatsapp_session.last_seen_at = now
            whatsapp_session.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            if integration.deleted_at is None:
                integration.setup_state = state.value
                integration.status_message = status_message(
                    state,
                    failure_code,
                )
                integration.last_checked_at = now
                integration.updated_at = now
                if state == WhatsAppSessionState.NEEDS_RELINK:
                    integration.status = IntegrationStatus.NEEDS_AUTH.value
                elif state == WhatsAppSessionState.FAILED:
                    integration.status = IntegrationStatus.FAILED.value
            if state == WhatsAppSessionState.NEEDS_RELINK and previous_state != WhatsAppSessionState.NEEDS_RELINK.value:
                await _integration_service(
                    session=session,
                    tenant_factory=self._tenant_factory(fence.user_id),
                    secret_vault=self._secret_vault,
                ).record_audit(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    provider=IntegrationProviderType.WHATSAPP,
                    action=(IntegrationAuditAction.WHATSAPP_SESSION_NEEDS_RELINK),
                    actor_type=IntegrationAuditActorType.SYSTEM,
                    actor_id=None,
                    outcome=IntegrationAuditOutcome.SUCCEEDED,
                    safe_metadata={},
                )
            await session.commit()

    async def mark_awaiting_link(
        self,
        *,
        fence: WhatsAppLeaseFence,
        reset_validation: bool = False,
        allow_disconnecting: bool = False,
    ) -> None:
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if (
                whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value
                or (whatsapp_session.state == WhatsAppSessionState.DISCONNECTING.value and not allow_disconnecting)
                or whatsapp_session.state == WhatsAppSessionState.CONNECTED.value
            ):
                return
            now = utc_now()
            whatsapp_session.state = WhatsAppSessionState.AWAITING_LINK.value
            whatsapp_session.failure_code = None
            if reset_validation:
                whatsapp_session.validation_fingerprint = None
                whatsapp_session.validated_at = None
                whatsapp_session.connected_at = None
            whatsapp_session.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            integration.enabled = True
            integration.status = IntegrationStatus.NEEDS_AUTH.value
            integration.setup_state = WhatsAppSessionState.AWAITING_LINK.value
            integration.status_message = status_message(
                WhatsAppSessionState.AWAITING_LINK,
            )
            integration.updated_at = now
            if reset_validation:
                resource = await session.get(
                    IntegrationResourceRecord,
                    whatsapp_session.resource_id,
                )
                if resource is None or resource.user_id != fence.user_id:
                    raise WhatsAppIntegrationNotFoundError
                resource.status = IntegrationResourceStatus.DISABLED.value
                resource.updated_at = now
            await session.commit()

    async def mark_identity_rejection_pending(
        self,
        *,
        fence: WhatsAppLeaseFence,
        failure_code: str,
    ) -> None:
        if not failure_code:
            message = "WhatsApp identity rejection requires a failure code"
            raise ValueError(message)
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value:
                return
            now = utc_now()
            whatsapp_session.state = WhatsAppSessionState.DISCONNECTING.value
            whatsapp_session.failure_code = failure_code
            whatsapp_session.last_seen_at = now
            whatsapp_session.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            integration.enabled = False
            integration.status = IntegrationStatus.NEEDS_AUTH.value
            integration.setup_state = WhatsAppSessionState.DISCONNECTING.value
            integration.status_message = status_message(
                WhatsAppSessionState.DISCONNECTING,
                failure_code,
            )
            integration.last_checked_at = now
            integration.updated_at = now
            resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.resource_id,
            )
            if resource is None or resource.user_id != fence.user_id:
                raise WhatsAppIntegrationNotFoundError
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.updated_at = now
            await session.commit()

    async def complete_identity_rejection(
        self,
        *,
        fence: WhatsAppLeaseFence,
        failure_code: str,
    ) -> bool:
        if not failure_code:
            message = "WhatsApp identity rejection requires a failure code"
            raise ValueError(message)
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value:
                return False
            now = utc_now()
            whatsapp_session.state = WhatsAppSessionState.NEEDS_RELINK.value
            whatsapp_session.failure_code = failure_code
            whatsapp_session.validation_fingerprint = None
            whatsapp_session.validated_at = None
            whatsapp_session.connected_at = None
            whatsapp_session.challenge_ciphertext = None
            whatsapp_session.challenge_nonce = None
            whatsapp_session.challenge_expires_at = None
            whatsapp_session.last_seen_at = now
            whatsapp_session.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            integration.enabled = True
            integration.status = IntegrationStatus.NEEDS_AUTH.value
            integration.setup_state = WhatsAppSessionState.NEEDS_RELINK.value
            integration.status_message = status_message(
                WhatsAppSessionState.NEEDS_RELINK,
                failure_code,
            )
            integration.last_checked_at = now
            integration.updated_at = now
            resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.resource_id,
            )
            if resource is None or resource.user_id != fence.user_id:
                raise WhatsAppIntegrationNotFoundError
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.updated_at = now
            await _integration_service(
                session=session,
                tenant_factory=self._tenant_factory(fence.user_id),
                secret_vault=self._secret_vault,
            ).record_audit(
                user_id=fence.user_id,
                integration_id=fence.integration_id,
                provider=IntegrationProviderType.WHATSAPP,
                action=IntegrationAuditAction.WHATSAPP_SESSION_NEEDS_RELINK,
                actor_type=IntegrationAuditActorType.SYSTEM,
                actor_id=None,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={"failure_code": failure_code},
            )
            await session.commit()
        return True

    async def finalize_disconnect(
        self,
        *,
        fence: WhatsAppLeaseFence,
        credential_id: str,
    ) -> None:
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if whatsapp_session.desired_state != WhatsAppDesiredState.STOPPED.value:
                message = "WhatsApp session is not pending disconnect"
                raise ValueError(message)
            outboxes = (
                await session.exec(
                    select(WhatsAppOutboxRecord)
                    .where(
                        col(WhatsAppOutboxRecord.user_id) == fence.user_id,
                        col(WhatsAppOutboxRecord.integration_id) == fence.integration_id,
                        col(WhatsAppOutboxRecord.status).in_(
                            (
                                WhatsAppOutboxStatus.QUEUED.value,
                                WhatsAppOutboxStatus.SUBMITTING.value,
                            ),
                        ),
                    )
                    .with_for_update(),
                )
            ).all()
            reply_ids = tuple(reply_id for outbox in outboxes if (reply_id := outbox.reply_id) is not None)
            replies_by_id: dict[str, IntegrationReplyRecord] = {}
            if reply_ids:
                replies = (
                    await session.exec(
                        select(IntegrationReplyRecord)
                        .where(
                            col(IntegrationReplyRecord.user_id) == fence.user_id,
                            col(IntegrationReplyRecord.id).in_(reply_ids),
                        )
                        .with_for_update(),
                    )
                ).all()
                replies_by_id = {reply.id: reply for reply in replies}
            now = utc_now()
            for outbox in outboxes:
                was_submitting = outbox.status == WhatsAppOutboxStatus.SUBMITTING.value
                outbox.status = (
                    WhatsAppOutboxStatus.AMBIGUOUS.value if was_submitting else WhatsAppOutboxStatus.FAILED.value
                )
                outbox.owner_id = None
                outbox.lease_generation = None
                outbox.failure_code = (
                    "integration_disconnected_during_submission" if was_submitting else "integration_disconnected"
                )
                outbox.updated_at = now
                reply = replies_by_id.get(outbox.reply_id) if outbox.reply_id is not None else None
                if reply is not None:
                    reply.status = (
                        IntegrationReplyStatus.AMBIGUOUS.value
                        if was_submitting
                        else IntegrationReplyStatus.FAILED.value
                    )
                    reply.error_code = outbox.failure_code
                    reply.updated_at = now
            records = (
                await session.exec(
                    select(WhatsAppSessionKeyRecord).where(
                        col(WhatsAppSessionKeyRecord.user_id) == fence.user_id,
                        col(WhatsAppSessionKeyRecord.integration_id) == fence.integration_id,
                    ),
                )
            ).all()
            for record in records:
                await session.delete(record)
            await self._secret_vault.delete_secret(
                session=session,
                reference=_data_key_reference(
                    user_id=fence.user_id,
                    credential_id=credential_id,
                ),
            )
            credential = await session.get(
                IntegrationCredentialRecord,
                credential_id,
            )
            if credential is not None and credential.user_id == fence.user_id:
                credential.status = IntegrationCredentialStatus.REVOKED.value
                credential.revoked_at = now
                credential.updated_at = now
            integration = await _whatsapp_integration(
                session=session,
                fence=fence,
            )
            await _integration_service(
                session=session,
                tenant_factory=self._tenant_factory(fence.user_id),
                secret_vault=self._secret_vault,
            ).record_audit(
                user_id=fence.user_id,
                integration_id=fence.integration_id,
                provider=IntegrationProviderType.WHATSAPP,
                action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
                actor_type=IntegrationAuditActorType.USER,
                actor_id=fence.user_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={},
            )
            integration.enabled = False
            integration.status = IntegrationStatus.REVOKED.value
            integration.setup_state = WhatsAppSessionState.DISABLED.value
            integration.status_message = None
            integration.deleted_at = now
            integration.updated_at = now
            resources = (
                await session.exec(
                    select(IntegrationResourceRecord).where(
                        col(IntegrationResourceRecord.user_id) == fence.user_id,
                        col(IntegrationResourceRecord.integration_id) == fence.integration_id,
                        col(IntegrationResourceRecord.deleted_at).is_(None),
                    ),
                )
            ).all()
            for resource in resources:
                resource.status = IntegrationResourceStatus.DISABLED.value
                resource.deleted_at = now
                resource.updated_at = now
            await session.delete(whatsapp_session)
            await session.commit()

    async def accept_inbound_text(
        self,
        *,
        fence: WhatsAppLeaseFence,
        inbound: WhatsAppInboundText,
    ) -> str | None:
        if not is_whatsapp_chat_jid(inbound.chat_jid):
            return None
        async with self._worker_store.fenced_tenant_session(
            fence=fence,
        ) as session:
            whatsapp_session = await _locked_whatsapp_session(
                session=session,
                fence=fence,
            )
            if whatsapp_session.desired_state != WhatsAppDesiredState.CONNECTED.value or whatsapp_session.state in {
                WhatsAppSessionState.DISCONNECTING.value,
                WhatsAppSessionState.NEEDS_RELINK.value,
                WhatsAppSessionState.DISABLED.value,
                WhatsAppSessionState.FAILED.value,
            }:
                raise WhatsAppInboundUnavailableError
            if whatsapp_session.state != WhatsAppSessionState.CONNECTED.value:
                raise WhatsAppInboundRetryableError
            resource = await session.get(
                IntegrationResourceRecord,
                whatsapp_session.resource_id,
            )
            if resource is None or resource.user_id != fence.user_id:
                raise WhatsAppIntegrationNotFoundError
            identity = await WhatsAppContactIdentityService(session).observe(
                user_id=fence.user_id,
                integration_id=fence.integration_id,
                observation=WhatsAppIdentityObservation(
                    pn_jid=inbound.sender_pn_jid,
                    lid_jid=inbound.sender_lid_jid,
                    username=inbound.sender_username,
                    display_name=inbound.push_name,
                ),
            )
            sender_peer = identity.phone_number or identity.lid_jid or identity.pn_jid
            if sender_peer is None:
                return None
            conversation_peer = (
                inbound.chat_jid
                if inbound.chat_kind == "group"
                else identity.lid_jid or identity.pn_jid or inbound.sender_jid
            )
            service = _integration_service(
                session=session,
                tenant_factory=self._tenant_factory(fence.user_id),
                secret_vault=self._secret_vault,
            )
            event_record, created = await service.claim_communication_event(
                user_id=fence.user_id,
                integration_id=fence.integration_id,
                resource_id=resource.id,
                provider=IntegrationProviderType.WHATSAPP,
                event_type="message.received",
                direction=IntegrationDirection.INBOUND,
                external_event_id=inbound.provider_message_id,
                idempotency_key=(f"whatsapp:message.received:{fence.integration_id}:{inbound.provider_message_id}"),
                payload_json={
                    "chat_jid": inbound.chat_jid,
                    "chat_kind": inbound.chat_kind,
                    "conversation_peer": conversation_peer,
                    "sender_jid": inbound.sender_jid,
                    "sender_jid_alt": inbound.sender_jid_alt,
                    "sender_username": inbound.sender_username,
                    "sender_pn_jid": inbound.sender_pn_jid,
                    "sender_lid_jid": inbound.sender_lid_jid,
                    "addressing_mode": inbound.addressing_mode,
                    "identity_proof": inbound.identity_proof,
                    "push_name": inbound.push_name,
                    "contact_identity_id": identity.identity_id,
                    "contact_resolution": identity.state.value,
                    "contact_id": identity.contact_id,
                    "timestamp": inbound.timestamp,
                },
                safe_summary_json={
                    "event_type": "message.received",
                    "message_id": inbound.provider_message_id,
                    "from": identity.phone_number or "WhatsApp identity",
                    "to": whatsapp_session.phone_number,
                    "contact_resolution": identity.state.value,
                },
                status=IntegrationEventStatus.PROCESSING,
                retry_failed=True,
                retry_processing_after_seconds=(WHATSAPP_INBOUND_PROCESSING_LEASE_SECONDS),
            )
            if not created:
                await session.commit()
                run_id = event_record.safe_summary_json.get("agent_run_id")
                return run_id if isinstance(run_id, str) else None
            try:
                message_record = await service.record_communication_message(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.WHATSAPP,
                    direction=IntegrationDirection.INBOUND,
                    external_message_id=inbound.provider_message_id,
                    from_number=sender_peer,
                    to_number=resource.display_name,
                    contact_id=identity.contact_id,
                    contact_display_name=identity.contact_display_name,
                    body=inbound.body,
                    status=IntegrationMessageStatus.RECEIVED,
                    received_at=utc_now(),
                    payload_json={
                        "chat_jid": inbound.chat_jid,
                        "chat_kind": inbound.chat_kind,
                        "conversation_peer": conversation_peer,
                        "sender_jid": inbound.sender_jid,
                        "sender_jid_alt": inbound.sender_jid_alt,
                        "sender_username": inbound.sender_username,
                        "sender_pn_jid": inbound.sender_pn_jid,
                        "sender_lid_jid": inbound.sender_lid_jid,
                        "addressing_mode": inbound.addressing_mode,
                        "identity_proof": inbound.identity_proof,
                        "push_name": inbound.push_name,
                        "contact_identity_id": identity.identity_id,
                        "contact_resolution": identity.state.value,
                    },
                )
                disposition = await service.enqueue_inbound_communication_agent_reply(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.WHATSAPP,
                    medium=CommunicationMedium.WHATSAPP,
                    inbound_message_id=message_record.id,
                    external_message_id=inbound.provider_message_id,
                    remote_peer=sender_peer,
                    conversation_peer=conversation_peer,
                )
                event_record.safe_summary_json = {
                    **event_record.safe_summary_json,
                    **disposition.safe_metadata(),
                }
                event_record.status = IntegrationEventStatus.PROCESSED.value
                event_record.processed_at = utc_now()
                await service.record_audit(
                    user_id=fence.user_id,
                    integration_id=fence.integration_id,
                    provider=IntegrationProviderType.WHATSAPP,
                    action=IntegrationAuditAction.EVENT_ACCEPTED,
                    actor_type=IntegrationAuditActorType.SYSTEM,
                    actor_id=inbound.provider_message_id,
                    outcome=IntegrationAuditOutcome.SUCCEEDED,
                    safe_metadata={
                        "event_type": "message.received",
                        **disposition.safe_metadata(),
                    },
                )
                await session.commit()
                return disposition.run_id
            except Exception as error:
                await _record_inbound_failure(
                    session=session,
                    event_id=event_record.id,
                    error=error,
                )
                raise

    def _tenant_factory(self, user_id: str) -> AsyncSessionFactory:
        return tenant_scoped_session_factory(
            session_factory=self._base_session_factory,
            user_id=user_id,
        )


def phone_number_from_pn_jid(jid: str | None) -> str | None:
    return phone_number_from_whatsapp_pn_jid(jid)


async def _record_inbound_failure(
    *,
    session: AsyncSession,
    event_id: str,
    error: Exception,
) -> None:
    try:
        await session.rollback()
        event_record = await session.get(IntegrationEventRecord, event_id)
        if event_record is None:
            return
        event_record.status = IntegrationEventStatus.FAILED.value
        event_record.error_type = type(error).__name__[:128]
        event_record.error_message = "Inbound WhatsApp reply processing failed."
        event_record.processed_at = utc_now()
        await session.commit()
    except Exception as persistence_error:
        logger.exception(
            "Could not persist failed inbound WhatsApp event",
            extra={"failure_type": type(persistence_error).__name__},
        )


async def _locked_whatsapp_session(
    *,
    session: AsyncSession,
    fence: WhatsAppLeaseFence,
) -> WhatsAppSessionRecord:
    statement = (
        select(WhatsAppSessionRecord)
        .where(
            col(WhatsAppSessionRecord.user_id) == fence.user_id,
            col(WhatsAppSessionRecord.integration_id) == fence.integration_id,
        )
        .with_for_update()
    )
    record = (await session.exec(statement)).one_or_none()
    if record is None:
        raise WhatsAppIntegrationNotFoundError
    return record


async def _whatsapp_integration(
    *,
    session: AsyncSession,
    fence: WhatsAppLeaseFence,
) -> UserIntegrationRecord:
    integration = await session.get(
        UserIntegrationRecord,
        fence.integration_id,
    )
    if (
        integration is None
        or integration.user_id != fence.user_id
        or integration.provider != IntegrationProviderType.WHATSAPP.value
    ):
        raise WhatsAppIntegrationNotFoundError
    return integration


def _integration_service(
    *,
    session: AsyncSession,
    tenant_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
) -> UserIntegrationService:
    return UserIntegrationService(
        session=session,
        secret_vault=secret_vault,
        public_base_url=None,
        provider_registry=IntegrationProviderRegistry(
            telnyx=TelnyxIntegrationProvider(),
            twilio=TwilioIntegrationProvider(),
        ),
        session_factory=tenant_factory,
        authorization_service=SqlModelToolAuthorizationService(
            session_factory=tenant_factory,
        ),
    )


def _data_key_reference(
    *,
    user_id: str,
    credential_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=IntegrationProviderType.WHATSAPP.value,
        credential_id=credential_id,
        purpose=SecretPurpose.WHATSAPP_SESSION_DATA_KEY,
    )

import asyncio
from dataclasses import dataclass

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.models import (
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationCredentialKind,
    IntegrationCredentialStatus,
    IntegrationProviderType,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationStatus,
)
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationCredentialRecord,
    IntegrationResourceRecord,
    UserIntegrationRecord,
)
from voxos.utils.phone_numbers import normalize_e164_phone_number
from voxos.whatsapp.crypto import (
    encode_session_data_key,
    generate_session_data_key,
)
from voxos.whatsapp.errors import (
    WhatsAppChallengeNotReadyError,
    WhatsAppFeatureDisabledError,
    WhatsAppIntegrationConflictError,
    WhatsAppIntegrationNotFoundError,
    WhatsAppSourceResourceError,
)
from voxos.whatsapp.models import (
    WhatsAppChallengeResponse,
    WhatsAppDesiredState,
    WhatsAppLinkMethod,
    WhatsAppSessionState,
    WhatsAppSetupOption,
    WhatsAppSetupOptionsResponse,
    WhatsAppStatusResponse,
)
from voxos.whatsapp.records import WhatsAppSessionRecord
from voxos.whatsapp.settings import WhatsAppWorkerSettings
from voxos.whatsapp.store import WhatsAppSessionDataStore

SOURCE_PROVIDERS = {
    IntegrationProviderType.TELNYX.value,
    IntegrationProviderType.TWILIO.value,
}


@dataclass(frozen=True, slots=True)
class WhatsAppIntegrationService:
    session: AsyncSession
    session_factory: AsyncSessionFactory
    secret_vault: UserSecretVaultService
    settings: WhatsAppWorkerSettings

    async def list_setup_options(
        self,
        *,
        user_id: str,
    ) -> WhatsAppSetupOptionsResponse:
        statement = (
            select(IntegrationResourceRecord, UserIntegrationRecord)
            .join(
                UserIntegrationRecord,
                col(UserIntegrationRecord.id) == col(IntegrationResourceRecord.integration_id),
            )
            .where(
                col(IntegrationResourceRecord.user_id) == user_id,
                col(IntegrationResourceRecord.provider).in_(SOURCE_PROVIDERS),
                col(IntegrationResourceRecord.resource_type) == IntegrationResourceType.PHONE_NUMBER.value,
                col(IntegrationResourceRecord.status) == IntegrationResourceStatus.CONNECTED.value,
                col(IntegrationResourceRecord.deleted_at).is_(None),
                col(UserIntegrationRecord.user_id) == user_id,
                col(UserIntegrationRecord.enabled).is_(True),
                col(UserIntegrationRecord.status) == IntegrationStatus.CONNECTED.value,
                col(UserIntegrationRecord.deleted_at).is_(None),
            )
            .order_by(
                col(IntegrationResourceRecord.created_at),
                col(IntegrationResourceRecord.id),
            )
        )
        rows = (await self.session.exec(statement)).all()
        options: list[WhatsAppSetupOption] = []
        for resource, integration in rows:
            try:
                phone_number = normalize_e164_phone_number(
                    resource.display_name,
                )
            except ValueError:
                continue
            line_type = _safe_line_type(resource)
            options.append(
                WhatsAppSetupOption(
                    source_resource_id=resource.id,
                    provider=integration.provider,
                    phone_number=phone_number,
                    line_type=line_type,
                ),
            )
        return WhatsAppSetupOptionsResponse(options=tuple(options))

    async def start_link(
        self,
        *,
        user_id: str,
        source_resource_id: str,
        method: WhatsAppLinkMethod,
    ) -> WhatsAppStatusResponse:
        self._require_enabled()
        existing = await self._session_for_user(user_id=user_id, lock=True)
        if existing is not None:
            if existing.source_resource_id != source_resource_id:
                raise WhatsAppIntegrationConflictError
            integration = await self.session.get(
                UserIntegrationRecord,
                existing.integration_id,
            )
            if integration is None or integration.deleted_at is not None:
                message = "A previous WhatsApp disconnect is still being cleaned up."
                raise WhatsAppIntegrationConflictError(message)
            existing.desired_state = WhatsAppDesiredState.CONNECTED.value
            existing.link_method = method.value
            if existing.state in {
                WhatsAppSessionState.FAILED.value,
                WhatsAppSessionState.NEEDS_RELINK.value,
                WhatsAppSessionState.DISABLED.value,
            }:
                _request_fresh_link(existing=existing, method=method)
            existing.updated_at = utc_now()
            await self.session.commit()
            return status_response(existing)

        source_resource, source_integration, phone_number = await self.resolve_source_resource(
            user_id=user_id,
            source_resource_id=source_resource_id,
        )
        now = utc_now()
        integration = UserIntegrationRecord(
            user_id=user_id,
            provider=IntegrationProviderType.WHATSAPP.value,
            label="WhatsApp",
            status=IntegrationStatus.NEEDS_AUTH.value,
            enabled=True,
            auth_type=IntegrationAuthType.LINKED_DEVICE.value,
            setup_state=WhatsAppSessionState.AWAITING_LINK.value,
            default_agent_id=source_integration.default_agent_id,
            safe_metadata={
                "source_provider": source_integration.provider,
                "source_resource_id": source_resource.id,
                "phone_number": phone_number,
            },
            status_message=status_message(WhatsAppSessionState.AWAITING_LINK),
            created_at=now,
            updated_at=now,
        )
        credential = IntegrationCredentialRecord(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.WHATSAPP.value,
            secret_kind=IntegrationCredentialKind.WHATSAPP_SESSION_DATA_KEY.value,
            status=IntegrationCredentialStatus.ACTIVE.value,
            public_identifier="linked_device_session",
            safe_metadata={},
            created_at=now,
            updated_at=now,
        )
        resource = IntegrationResourceRecord(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.WHATSAPP.value,
            resource_type=IntegrationResourceType.PHONE_NUMBER.value,
            external_id=source_resource.id,
            display_name=phone_number,
            status=IntegrationResourceStatus.DISABLED.value,
            capabilities=[
                IntegrationCapability.WHATSAPP_READ.value,
                IntegrationCapability.WHATSAPP_SEND.value,
            ],
            safe_metadata={
                "source_provider": source_integration.provider,
                "source_resource_id": source_resource.id,
                "phone_number": phone_number,
            },
            retained_metadata_json={},
            created_at=now,
            updated_at=now,
        )
        whatsapp_session = WhatsAppSessionRecord(
            user_id=user_id,
            integration_id=integration.id,
            resource_id=resource.id,
            source_resource_id=source_resource.id,
            credential_id=credential.id,
            desired_state=WhatsAppDesiredState.CONNECTED.value,
            state=WhatsAppSessionState.AWAITING_LINK.value,
            link_method=method.value,
            phone_number=phone_number,
            created_at=now,
            updated_at=now,
        )
        self.session.add(integration)
        self.session.add(credential)
        self.session.add(resource)
        self.session.add(whatsapp_session)
        self.session.add(
            IntegrationAuditLogRecord(
                user_id=user_id,
                integration_id=integration.id,
                provider=IntegrationProviderType.WHATSAPP.value,
                action=IntegrationAuditAction.INTEGRATION_CREATED.value,
                actor_type=IntegrationAuditActorType.USER.value,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED.value,
                safe_metadata={
                    "source_provider": source_integration.provider,
                    "source_resource_id": source_resource.id,
                },
                created_at=now,
            ),
        )
        data_key = generate_session_data_key()
        await self.secret_vault.seal_secret(
            session=self.session,
            secret=encode_session_data_key(data_key),
            reference=_data_key_reference(
                user_id=user_id,
                credential_id=credential.id,
            ),
        )
        await self.session.commit()
        await self.session.refresh(whatsapp_session)
        return status_response(whatsapp_session)

    async def get_status(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> WhatsAppStatusResponse:
        record = await self._session_for_integration(
            user_id=user_id,
            integration_id=integration_id,
        )
        return status_response(record)

    async def get_challenge(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> WhatsAppChallengeResponse:
        record = await self._session_for_integration(
            user_id=user_id,
            integration_id=integration_id,
        )
        cipher = await self._data_store(user_id).resolve_cipher(
            credential_id=record.credential_id,
        )
        challenge = await self._data_store(user_id).get_challenge(
            integration_id=integration_id,
            cipher=cipher,
        )
        if challenge is None:
            raise WhatsAppChallengeNotReadyError
        return WhatsAppChallengeResponse(
            **status_response(record).model_dump(),
            method=challenge.method,
            value=challenge.value,
        )

    async def request_challenge(
        self,
        *,
        user_id: str,
        integration_id: str,
        method: WhatsAppLinkMethod,
    ) -> WhatsAppChallengeResponse:
        self._require_enabled()
        record = await self._session_for_integration(
            user_id=user_id,
            integration_id=integration_id,
            lock=True,
        )
        if record.state in {
            WhatsAppSessionState.CONNECTED.value,
            WhatsAppSessionState.NEEDS_RELINK.value,
            WhatsAppSessionState.DISCONNECTING.value,
            WhatsAppSessionState.FAILED.value,
            WhatsAppSessionState.DISABLED.value,
        }:
            message = "Relink WhatsApp before requesting a new link code from this state."
            raise WhatsAppIntegrationConflictError(message)
        record.link_method = method.value
        record.challenge_request_revision += 1
        record.challenge_ciphertext = None
        record.challenge_nonce = None
        record.challenge_expires_at = None
        record.state = WhatsAppSessionState.AWAITING_LINK.value
        record.failure_code = None
        record.updated_at = utc_now()
        await self.session.commit()
        return await self._wait_for_challenge(
            user_id=user_id,
            integration_id=integration_id,
        )

    async def relink(
        self,
        *,
        user_id: str,
        integration_id: str,
        method: WhatsAppLinkMethod,
    ) -> WhatsAppStatusResponse:
        self._require_enabled()
        record = await self._session_for_integration(
            user_id=user_id,
            integration_id=integration_id,
            lock=True,
        )
        record.desired_state = WhatsAppDesiredState.CONNECTED.value
        record.state = WhatsAppSessionState.DISCONNECTING.value
        record.link_method = method.value
        record.challenge_request_revision += 1
        record.challenge_ciphertext = None
        record.challenge_nonce = None
        record.challenge_expires_at = None
        record.failure_code = None
        record.updated_at = utc_now()
        integration = await self.session.get(
            UserIntegrationRecord,
            integration_id,
        )
        if integration is not None and integration.user_id == user_id:
            integration.enabled = True
            integration.status = IntegrationStatus.NEEDS_AUTH.value
            integration.setup_state = WhatsAppSessionState.DISCONNECTING.value
            integration.status_message = status_message(
                WhatsAppSessionState.DISCONNECTING,
            )
            integration.updated_at = record.updated_at
        self.session.add(
            IntegrationAuditLogRecord(
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.WHATSAPP.value,
                action=IntegrationAuditAction.WHATSAPP_RELINKED.value,
                actor_type=IntegrationAuditActorType.USER.value,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED.value,
                safe_metadata={"resource_id": record.resource_id},
                created_at=record.updated_at,
            ),
        )
        await self.session.commit()
        return status_response(record)

    async def begin_disconnect_if_whatsapp(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> bool:
        integration = await self.session.get(
            UserIntegrationRecord,
            integration_id,
        )
        if integration is None or integration.user_id != user_id or integration.deleted_at is not None:
            raise WhatsAppIntegrationNotFoundError
        if integration.provider != IntegrationProviderType.WHATSAPP.value:
            return False
        record = await self._session_for_integration(
            user_id=user_id,
            integration_id=integration_id,
            lock=True,
        )
        now = utc_now()
        record.desired_state = WhatsAppDesiredState.STOPPED.value
        record.state = WhatsAppSessionState.DISCONNECTING.value
        record.challenge_ciphertext = None
        record.challenge_nonce = None
        record.challenge_expires_at = None
        record.updated_at = now
        integration.enabled = False
        integration.status = IntegrationStatus.REVOKED.value
        integration.setup_state = WhatsAppSessionState.DISCONNECTING.value
        integration.status_message = status_message(
            WhatsAppSessionState.DISCONNECTING,
        )
        integration.updated_at = now
        resources = (
            await self.session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.user_id) == user_id,
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                    col(IntegrationResourceRecord.deleted_at).is_(None),
                ),
            )
        ).all()
        for resource in resources:
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.updated_at = now
        await self.session.commit()
        return True

    async def _wait_for_challenge(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> WhatsAppChallengeResponse:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.settings.whatsapp_api_challenge_wait_seconds
        while loop.time() < deadline:
            try:
                return await self.get_challenge(
                    user_id=user_id,
                    integration_id=integration_id,
                )
            except WhatsAppChallengeNotReadyError:
                await asyncio.sleep(
                    min(
                        self.settings.whatsapp_worker_poll_interval_seconds,
                        max(0.05, deadline - loop.time()),
                    ),
                )
        raise WhatsAppChallengeNotReadyError

    async def resolve_source_resource(
        self,
        *,
        user_id: str,
        source_resource_id: str,
    ) -> tuple[
        IntegrationResourceRecord,
        UserIntegrationRecord,
        str,
    ]:
        resource = await self.session.get(
            IntegrationResourceRecord,
            source_resource_id,
        )
        if (
            resource is None
            or resource.user_id != user_id
            or resource.provider not in SOURCE_PROVIDERS
            or resource.resource_type != IntegrationResourceType.PHONE_NUMBER.value
            or resource.status != IntegrationResourceStatus.CONNECTED.value
            or resource.deleted_at is not None
        ):
            raise WhatsAppSourceResourceError
        source_integration = await self.session.get(
            UserIntegrationRecord,
            resource.integration_id,
        )
        if (
            source_integration is None
            or source_integration.user_id != user_id
            or not source_integration.enabled
            or source_integration.status != IntegrationStatus.CONNECTED.value
            or source_integration.deleted_at is not None
        ):
            raise WhatsAppSourceResourceError
        try:
            phone_number = normalize_e164_phone_number(resource.display_name)
        except ValueError as error:
            raise WhatsAppSourceResourceError from error
        return resource, source_integration, phone_number

    async def _session_for_user(
        self,
        *,
        user_id: str,
        lock: bool = False,
    ) -> WhatsAppSessionRecord | None:
        statement = select(WhatsAppSessionRecord).where(
            col(WhatsAppSessionRecord.user_id) == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        return (await self.session.exec(statement)).one_or_none()

    async def _session_for_integration(
        self,
        *,
        user_id: str,
        integration_id: str,
        lock: bool = False,
    ) -> WhatsAppSessionRecord:
        statement = select(WhatsAppSessionRecord).where(
            col(WhatsAppSessionRecord.user_id) == user_id,
            col(WhatsAppSessionRecord.integration_id) == integration_id,
        )
        if lock:
            statement = statement.with_for_update()
        record = (await self.session.exec(statement)).one_or_none()
        if record is None:
            raise WhatsAppIntegrationNotFoundError
        integration = await self.session.get(
            UserIntegrationRecord,
            integration_id,
        )
        if (
            integration is None
            or integration.user_id != user_id
            or integration.provider != IntegrationProviderType.WHATSAPP.value
            or integration.deleted_at is not None
        ):
            raise WhatsAppIntegrationNotFoundError
        return record

    def _data_store(self, user_id: str) -> WhatsAppSessionDataStore:
        return WhatsAppSessionDataStore(
            session_factory=self.session_factory,
            vault=self.secret_vault,
            user_id=user_id,
        )

    def _require_enabled(self) -> None:
        if not self.settings.whatsapp_enabled:
            raise WhatsAppFeatureDisabledError


def status_response(record: WhatsAppSessionRecord) -> WhatsAppStatusResponse:
    state = WhatsAppSessionState(record.state)
    return WhatsAppStatusResponse(
        integration_id=record.integration_id,
        source_resource_id=record.source_resource_id,
        phone_number=record.phone_number,
        state=state,
        status_message=status_message(state, record.failure_code),
        validated_at=record.validated_at,
        connected_at=record.connected_at,
        challenge_expires_at=record.challenge_expires_at,
    )


def status_message(
    state: WhatsAppSessionState,
    failure_code: str | None = None,
) -> str:
    if failure_code == "number_mismatch":
        if state == WhatsAppSessionState.DISCONNECTING:
            return (
                "The linked WhatsApp number does not match the selected phone "
                "number. Removing the attempted linked device."
            )
        return "The linked WhatsApp number does not match the selected phone number. The linked device was removed."
    if failure_code == "pn_identity_unavailable":
        if state == WhatsAppSessionState.DISCONNECTING:
            return (
                "WhatsApp did not provide an authoritative phone-number identity. Removing the attempted linked device."
            )
        return "WhatsApp did not provide an authoritative phone-number identity. Relink the selected number."
    if failure_code == "terminal_local_failure":
        return (
            "WhatsApp closed this linked-device attempt before it could complete. "
            "The local bridge is available, but this attempt cannot be resumed."
        )
    if failure_code == "reconnect_exhausted":
        return (
            "WhatsApp did not reconnect after several attempts. "
            "The local bridge is available, but this attempt cannot be resumed."
        )
    messages = {
        WhatsAppSessionState.AWAITING_LINK: ("Open Linked devices in WhatsApp Business and scan or enter the code."),
        WhatsAppSessionState.CONNECTING: "WhatsApp is connecting.",
        WhatsAppSessionState.CONNECTED: ("WhatsApp is connected and the selected number is verified."),
        WhatsAppSessionState.RECONNECTING: "WhatsApp is reconnecting.",
        WhatsAppSessionState.WAITING_FOR_CAPACITY: ("WhatsApp setup is queued for an available worker."),
        WhatsAppSessionState.NEEDS_RELINK: ("WhatsApp revoked this linked device. Relink to continue."),
        WhatsAppSessionState.DISCONNECTING: ("WhatsApp is removing the previous linked session."),
        WhatsAppSessionState.FAILED: ("WhatsApp could not connect. Try relinking the device."),
        WhatsAppSessionState.DISABLED: "WhatsApp is disconnected.",
    }
    return messages[state]


def _safe_line_type(resource: IntegrationResourceRecord) -> str | None:
    value = resource.safe_metadata.get("line_type")
    if isinstance(value, str) and 0 < len(value) <= 64:
        return value
    return None


def _request_fresh_link(
    *,
    existing: WhatsAppSessionRecord,
    method: WhatsAppLinkMethod,
) -> None:
    existing.state = WhatsAppSessionState.DISCONNECTING.value
    existing.link_method = method.value
    existing.challenge_request_revision += 1
    existing.challenge_ciphertext = None
    existing.challenge_nonce = None
    existing.challenge_expires_at = None
    existing.failure_code = None


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

import base64
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import NoReturn, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import orjson
from pydantic import SecretStr
from sqlalchemy import or_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_core.response_profiles import CommunicationMedium, response_profile
from voxos.agent_identity.models import AgentScope
from voxos.agent_identity.records import UserAgentRecord
from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.effective_tool_policy import (
    EffectiveToolPolicySnapshot,
    policy_snapshot_from_grants,
)
from voxos.agent_runtime.records import AgentRunRecord
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolReference,
    ToolRisk,
)
from voxos.contacts.services import UserContactService
from voxos.credentials.models import SecretOwner, SecretPurpose, SecretReference
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import new_record_id, utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.integrations.conversations import (
    IntegrationConversationService,
    validate_conversation_provider_medium,
)
from voxos.integrations.errors import (
    IntegrationCredentialError,
    IntegrationNotFoundError,
    IntegrationResourceError,
    IntegrationToolPermissionError,
    IntegrationWebhookError,
)
from voxos.integrations.inbound_access import (
    UNKNOWN_CONTACT_SUPPRESSION_REASON,
    InboundCommunicationAccessResolver,
    InboundCommunicationReplyDisposition,
    disposition_from_reply,
)
from voxos.integrations.models import (
    CommunicationOutboundCallRequest,
    CommunicationOutboundSmsRequest,
    IntegrationActionResponse,
    IntegrationAuditAction,
    IntegrationAuditActorType,
    IntegrationAuditOutcome,
    IntegrationAuthType,
    IntegrationCallActivity,
    IntegrationCallActivityListResponse,
    IntegrationCallLatencyPercentiles,
    IntegrationCallLatencyReport,
    IntegrationCallOpeningState,
    IntegrationCallStatus,
    IntegrationCapability,
    IntegrationCredentialKind,
    IntegrationCredentialStatus,
    IntegrationCredentialSummary,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationListResponse,
    IntegrationMessageActivity,
    IntegrationMessageActivityListResponse,
    IntegrationMessageStatus,
    IntegrationProviderType,
    IntegrationReadinessCheck,
    IntegrationReadinessStatus,
    IntegrationReplyStatus,
    IntegrationResourceListResponse,
    IntegrationResourceResponse,
    IntegrationResourceStatus,
    IntegrationResourceType,
    IntegrationResponse,
    IntegrationRouteStatus,
    IntegrationStatus,
    IntegrationTranscriptStatus,
    IntegrationWebhookPurpose,
    TelnyxPhoneSetupRequest,
    TelnyxPhoneSetupResponse,
    TelnyxProviderReadiness,
    TelnyxReadinessResponse,
    TwilioCredentialSetupRequest,
    TwilioCredentialSetupResponse,
    TwilioOutboundCallRequest,
    TwilioOutboundSmsRequest,
    TwilioPhoneNumberCandidate,
    TwilioResourceConnectRequest,
    TwilioResourceConnectResponse,
    last_four,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    TelnyxCredentialBundle,
    TelnyxPhoneNumberCleanup,
    TelnyxPhoneNumberConfiguration,
    TelnyxProviderCapabilities,
    TwilioCredentialBundle,
    TwilioProviderCapabilities,
)
from voxos.integrations.providers.telnyx.provider import (
    TelnyxPhoneNumberCapabilityError,
    TelnyxProviderError,
)
from voxos.integrations.providers.twilio.provider import (
    TwilioProviderError,
)
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationCredentialRecord,
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    UserIntegrationRecord,
)
from voxos.integrations.tool_names import (
    TWILIO_PLACE_CALL_TOOL_NAME,
    TWILIO_SEND_SMS_TOOL_NAME,
    WHATSAPP_SEND_MESSAGE_TOOL_NAME,
    provider_integration_tool_name,
)
from voxos.integrations.whatsapp_jids import (
    is_whatsapp_group_jid,
    whatsapp_conversation_peer_from_payload,
    whatsapp_target_jid_from_payload,
)
from voxos.logos.domains import logo_for_integration_provider
from voxos.mcp.non_admin_access import non_admin_mcp_tool_access
from voxos.text.records import UserTextPreferenceRecord
from voxos.voice.errors import VoiceWorkerUnavailableError

DEFAULT_TWILIO_STATUS_METHOD = "POST"
DEFAULT_TWILIO_WEBHOOK_METHOD = "POST"
TWILIO_CREDENTIAL_VALIDATION_ERROR = "Twilio credentials could not be validated."
INTEGRATION_LABEL_EXISTS_ERROR = "An active integration already uses this label."
WEBHOOK_OVERWRITE_CONFIRMATION_ERROR = "Twilio webhook overwrite confirmation is required."
TWILIO_PHONE_NUMBER_NOT_FOUND_ERROR = "Selected Twilio phone number was not found."
TWILIO_CREDENTIAL_METADATA_ERROR = "Twilio credential metadata is incomplete."
INTEGRATION_RESOURCE_NOT_FOUND_ERROR = "Integration resource was not found."
INTEGRATION_CREDENTIAL_METADATA_MISSING_ERROR = "Integration credential metadata was not found."
INTEGRATION_CREDENTIAL_VALUE_MISSING_ERROR = "Integration credential value was not found."
INBOUND_COMMUNICATION_SAFE_INTERNAL_TOOLS = frozenset(
    {
        "get_current_datetime",
        "read_voxos_doc_page",
        "search_voxos_docs",
    },
)
INBOUND_COMMUNICATION_ADMIN_INTERNAL_TOOLS = frozenset({"search_contacts"})
WEBHOOK_ROUTE_NOT_FOUND_ERROR = "Webhook route was not found."
TWILIO_CAPABILITY_UNSUPPORTED_ERROR = "Selected Twilio number does not support requested capabilities."
TWILIO_VOICE_WEBHOOK_MISSING_ERROR = "Twilio voice webhook URL is not configured."
TWILIO_MEDIA_STREAM_WEBHOOK_MISSING_ERROR = "Twilio media stream URL is not configured."
TWILIO_WEBHOOK_CONFIGURATION_FAILED_ERROR = "Twilio webhook configuration failed."
TWILIO_SMS_SEND_FAILED_ERROR = "Twilio SMS send failed."
TWILIO_CALL_START_FAILED_ERROR = "Twilio call start failed."
TWILIO_PUBLIC_BASE_URL_REQUIRED_ERROR = "API_PUBLIC_BASE_URL is required to configure Twilio webhooks."
TWILIO_PUBLIC_BASE_URL_SCHEME_ERROR = "Twilio public base URL must start with https://."
TWILIO_CALL_SESSION_PERSISTENCE_ERROR = "Call session could not be persisted."
OUTBOUND_CALL_SESSION_CORRELATION_ERROR = "Outbound call session correlation is invalid."
OUTBOUND_CALL_SESSION_QUERY_PARAMETER = "voxos_call_id"
TWILIO_MESSAGE_PERSISTENCE_ERROR = "Twilio message could not be persisted."
TWILIO_EVENT_PERSISTENCE_ERROR = "Twilio event could not be persisted."
TELNYX_CREDENTIAL_VALIDATION_ERROR = "Telnyx credentials could not be validated."
TELNYX_NUMBER_ASSIGNMENT_CONFIRMATION_ERROR = "Telnyx number assignment confirmation is required."
TELNYX_REPAIR_CONFIRMATION_ERROR = "Telnyx provider repair confirmation is required."
TELNYX_WEBHOOK_CONFIGURATION_FAILED_ERROR = "Telnyx webhook configuration failed."
TELNYX_DISCONNECT_CLEANUP_FAILED_ERROR = "Telnyx routing cleanup failed for one or more resources."
TELNYX_SMS_SEND_FAILED_ERROR = "Telnyx SMS send failed."
TELNYX_CALL_START_FAILED_ERROR = "Telnyx call start failed."
TELNYX_OUTBOUND_VOICE_NOT_READY_ERROR = (
    "Telnyx outbound voice routing is not ready. Verify or repair the phone integration before dialing."
)
TELNYX_CONFIGURATION_CHECK_FAILED_ERROR = "Telnyx phone configuration could not be verified."
TELNYX_CREDENTIAL_METADATA_ERROR = "Telnyx credential metadata is incomplete."
TELNYX_WEBHOOK_ROUTE_METADATA_ERROR = "Telnyx webhook route metadata is invalid."
WORKER_ROUTING_UNAVAILABLE_CODE = "worker_routing_unavailable"
WORKER_READINESS_UNAVAILABLE_CODE = "worker_readiness_unavailable"
CALL_ROUTE_METADATA_MISSING_CODE = "call_route_metadata_missing"
REDACTED_WEBHOOK_VALUE = "[redacted]"
WEBHOOK_URL_METADATA_KEYS = frozenset(
    {
        "voice_url",
        "sms_url",
        "status_callback",
        "media_stream_url",
    },
)


@dataclass(frozen=True, slots=True)
class TwilioWebhookUrls:
    voice_url: str
    sms_url: str
    status_url: str
    media_stream_url: str


@dataclass(frozen=True, slots=True)
class CreatedWebhookRoutes:
    voice_route: IntegrationWebhookRouteRecord
    sms_route: IntegrationWebhookRouteRecord
    status_route: IntegrationWebhookRouteRecord
    media_stream_route: IntegrationWebhookRouteRecord
    urls: TwilioWebhookUrls


@dataclass(frozen=True, slots=True)
class IntegrationWebhookRouteContext:
    route: IntegrationWebhookRouteRecord
    route_key_hash: str


@dataclass(frozen=True, slots=True)
class TwilioCallStart:
    call_session: IntegrationCallSessionRecord
    stream_url: str
    twiml: str


@dataclass(frozen=True, slots=True)
class TwilioWebhookState:
    voice_url: str | None = None
    voice_method: str | None = None
    sms_url: str | None = None
    sms_method: str | None = None
    status_callback: str | None = None
    status_callback_method: str | None = None

    def to_metadata(self) -> dict[str, object]:
        return {
            "voice_url": self.voice_url,
            "voice_method": self.voice_method,
            "sms_url": self.sms_url,
            "sms_method": self.sms_method,
            "status_callback": self.status_callback,
            "status_callback_method": self.status_callback_method,
        }


class VoiceWorkerDemandRequester(Protocol):
    async def __call__(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        """Persist demand for a server-owned call route without waiting for readiness."""


class VoiceCallPreflight(Protocol):
    async def __call__(self, *, session: AsyncSession, user_id: str) -> None:
        """Validate tenant voice routing before creating an external call."""


class VoiceWorkerReadinessWaiter(Protocol):
    async def __call__(self, *, user_id: str, runtime_session_id: str) -> None:
        """Wait until the tenant project's worker registration is dispatchable."""


class VoiceAgentDispatcher(Protocol):
    async def __call__(
        self,
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
    ) -> None:
        """Explicitly dispatch the routed voice agent to a reserved call room."""


class VoiceAgentReadinessWaiter(Protocol):
    async def __call__(self, *, user_id: str, runtime_session_id: str) -> None:
        """Wait until the per-call agent and realtime provider session are ready."""


class VoiceWorkerDemandCanceller(Protocol):
    async def __call__(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        """Cancel a call demand inside the caller's tenant transaction."""


class VoiceWorkerDemandCompleter(Protocol):
    async def __call__(
        self,
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
    ) -> None:
        """Complete a call demand inside the caller's tenant transaction."""


@dataclass(frozen=True, slots=True)
class UserIntegrationService:
    session: AsyncSession
    secret_vault: UserSecretVaultService
    public_base_url: str | None
    provider_registry: IntegrationProviderRegistry
    session_factory: AsyncSessionFactory
    authorization_service: ToolAuthorizationService | None = None
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None
    voice_agent_dispatcher: VoiceAgentDispatcher | None = None
    voice_agent_readiness_waiter: VoiceAgentReadinessWaiter | None = None
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None
    voice_worker_demand_completer: VoiceWorkerDemandCompleter | None = None
    voice_call_preflight: VoiceCallPreflight | None = None

    async def list_integrations(self, user_id: str) -> IntegrationListResponse:
        statement = (
            select(UserIntegrationRecord)
            .where(
                col(UserIntegrationRecord.user_id) == user_id,
                col(UserIntegrationRecord.deleted_at).is_(None),
            )
            .order_by(col(UserIntegrationRecord.created_at).desc())
        )
        result = await self.session.exec(statement)
        return IntegrationListResponse(
            integrations=tuple(integration_response(record) for record in result.all()),
        )

    async def list_resources(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> IntegrationResourceListResponse:
        await self._integration_record(user_id=user_id, integration_id=integration_id)
        statement = (
            select(IntegrationResourceRecord)
            .where(
                col(IntegrationResourceRecord.user_id) == user_id,
                col(IntegrationResourceRecord.integration_id) == integration_id,
                col(IntegrationResourceRecord.deleted_at).is_(None),
            )
            .order_by(col(IntegrationResourceRecord.created_at).asc())
        )
        result = await self.session.exec(statement)
        return IntegrationResourceListResponse(
            resources=tuple(resource_response(record) for record in result.all()),
        )

    async def verify_telnyx_phone_configuration(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> TelnyxReadinessResponse:
        integration, resource = await self._telnyx_integration_resource(
            user_id=user_id,
            integration_id=integration_id,
        )
        credentials = await self.load_telnyx_credentials(
            user_id=user_id,
            integration_id=integration_id,
        )
        webhook_urls = await self._expected_telnyx_webhook_urls(resource)
        try:
            configuration = await self._telnyx_provider().inspect_phone_number_configuration(
                credentials=credentials,
                phone_number_id=resource.external_id,
                outbound_voice_profile_id=metadata_string_value(
                    resource.safe_metadata,
                    "outbound_voice_profile_id",
                ),
                expected_voice_url=webhook_urls.voice_url,
                expected_sms_url=webhook_urls.sms_url,
            )
        except TelnyxProviderError as exc:
            raise IntegrationResourceError(TELNYX_CONFIGURATION_CHECK_FAILED_ERROR) from exc
        response = await self._telnyx_readiness_response(
            user_id=user_id,
            integration=integration,
            resource=resource,
            configuration=configuration,
            repaired=False,
        )
        await self._record_telnyx_readiness(
            integration=integration,
            resource=resource,
            response=response,
        )
        return response

    async def repair_telnyx_phone_configuration(
        self,
        *,
        user_id: str,
        integration_id: str,
        confirm_provider_changes: bool,
    ) -> TelnyxReadinessResponse:
        if not confirm_provider_changes:
            raise IntegrationWebhookError(TELNYX_REPAIR_CONFIRMATION_ERROR)
        integration, resource = await self._telnyx_integration_resource(
            user_id=user_id,
            integration_id=integration_id,
        )
        credentials = await self.load_telnyx_credentials(
            user_id=user_id,
            integration_id=integration_id,
        )
        outbound_voice_profile_id = metadata_string_value(
            resource.safe_metadata,
            "outbound_voice_profile_id",
        )
        if outbound_voice_profile_id is None:
            raise IntegrationCredentialError(TELNYX_CREDENTIAL_METADATA_ERROR)
        webhook_urls = await self._expected_telnyx_webhook_urls(resource)
        try:
            configuration = await self._telnyx_provider().repair_phone_number_configuration(
                credentials=credentials,
                phone_number_id=resource.external_id,
                outbound_voice_profile_id=outbound_voice_profile_id,
                label=integration.label,
                expected_voice_url=webhook_urls.voice_url,
                expected_sms_url=webhook_urls.sms_url,
            )
        except TelnyxProviderError as exc:
            raise IntegrationResourceError(TELNYX_WEBHOOK_CONFIGURATION_FAILED_ERROR) from exc
        response = await self._telnyx_readiness_response(
            user_id=user_id,
            integration=integration,
            resource=resource,
            configuration=configuration,
            repaired=True,
        )
        resource.retained_metadata_json = {
            **resource.retained_metadata_json,
            "current_webhooks": {
                "voice_url": webhook_urls.voice_url,
                "sms_url": webhook_urls.sms_url,
                "status_callback": webhook_urls.status_url,
                "media_stream_url": webhook_urls.media_stream_url,
            },
        }
        await self._audit(
            user_id=user_id,
            integration_id=integration_id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.WEBHOOK_CONFIGURED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"resource_id": resource.id, "repair": True},
        )
        await self._record_telnyx_readiness(
            integration=integration,
            resource=resource,
            response=response,
        )
        return response

    async def list_message_activity(
        self,
        *,
        user_id: str,
        integration_id: str,
        limit: int,
        offset: int,
    ) -> IntegrationMessageActivityListResponse:
        await self._integration_record(user_id=user_id, integration_id=integration_id)
        statement = (
            select(IntegrationMessageRecord)
            .where(
                col(IntegrationMessageRecord.user_id) == user_id,
                col(IntegrationMessageRecord.integration_id) == integration_id,
            )
            .order_by(col(IntegrationMessageRecord.created_at).desc())
            .offset(offset)
            .limit(limit + 1)
        )
        records = list((await self.session.exec(statement)).all())
        has_more = len(records) > limit
        page = records[:limit]
        run_by_message_id = await self._agent_runs_by_message_id(
            user_id=user_id,
            records=page,
        )
        reply_by_message_id = await self._replies_by_message_id(
            user_id=user_id,
            records=page,
        )
        run_by_reply_id = await self._agent_runs_by_reply_id(
            user_id=user_id,
            replies=tuple(reply_by_message_id.values()),
        )
        activities: list[IntegrationMessageActivity] = []
        for record in page:
            reply = reply_by_message_id.get(record.id)
            run = run_by_message_id.get(record.external_message_id)
            if reply is not None and reply.agent_run_id is not None:
                run = run_by_reply_id.get(reply.agent_run_id)
            activities.append(
                message_activity_response(
                    record,
                    run=run,
                    reply=reply,
                ),
            )
        return IntegrationMessageActivityListResponse(
            messages=tuple(activities),
            offset=offset,
            limit=limit,
            next_offset=offset + limit if has_more else None,
        )

    async def list_call_activity(
        self,
        *,
        user_id: str,
        integration_id: str,
        limit: int,
        offset: int,
    ) -> IntegrationCallActivityListResponse:
        await self._integration_record(user_id=user_id, integration_id=integration_id)
        statement = (
            select(IntegrationCallSessionRecord)
            .where(
                col(IntegrationCallSessionRecord.user_id) == user_id,
                col(IntegrationCallSessionRecord.integration_id) == integration_id,
            )
            .order_by(col(IntegrationCallSessionRecord.created_at).desc())
            .offset(offset)
            .limit(limit + 1)
        )
        records = list((await self.session.exec(statement)).all())
        has_more = len(records) > limit
        return IntegrationCallActivityListResponse(
            calls=tuple(call_activity_response(record) for record in records[:limit]),
            offset=offset,
            limit=limit,
            next_offset=offset + limit if has_more else None,
        )

    async def call_latency_report(
        self,
        *,
        user_id: str,
        integration_id: str,
        provider: IntegrationProviderType | None = None,
    ) -> IntegrationCallLatencyReport:
        await self._integration_record(user_id=user_id, integration_id=integration_id)
        statement = select(IntegrationCallSessionRecord).where(
            col(IntegrationCallSessionRecord.user_id) == user_id,
            col(IntegrationCallSessionRecord.integration_id) == integration_id,
            col(IntegrationCallSessionRecord.direction) == IntegrationDirection.OUTBOUND.value,
        )
        if provider is not None:
            statement = statement.where(
                col(IntegrationCallSessionRecord.provider) == provider.value,
            )
        records = tuple((await self.session.exec(statement)).all())
        return IntegrationCallLatencyReport(
            integration_id=integration_id,
            provider=provider,
            worker_registration_to_agent_ready=latency_percentiles(
                elapsed_milliseconds(
                    record.worker_registered_at,
                    record.agent_ready_at,
                )
                for record in records
            ),
            agent_ready_to_provider_dial=latency_percentiles(
                elapsed_milliseconds(
                    record.agent_ready_at,
                    record.provider_dial_requested_at,
                )
                for record in records
            ),
            provider_dial_to_answer=latency_percentiles(
                elapsed_milliseconds(
                    record.provider_dial_requested_at,
                    record.answered_at,
                )
                for record in records
            ),
            provider_dial_to_media_ready=latency_percentiles(
                elapsed_milliseconds(
                    record.provider_dial_requested_at,
                    record.media_ready_at,
                )
                for record in records
            ),
            answer_to_first_speech=latency_percentiles(
                elapsed_milliseconds(
                    record.answered_at,
                    record.first_agent_speech_at,
                )
                for record in records
            ),
            opening_scheduled_to_playout_complete=latency_percentiles(
                elapsed_milliseconds(
                    record.opening_scheduled_at,
                    record.opening_completed_at,
                )
                for record in records
            ),
        )

    async def setup_twilio_credentials(
        self,
        *,
        user_id: str,
        request: TwilioCredentialSetupRequest,
    ) -> TwilioCredentialSetupResponse:
        await self._ensure_label_available(user_id=user_id, label=request.label)
        credentials = TwilioCredentialBundle(
            account_sid=request.account_sid,
            api_key_sid=request.api_key_sid,
            api_key_secret=request.api_key_secret,
            auth_token=request.auth_token,
        )
        provider = self._twilio_provider()
        try:
            validation = await provider.validate_credentials(credentials)
            if not validation.valid:
                raise IntegrationCredentialError(validation.status_message or TWILIO_CREDENTIAL_VALIDATION_ERROR)
            phone_numbers = await provider.list_phone_numbers(credentials)
        except TwilioProviderError as exc:
            raise IntegrationCredentialError(TWILIO_CREDENTIAL_VALIDATION_ERROR) from exc

        integration = UserIntegrationRecord(
            user_id=user_id,
            provider=IntegrationProviderType.TWILIO.value,
            label=request.label,
            status=IntegrationStatus.UNTESTED.value,
            enabled=False,
            setup_state="credentials_validated",
            safe_metadata=validation.safe_metadata,
            last_checked_at=utc_now(),
            status_message=validation.status_message,
        )
        self.session.add(integration)
        await self.session.flush()

        api_key_record = await self._upsert_credential(
            user_id=user_id,
            integration_id=integration.id,
            kind=IntegrationCredentialKind.TWILIO_API_KEY_SECRET,
            public_identifier=request.api_key_sid,
            secret=request.api_key_secret,
        )
        auth_token_record = await self._upsert_credential(
            user_id=user_id,
            integration_id=integration.id,
            kind=IntegrationCredentialKind.TWILIO_AUTH_TOKEN,
            public_identifier=request.account_sid,
            secret=request.auth_token,
        )

        integration.updated_at = utc_now()
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            action=IntegrationAuditAction.INTEGRATION_CREATED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"provider": IntegrationProviderType.TWILIO.value},
        )
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            action=IntegrationAuditAction.CREDENTIALS_SAVED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "api_key_sid_last_four": last_four(request.api_key_sid),
                "auth_token_last_four": last_four(request.auth_token.get_secret_value()),
            },
        )
        await self.session.commit()
        await self.session.refresh(integration)
        await self.session.refresh(api_key_record)
        await self.session.refresh(auth_token_record)
        return TwilioCredentialSetupResponse(
            integration=integration_response(integration),
            credentials=(
                credential_summary(api_key_record),
                credential_summary(auth_token_record),
            ),
            phone_numbers=phone_numbers,
        )

    async def setup_telnyx_phone(
        self,
        *,
        user_id: str,
        request: TelnyxPhoneSetupRequest,
    ) -> TelnyxPhoneSetupResponse:
        if not request.confirm_number_assignment:
            raise IntegrationWebhookError(TELNYX_NUMBER_ASSIGNMENT_CONFIRMATION_ERROR)
        await self._ensure_label_available(user_id=user_id, label=request.label)
        stream_auth_token = SecretStr(new_stream_auth_token())
        credentials = TelnyxCredentialBundle(
            api_key=request.api_key,
            webhook_public_key=request.webhook_public_key,
            stream_auth_token=stream_auth_token,
        )
        provider = self._telnyx_provider()
        try:
            validation = await provider.validate_credentials(credentials)
            if not validation.valid:
                raise IntegrationCredentialError(validation.status_message or TELNYX_CREDENTIAL_VALIDATION_ERROR)
        except TelnyxProviderError as exc:
            raise IntegrationCredentialError(TELNYX_CREDENTIAL_VALIDATION_ERROR) from exc

        integration = UserIntegrationRecord(
            user_id=user_id,
            provider=IntegrationProviderType.TELNYX.value,
            label=request.label,
            status=IntegrationStatus.UNTESTED.value,
            enabled=False,
            setup_state="credentials_validated",
            safe_metadata=validation.safe_metadata,
            last_checked_at=utc_now(),
            status_message=validation.status_message,
        )
        self.session.add(integration)
        await self.session.flush()
        api_key_record = await self._upsert_credential(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            kind=IntegrationCredentialKind.PROVIDER_API_SECRET,
            public_identifier=request.phone_number,
            secret=request.api_key,
        )
        await self._upsert_credential(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            kind=IntegrationCredentialKind.TELNYX_STREAM_AUTH_TOKEN,
            public_identifier="media_stream",
            secret=stream_auth_token,
        )
        resource = await self._upsert_telnyx_phone_number_resource(
            user_id=user_id,
            integration_id=integration.id,
            request=request,
        )
        await self._disable_routes_for_resource(
            user_id=user_id,
            integration_id=integration.id,
            resource_id=resource.id,
        )
        routes = self._create_webhook_routes(
            user_id=user_id,
            integration_id=integration.id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
        )
        resource.retained_metadata_json = {
            "current_webhooks": {
                "voice_url": routes.urls.voice_url,
                "sms_url": routes.urls.sms_url,
                "status_callback": routes.urls.status_url,
                "media_stream_url": routes.urls.media_stream_url,
            },
            "setup_mode": "api_configured",
        }
        resource.status = IntegrationResourceStatus.FAILED.value
        resource.updated_at = utc_now()
        await self.session.commit()
        try:
            provisioning = await provider.provision_phone_number(
                credentials=credentials,
                label=request.label,
                phone_number=request.phone_number,
                webhook_event_url=routes.urls.voice_url,
                webhook_url=routes.urls.sms_url,
            )
        except TelnyxPhoneNumberCapabilityError as exc:
            await self._disable_routes_for_resource(
                user_id=user_id,
                integration_id=integration.id,
                resource_id=resource.id,
            )
            resource.status = IntegrationResourceStatus.FAILED.value
            integration.status = IntegrationStatus.FAILED.value
            integration.status_message = str(exc)
            integration.updated_at = utc_now()
            resource.updated_at = utc_now()
            await self.session.commit()
            raise IntegrationResourceError(str(exc)) from exc
        except TelnyxProviderError as exc:
            await self._disable_routes_for_resource(
                user_id=user_id,
                integration_id=integration.id,
                resource_id=resource.id,
            )
            resource.status = IntegrationResourceStatus.FAILED.value
            resource.retained_metadata_json = {
                **resource.retained_metadata_json,
                "webhook_configuration_error": str(exc),
            }
            integration.status = IntegrationStatus.FAILED.value
            integration.status_message = TELNYX_WEBHOOK_CONFIGURATION_FAILED_ERROR
            integration.updated_at = utc_now()
            resource.updated_at = utc_now()
            await self.session.commit()
            raise IntegrationResourceError(TELNYX_WEBHOOK_CONFIGURATION_FAILED_ERROR) from exc

        resource.retained_metadata_json = {
            **resource.retained_metadata_json,
            "telnyx_number": provisioning.number,
            "telnyx_previous_number": provisioning.previous_number,
            "telnyx_voice_application": provisioning.voice_application,
            "telnyx_messaging_profile": provisioning.messaging_profile,
            "telnyx_outbound_voice_profile": provisioning.outbound_voice_profile,
        }
        resource.external_id = provisioning.phone_number_id
        resource.safe_metadata = {
            **resource.safe_metadata,
            "connection_id": provisioning.connection_id,
            "messaging_profile_id": provisioning.messaging_profile_id,
            "outbound_voice_profile_id": provisioning.outbound_voice_profile_id,
        }
        resource.capabilities = [capability.value for capability in provisioning.capabilities]
        resource.status = IntegrationResourceStatus.CONNECTED.value
        resource.updated_at = utc_now()
        integration.enabled = True
        integration.status = IntegrationStatus.CONNECTED.value
        integration.setup_state = "connected"
        integration.status_message = None
        integration.safe_metadata = {
            **validation.safe_metadata,
            "connection_id": provisioning.connection_id,
            "messaging_profile_id": provisioning.messaging_profile_id,
            "outbound_voice_profile_id": provisioning.outbound_voice_profile_id,
        }
        integration.updated_at = utc_now()
        api_key_record.public_identifier = provisioning.connection_id
        api_key_record.updated_at = utc_now()
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.INTEGRATION_CREATED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"provider": IntegrationProviderType.TELNYX.value},
        )
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.CREDENTIALS_SAVED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"credential_public_identifier": provisioning.connection_id},
        )
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.WEBHOOK_CONFIGURED,
            actor_type=IntegrationAuditActorType.SYSTEM,
            actor_id=None,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "resource_id": resource.id,
                "phone_number": resource.display_name,
            },
        )
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.RESOURCE_CONNECTED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "resource_id": resource.id,
                "resource_type": resource.resource_type,
            },
        )
        await self.session.commit()
        await self.session.refresh(integration)
        await self.session.refresh(resource)
        await self.session.refresh(api_key_record)
        await self.preapprove_resource_tools(
            user_id=user_id,
            integration_id=integration.id,
            resource=resource,
        )
        return TelnyxPhoneSetupResponse(
            integration=integration_response(integration),
            credentials=(credential_summary(api_key_record),),
            resources=(resource_response(resource),),
        )

    async def connect_twilio_resources(
        self,
        *,
        user_id: str,
        integration_id: str,
        request: TwilioResourceConnectRequest,
    ) -> TwilioResourceConnectResponse:
        if not request.overwrite_existing_webhooks:
            raise IntegrationWebhookError(WEBHOOK_OVERWRITE_CONFIRMATION_ERROR)
        integration = await self._integration_record(
            user_id=user_id,
            integration_id=integration_id,
        )
        credentials = await self.load_twilio_credentials(
            user_id=user_id,
            integration_id=integration_id,
        )
        provider = self._twilio_provider()
        phone_numbers = {number.sid: number for number in await provider.list_phone_numbers(credentials)}

        resources: list[IntegrationResourceRecord] = []
        for selection in request.resources:
            candidate = phone_numbers.get(selection.phone_number_sid)
            if candidate is None:
                raise IntegrationResourceError(TWILIO_PHONE_NUMBER_NOT_FOUND_ERROR)
            ensure_selected_capabilities_supported(selection.capabilities, candidate)
            resource = await self._upsert_twilio_phone_number_resource(
                user_id=user_id,
                integration_id=integration_id,
                candidate=candidate,
                capabilities=selection.capabilities,
                transcript_storage_enabled=selection.transcript_storage_enabled,
            )
            await self._disable_routes_for_resource(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource.id,
            )
            routes = self._create_webhook_routes(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource.id,
                provider=IntegrationProviderType.TWILIO,
            )
            previous_settings = previous_webhook_settings_for_reconnect(
                candidate=candidate,
                resource=resource,
            )
            resource.retained_metadata_json = {
                "previous_webhooks": previous_settings.to_metadata(),
                "current_webhooks": {
                    "voice_url": routes.urls.voice_url,
                    "voice_method": DEFAULT_TWILIO_WEBHOOK_METHOD,
                    "sms_url": routes.urls.sms_url,
                    "sms_method": DEFAULT_TWILIO_WEBHOOK_METHOD,
                    "status_callback": routes.urls.status_url,
                    "status_callback_method": DEFAULT_TWILIO_STATUS_METHOD,
                    "media_stream_url": routes.urls.media_stream_url,
                },
                "twilio_response": {},
            }
            resource.status = IntegrationResourceStatus.FAILED.value
            resource.updated_at = utc_now()
            self.session.add(resource)
            await self.session.commit()
            try:
                updated = await provider.configure_phone_number_webhooks(
                    credentials=credentials,
                    phone_number_id=candidate.sid,
                    voice_url=routes.urls.voice_url,
                    voice_method=DEFAULT_TWILIO_WEBHOOK_METHOD,
                    sms_url=routes.urls.sms_url,
                    sms_method=DEFAULT_TWILIO_WEBHOOK_METHOD,
                    status_callback=routes.urls.status_url,
                    status_callback_method=DEFAULT_TWILIO_STATUS_METHOD,
                )
            except TwilioProviderError as exc:
                await self._disable_routes_for_resource(
                    user_id=user_id,
                    integration_id=integration_id,
                    resource_id=resource.id,
                )
                resource.status = IntegrationResourceStatus.FAILED.value
                resource.retained_metadata_json = {
                    **resource.retained_metadata_json,
                    "webhook_configuration_error": str(exc),
                }
                resource.updated_at = utc_now()
                integration.status = IntegrationStatus.FAILED.value
                integration.status_message = TWILIO_WEBHOOK_CONFIGURATION_FAILED_ERROR
                integration.updated_at = utc_now()
                await self.session.commit()
                raise IntegrationResourceError(TWILIO_WEBHOOK_CONFIGURATION_FAILED_ERROR) from exc
            resource.retained_metadata_json = {
                **resource.retained_metadata_json,
                "twilio_response": {
                    "sid": updated.sid,
                    "voice_url": updated.voice_url,
                    "sms_url": updated.sms_url,
                    "status_callback": updated.status_callback,
                },
            }
            resource.status = IntegrationResourceStatus.CONNECTED.value
            resource.updated_at = utc_now()
            await self._audit(
                user_id=user_id,
                integration_id=integration_id,
                action=IntegrationAuditAction.WEBHOOK_OVERWRITE_CONFIRMED,
                actor_type=IntegrationAuditActorType.USER,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={"resource_id": resource.id},
            )
            await self._audit(
                user_id=user_id,
                integration_id=integration_id,
                action=IntegrationAuditAction.WEBHOOK_CONFIGURED,
                actor_type=IntegrationAuditActorType.SYSTEM,
                actor_id=None,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={
                    "resource_id": resource.id,
                    "phone_number": resource.display_name,
                },
            )
            await self._audit(
                user_id=user_id,
                integration_id=integration_id,
                action=IntegrationAuditAction.RESOURCE_CONNECTED,
                actor_type=IntegrationAuditActorType.USER,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.SUCCEEDED,
                safe_metadata={
                    "resource_id": resource.id,
                    "resource_type": resource.resource_type,
                },
            )
            resources.append(resource)

        integration.enabled = True
        integration.status = IntegrationStatus.CONNECTED.value
        integration.setup_state = "connected"
        integration.status_message = None
        integration.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(integration)
        for resource in resources:
            await self.session.refresh(resource)
        for resource in resources:
            await self.preapprove_resource_tools(
                user_id=user_id,
                integration_id=integration_id,
                resource=resource,
            )
        return TwilioResourceConnectResponse(
            integration=integration_response(integration),
            resources=tuple(resource_response(record) for record in resources),
        )

    async def disconnect_integration(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> None:
        integration = await self._integration_record(
            user_id=user_id,
            integration_id=integration_id,
        )
        if integration.provider == IntegrationProviderType.TELNYX.value:
            await self._disconnect_telnyx_integration(
                user_id=user_id,
                integration=integration,
            )
            return
        if integration.provider != IntegrationProviderType.TWILIO.value:
            await self._disconnect_local_integration(
                user_id=user_id,
                integration=integration,
                provider=IntegrationProviderType(integration.provider),
            )
            return
        credentials = await self.load_twilio_credentials(
            user_id=user_id,
            integration_id=integration_id,
        )
        provider = self._twilio_provider()
        resources = await self._resource_records(
            user_id=user_id,
            integration_id=integration_id,
        )
        cleanup_failed = False
        for resource in resources:
            previous = previous_webhook_settings(resource)
            if previous is not None:
                try:
                    await provider.configure_phone_number_webhooks(
                        credentials=credentials,
                        phone_number_id=resource.external_id,
                        voice_url=previous.voice_url,
                        voice_method=previous.voice_method,
                        sms_url=previous.sms_url,
                        sms_method=previous.sms_method,
                        status_callback=previous.status_callback,
                        status_callback_method=previous.status_callback_method,
                        clear_missing_urls=True,
                    )
                except TwilioProviderError as exc:
                    resource.status = IntegrationResourceStatus.FAILED_DISCONNECT_CLEANUP.value
                    resource.retained_metadata_json = {
                        **resource.retained_metadata_json,
                        "disconnect_cleanup_error": str(exc),
                    }
                    resource.updated_at = utc_now()
                    cleanup_failed = True
                    continue
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.deleted_at = utc_now()
            resource.updated_at = utc_now()
            await self._disable_routes_for_resource(
                user_id=user_id,
                integration_id=integration_id,
                resource_id=resource.id,
            )
        if cleanup_failed:
            integration.enabled = False
            integration.status = IntegrationStatus.FAILED.value
            integration.status_message = "Twilio webhook restoration failed for one or more resources."
            integration.updated_at = utc_now()
            await self._audit(
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.TWILIO,
                action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
                actor_type=IntegrationAuditActorType.USER,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.FAILED,
                safe_metadata={},
            )
            await self.session.commit()
            return
        await self._disable_routes(user_id=user_id, integration_id=integration_id)
        await self._revoke_credentials(user_id=user_id, integration_id=integration_id)
        integration.enabled = False
        integration.status = IntegrationStatus.REVOKED.value
        integration.status_message = None
        integration.deleted_at = utc_now()
        integration.updated_at = utc_now()
        await self._audit(
            user_id=user_id,
            integration_id=integration_id,
            provider=IntegrationProviderType.TWILIO,
            action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={},
        )
        await self.session.commit()

    async def load_twilio_credentials(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> TwilioCredentialBundle:
        integration = await self._integration_record(
            user_id=user_id,
            integration_id=integration_id,
        )
        account_sid = integration.safe_metadata.get("account_sid")
        api_key_sid = integration.safe_metadata.get("api_key_sid")
        if not isinstance(account_sid, str) or not isinstance(api_key_sid, str):
            raise IntegrationCredentialError(TWILIO_CREDENTIAL_METADATA_ERROR)
        api_key_secret = await self._load_secret(
            user_id=user_id,
            integration_id=integration_id,
            kind=IntegrationCredentialKind.TWILIO_API_KEY_SECRET,
        )
        auth_token = await self._load_secret(
            user_id=user_id,
            integration_id=integration_id,
            kind=IntegrationCredentialKind.TWILIO_AUTH_TOKEN,
        )
        return TwilioCredentialBundle(
            account_sid=account_sid,
            api_key_sid=api_key_sid,
            api_key_secret=api_key_secret,
            auth_token=auth_token,
        )

    async def load_telnyx_credentials(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> TelnyxCredentialBundle:
        integration = await self._integration_record(
            user_id=user_id,
            integration_id=integration_id,
        )
        connection_id = integration.safe_metadata.get("connection_id")
        messaging_profile_id = integration.safe_metadata.get("messaging_profile_id")
        webhook_public_key = integration.safe_metadata.get("webhook_public_key")
        if (
            not isinstance(connection_id, str)
            or not isinstance(messaging_profile_id, str)
            or not isinstance(webhook_public_key, str)
        ):
            raise IntegrationCredentialError(TELNYX_CREDENTIAL_METADATA_ERROR)
        api_key = await self._load_secret(
            user_id=user_id,
            integration_id=integration_id,
            kind=IntegrationCredentialKind.PROVIDER_API_SECRET,
        )
        stream_auth_token = await self._load_secret(
            user_id=user_id,
            integration_id=integration_id,
            kind=IntegrationCredentialKind.TELNYX_STREAM_AUTH_TOKEN,
        )
        return TelnyxCredentialBundle(
            api_key=api_key,
            webhook_public_key=webhook_public_key,
            connection_id=connection_id,
            messaging_profile_id=messaging_profile_id,
            stream_auth_token=stream_auth_token,
        )

    async def start_twilio_call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        direction: IntegrationDirection,
        call_sid: str,
        from_number: str | None,
        to_number: str | None,
        payload_json: dict[str, object],
    ) -> IntegrationCallSessionRecord:
        return await self.start_communication_call_session(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.TWILIO,
            direction=direction,
            call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
            payload_json=payload_json,
        )

    async def start_telnyx_call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        direction: IntegrationDirection,
        call_control_id: str,
        from_number: str | None,
        to_number: str | None,
        payload_json: dict[str, object],
        request_voice_worker: bool = True,
        transcript_enabled_override: bool | None = None,
        outbound_objective: str | None = None,
        initiating_run_id: str | None = None,
    ) -> IntegrationCallSessionRecord:
        return await self.start_communication_call_session(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.TELNYX,
            direction=direction,
            call_sid=call_control_id,
            from_number=from_number,
            to_number=to_number,
            payload_json=payload_json,
            initial_status=IntegrationCallStatus.INITIATING,
            answered=False,
            request_voice_worker=request_voice_worker,
            transcript_enabled_override=transcript_enabled_override,
            outbound_objective=outbound_objective,
            initiating_run_id=initiating_run_id,
        )

    async def start_communication_call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        direction: IntegrationDirection,
        call_sid: str,
        from_number: str | None,
        to_number: str | None,
        payload_json: dict[str, object],
        agent_id: str | None = None,
        runtime_session_id: str | None = None,
        livekit_room_name_override: str | None = None,
        initial_status: IntegrationCallStatus = IntegrationCallStatus.IN_PROGRESS,
        answered: bool = True,
        request_voice_worker: bool = True,
        transcript_enabled_override: bool | None = None,
        outbound_objective: str | None = None,
        initiating_run_id: str | None = None,
    ) -> IntegrationCallSessionRecord:
        resource = await self._resource_record(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
        )
        route_agent_id = agent_id
        if route_agent_id is None:
            route_context = await AgentIdentityService(
                session_factory=self.session_factory,
            ).get_or_create_default_agent(user_id)
            route_agent_id = route_context.id
        existing = await self._call_session_by_external_id(
            user_id=user_id,
            provider=provider,
            call_sid=call_sid,
        )
        transcript_enabled = (
            transcript_enabled_override
            if transcript_enabled_override is not None
            else bool(resource.safe_metadata.get("transcript_storage_enabled"))
        )
        transcript_status = (
            IntegrationTranscriptStatus.ENABLED.value
            if transcript_enabled
            else IntegrationTranscriptStatus.DISABLED.value
        )
        if existing is not None:
            existing = update_existing_call_session(
                existing=existing,
                agent_id=route_agent_id,
                call_sid=call_sid,
                payload_json=payload_json,
                initial_status=initial_status,
                answered=answered,
            )
            if request_voice_worker:
                await self._request_voice_worker(existing)
            return existing

        remote_number = remote_party_phone_number(
            direction=direction,
            from_number=from_number,
            to_number=to_number,
        )
        contact = (
            await UserContactService(session=self.session).get_by_phone_number(
                user_id=user_id,
                phone_number=remote_number,
            )
            if remote_number is not None
            else None
        )
        now = utc_now()
        call_session_id = new_record_id()
        is_goal_directed_outbound = direction == IntegrationDirection.OUTBOUND and outbound_objective is not None
        await self._insert_call_session_ignore(
            {
                "id": call_session_id,
                "user_id": user_id,
                "integration_id": integration_id,
                "resource_id": resource_id,
                "provider": provider.value,
                "direction": direction.value,
                "external_call_id": call_sid,
                "from_number": from_number,
                "to_number": to_number,
                "status": initial_status.value,
                "contact_id": contact.id if contact is not None else None,
                "contact_display_name": (contact.display_name if contact is not None else None),
                "caller_is_admin": contact.is_admin if contact is not None else False,
                "agent_id": route_agent_id,
                "runtime_session_id": runtime_session_id or new_record_id(),
                "initiating_run_id": initiating_run_id,
                "livekit_room_name": (
                    livekit_room_name_override or livekit_room_name(provider=provider, call_sid=call_sid)
                ),
                "media_participant_identity": (
                    outbound_media_participant_identity(call_session_id) if is_goal_directed_outbound else None
                ),
                "media_stream_id": None,
                "outbound_objective": outbound_objective,
                "opening_state": (IntegrationCallOpeningState.PENDING.value if is_goal_directed_outbound else None),
                "opening_attempt_count": 0 if is_goal_directed_outbound else None,
                "transcript_enabled": transcript_enabled,
                "transcript_status": transcript_status,
                "started_at": now,
                "answered_at": now if answered else None,
                "worker_registered_at": None,
                "agent_ready_at": None,
                "provider_dial_requested_at": None,
                "media_ready_at": None,
                "opening_claimed_at": None,
                "opening_scheduled_at": None,
                "first_agent_speech_at": None,
                "opening_completed_at": None,
                "ended_at": None,
                "payload_json": payload_json,
                "created_at": now,
                "updated_at": now,
            },
        )
        record = await self._call_session_by_external_id(
            user_id=user_id,
            provider=provider,
            call_sid=call_sid,
        )
        if record is None:
            raise IntegrationResourceError(TWILIO_CALL_SESSION_PERSISTENCE_ERROR)
        if record.agent_id != route_agent_id:
            record = update_existing_call_session(
                existing=record,
                agent_id=route_agent_id,
                call_sid=call_sid,
                payload_json=payload_json,
                initial_status=initial_status,
                answered=answered,
            )
        if request_voice_worker:
            await self._request_voice_worker(record)
        return record

    async def communication_call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        external_call_id: str,
    ) -> IntegrationCallSessionRecord | None:
        """Return a call only when it belongs to the routed tenant resource."""
        record = await self._call_session_by_external_id(
            user_id=user_id,
            provider=provider,
            call_sid=external_call_id,
        )
        if record is None:
            return None
        if record.integration_id != integration_id or record.resource_id != resource_id:
            raise IntegrationResourceError(OUTBOUND_CALL_SESSION_CORRELATION_ERROR)
        return record

    async def bind_outbound_call_session(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        call_session_id: str,
        external_call_id: str,
        payload_json: dict[str, object],
        answered: bool = False,
    ) -> IntegrationCallSessionRecord:
        statement = (
            select(IntegrationCallSessionRecord)
            .where(
                col(IntegrationCallSessionRecord.id) == call_session_id,
                col(IntegrationCallSessionRecord.user_id) == user_id,
                col(IntegrationCallSessionRecord.integration_id) == integration_id,
                col(IntegrationCallSessionRecord.resource_id) == resource_id,
                col(IntegrationCallSessionRecord.provider) == provider.value,
                col(IntegrationCallSessionRecord.direction) == IntegrationDirection.OUTBOUND.value,
            )
            .with_for_update()
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise IntegrationResourceError(
                OUTBOUND_CALL_SESSION_CORRELATION_ERROR,
            )
        reservation_id = record.payload_json.get("reservation_id")
        is_unbound_reservation = record.external_call_id == reservation_id or record.external_call_id.startswith(
            "reservation-"
        )
        if record.external_call_id != external_call_id and not is_unbound_reservation:
            raise IntegrationResourceError(
                OUTBOUND_CALL_SESSION_CORRELATION_ERROR,
            )
        existing = await self._call_session_by_external_id(
            user_id=user_id,
            provider=provider,
            call_sid=external_call_id,
        )
        if existing is not None and existing.id != record.id:
            raise IntegrationResourceError(
                OUTBOUND_CALL_SESSION_CORRELATION_ERROR,
            )

        now = utc_now()
        record.external_call_id = external_call_id
        record.payload_json = {
            **record.payload_json,
            "provider_call": payload_json,
        }
        if answered:
            record.status = IntegrationCallStatus.IN_PROGRESS.value
            record.answered_at = record.answered_at or now
        record.updated_at = now
        return record

    def mark_call_answered(
        self,
        record: IntegrationCallSessionRecord,
    ) -> None:
        now = utc_now()
        record.status = IntegrationCallStatus.IN_PROGRESS.value
        record.answered_at = record.answered_at or now
        record.ended_at = None
        record.updated_at = now

    async def _request_voice_worker(self, record: IntegrationCallSessionRecord) -> None:
        requester = self.voice_worker_demand_requester
        if requester is None:
            raise VoiceWorkerUnavailableError(WORKER_ROUTING_UNAVAILABLE_CODE)
        if record.agent_id is None or record.runtime_session_id is None or record.livekit_room_name is None:
            raise VoiceWorkerUnavailableError(CALL_ROUTE_METADATA_MISSING_CODE)
        await requester(
            session=self.session,
            user_id=record.user_id,
            agent_id=record.agent_id,
            runtime_session_id=record.runtime_session_id,
            room_name=record.livekit_room_name,
            source=record.provider,
        )

    async def wait_for_voice_worker(self, record: IntegrationCallSessionRecord) -> None:
        waiter = self.voice_worker_readiness_waiter
        if waiter is None:
            raise VoiceWorkerUnavailableError(WORKER_READINESS_UNAVAILABLE_CODE)
        if record.runtime_session_id is None:
            raise VoiceWorkerUnavailableError(CALL_ROUTE_METADATA_MISSING_CODE)
        await waiter(
            user_id=record.user_id,
            runtime_session_id=record.runtime_session_id,
        )
        record.worker_registered_at = record.worker_registered_at or utc_now()
        record.updated_at = utc_now()
        await self.session.commit()

    async def dispatch_voice_agent(self, record: IntegrationCallSessionRecord) -> None:
        dispatcher = self.voice_agent_dispatcher
        if dispatcher is None:
            raise VoiceWorkerUnavailableError(WORKER_ROUTING_UNAVAILABLE_CODE)
        if record.agent_id is None or record.runtime_session_id is None or record.livekit_room_name is None:
            raise VoiceWorkerUnavailableError(CALL_ROUTE_METADATA_MISSING_CODE)
        await dispatcher(
            user_id=record.user_id,
            agent_id=record.agent_id,
            runtime_session_id=record.runtime_session_id,
            room_name=record.livekit_room_name,
        )

    async def wait_for_voice_agent(self, record: IntegrationCallSessionRecord) -> None:
        waiter = self.voice_agent_readiness_waiter
        if waiter is None:
            raise VoiceWorkerUnavailableError(WORKER_READINESS_UNAVAILABLE_CODE)
        if record.runtime_session_id is None:
            raise VoiceWorkerUnavailableError(CALL_ROUTE_METADATA_MISSING_CODE)
        await waiter(
            user_id=record.user_id,
            runtime_session_id=record.runtime_session_id,
        )

    def mark_call_media_ready(
        self,
        record: IntegrationCallSessionRecord,
    ) -> None:
        now = utc_now()
        record.media_ready_at = record.media_ready_at or now
        record.updated_at = now

    async def cancel_voice_worker(
        self,
        record: IntegrationCallSessionRecord,
        *,
        failure_code: str,
    ) -> None:
        canceller = self.voice_worker_demand_canceller
        if canceller is None or record.runtime_session_id is None:
            return
        await canceller(
            session=self.session,
            user_id=record.user_id,
            runtime_session_id=record.runtime_session_id,
            failure_code=failure_code,
        )

    async def complete_voice_worker(
        self,
        record: IntegrationCallSessionRecord,
    ) -> None:
        completer = self.voice_worker_demand_completer
        if completer is None or record.runtime_session_id is None:
            return
        await completer(
            session=self.session,
            user_id=record.user_id,
            runtime_session_id=record.runtime_session_id,
        )

    async def persist_call_transcript(
        self,
        *,
        user_id: str,
        call_session_id: str,
        source: str,
        speaker: str,
        text: str,
        is_final: bool,
        confidence: float | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        payload_json: dict[str, object] | None = None,
    ) -> IntegrationCallTranscriptRecord | None:
        call_session = await self.session.get(IntegrationCallSessionRecord, call_session_id)
        if call_session is None or call_session.user_id != user_id:
            raise IntegrationNotFoundError
        if not call_session.transcript_enabled:
            return None
        record = IntegrationCallTranscriptRecord(
            user_id=user_id,
            integration_id=call_session.integration_id,
            call_session_id=call_session.id,
            provider=call_session.provider,
            source=source,
            speaker=speaker,
            text=text,
            is_final=is_final,
            confidence=confidence,
            started_at=started_at,
            ended_at=ended_at,
            payload_json=payload_json or {},
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def record_communication_message(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        direction: IntegrationDirection,
        external_message_id: str,
        from_number: str,
        to_number: str,
        body: str,
        status: IntegrationMessageStatus | str,
        contact_id: str | None = None,
        contact_display_name: str | None = None,
        segments: int | None = None,
        price: str | None = None,
        price_unit: str | None = None,
        sent_at: datetime | None = None,
        received_at: datetime | None = None,
        payload_json: dict[str, object] | None = None,
    ) -> IntegrationMessageRecord:
        existing = await self._message_by_external_id(
            user_id=user_id,
            provider=provider,
            message_sid=external_message_id,
        )
        if existing is not None:
            return existing
        now = utc_now()
        status_value = status.value if isinstance(status, IntegrationMessageStatus) else status
        await self._insert_message_ignore(
            {
                "id": new_record_id(),
                "user_id": user_id,
                "integration_id": integration_id,
                "resource_id": resource_id,
                "provider": provider.value,
                "direction": direction.value,
                "external_message_id": external_message_id,
                "from_number": from_number,
                "to_number": to_number,
                "contact_id": contact_id,
                "contact_display_name": contact_display_name,
                "body": body,
                "status": status_value,
                "segments": segments,
                "price": price,
                "price_unit": price_unit,
                "sent_at": sent_at,
                "received_at": received_at,
                "payload_json": payload_json or {},
                "created_at": now,
                "updated_at": now,
            },
        )
        record = await self._message_by_external_id(
            user_id=user_id,
            provider=provider,
            message_sid=external_message_id,
        )
        if record is None:
            raise IntegrationResourceError(TWILIO_MESSAGE_PERSISTENCE_ERROR)
        return record

    async def record_twilio_message(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        direction: IntegrationDirection,
        external_message_id: str,
        from_number: str,
        to_number: str,
        body: str,
        status: IntegrationMessageStatus | str,
        segments: int | None = None,
        price: str | None = None,
        price_unit: str | None = None,
        sent_at: datetime | None = None,
        received_at: datetime | None = None,
        payload_json: dict[str, object] | None = None,
    ) -> IntegrationMessageRecord:
        return await self.record_communication_message(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.TWILIO,
            direction=direction,
            external_message_id=external_message_id,
            from_number=from_number,
            to_number=to_number,
            body=body,
            status=status,
            segments=segments,
            price=price,
            price_unit=price_unit,
            sent_at=sent_at,
            received_at=received_at,
            payload_json=payload_json,
        )

    async def enqueue_inbound_sms_agent_reply(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        inbound_message_id: str,
        external_message_id: str,
        from_number: str,
    ) -> InboundCommunicationReplyDisposition:
        return await self.enqueue_inbound_communication_agent_reply(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            medium=CommunicationMedium.SMS,
            inbound_message_id=inbound_message_id,
            external_message_id=external_message_id,
            remote_peer=from_number,
        )

    async def enqueue_inbound_communication_agent_reply(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        medium: CommunicationMedium,
        inbound_message_id: str,
        external_message_id: str,
        remote_peer: str,
        conversation_peer: str | None = None,
    ) -> InboundCommunicationReplyDisposition:
        validate_conversation_provider_medium(
            provider=provider,
            medium=medium,
        )
        inbound_message = await self.session.get(
            IntegrationMessageRecord,
            inbound_message_id,
        )
        if (
            inbound_message is None
            or inbound_message.user_id != user_id
            or inbound_message.integration_id != integration_id
            or inbound_message.resource_id != resource_id
            or inbound_message.provider != provider.value
            or inbound_message.direction != IntegrationDirection.INBOUND.value
            or inbound_message.external_message_id != external_message_id
            or inbound_message.from_number != remote_peer
        ):
            message = "Inbound communication reply context is invalid."
            raise IntegrationResourceError(message)
        existing_reply = (
            await self.session.exec(
                select(IntegrationReplyRecord).where(
                    col(IntegrationReplyRecord.user_id) == user_id,
                    col(IntegrationReplyRecord.inbound_message_id) == inbound_message_id,
                ),
            )
        ).one_or_none()
        if existing_reply is not None:
            return disposition_from_reply(existing_reply)
        is_whatsapp_group = False
        resolved_conversation_peer = remote_peer
        if medium == CommunicationMedium.WHATSAPP:
            target_jid = whatsapp_target_jid_from_payload(inbound_message.payload_json)
            payload_conversation_peer = whatsapp_conversation_peer_from_payload(
                inbound_message.payload_json,
            )
            if target_jid is None:
                message = "Inbound WhatsApp reply target is invalid."
                raise IntegrationResourceError(message)
            if conversation_peer is not None and payload_conversation_peer != conversation_peer:
                message = "Inbound WhatsApp conversation context is invalid."
                raise IntegrationResourceError(message)
            resolved_conversation_peer = conversation_peer or payload_conversation_peer or remote_peer
            is_whatsapp_group = is_whatsapp_group_jid(target_jid)
        route_context = await AgentIdentityService(
            session_factory=self.session_factory,
        ).get_or_create_default_agent(user_id)
        access = await InboundCommunicationAccessResolver(self.session).resolve(
            user_id=user_id,
            inbound_message=inbound_message,
            remote_peer=remote_peer,
            shared_audience=is_whatsapp_group,
        )
        inbound_message.contact_id = access.contact_id
        inbound_message.contact_display_name = access.contact_display_name
        conversation = await IntegrationConversationService(
            session=self.session,
        ).resolve_conversation(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            medium=medium,
            remote_peer=resolved_conversation_peer,
            agent_id=route_context.id,
        )
        if not access.reply_allowed:
            reply = IntegrationReplyRecord(
                user_id=user_id,
                inbound_message_id=inbound_message_id,
                conversation_id=conversation.id,
                agent_run_id=None,
                status=IntegrationReplyStatus.SUPPRESSED.value,
                response_profile=response_profile(medium).identifier,
                suppression_reason=UNKNOWN_CONTACT_SUPPRESSION_REASON,
            )
            self.session.add(reply)
            await self.session.commit()
            return disposition_from_reply(reply)
        policy = await self._inbound_communication_policy(
            user_id=user_id,
            agent_id=route_context.id,
            session_id=conversation.runtime_session_id,
            peer_is_admin=access.admin_tools_allowed,
            medium=medium,
        )
        medium_label = "WhatsApp" if medium == CommunicationMedium.WHATSAPP else medium.value.upper()
        sender_display_name = access.contact_display_name
        run = await SqlModelAgentRunStore(
            session_factory=self.session_factory,
        ).enqueue_run(
            user_id=user_id,
            agent_id=route_context.id,
            kind=AgentRunKind.COMMUNICATION_REPLY,
            created_by_user_id=user_id,
            session_id=conversation.runtime_session_id,
            chat_id=conversation.runtime_session_id,
            payload_json={
                "title": f"Inbound {medium_label} reply",
                "task_prompt": inbound_communication_agent_prompt(
                    medium,
                    contact_display_name=sender_display_name,
                    is_group=is_whatsapp_group,
                ),
                "chat_id": conversation.runtime_session_id,
                "conversation_id": conversation.id,
                "inbound_message_id": inbound_message_id,
                "communication_medium": medium.value,
                "contact_id": access.contact_id,
                "contact_display_name": access.contact_display_name,
                "sender_display_name": sender_display_name,
                "is_group": is_whatsapp_group,
                "integration": {
                    "provider": provider.value,
                    "integration_id": integration_id,
                    "resource_id": resource_id,
                    "external_message_id": external_message_id,
                },
            },
            effective_tool_policy_json=policy.to_json(),
            idempotency_key=(
                f"inbound-{medium.value}:{provider.value}:{integration_id}:{resource_id}:{external_message_id}"
            ),
        )
        reply = IntegrationReplyRecord(
            user_id=user_id,
            inbound_message_id=inbound_message_id,
            conversation_id=conversation.id,
            agent_run_id=run.id,
            status=IntegrationReplyStatus.PENDING.value,
            response_profile=response_profile(medium).identifier,
        )
        self.session.add(reply)
        await self.session.commit()
        return disposition_from_reply(reply)

    async def _inbound_communication_policy(
        self,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        peer_is_admin: bool,
        medium: CommunicationMedium = CommunicationMedium.SMS,
    ) -> EffectiveToolPolicySnapshot:
        if self.authorization_service is None:
            return EffectiveToolPolicySnapshot(user_id=user_id, agent_id=agent_id)
        grants = await self.authorization_service.list_effective_grants(
            AgentScope(
                owner_user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                channel=medium.value,
                source=f"integrations.{medium.value}.inbound",
            ),
        )
        allowed_mcp_tools_by_server_id = {
            access.server_id: frozenset(access.tool_names)
            for access in await non_admin_mcp_tool_access(
                session=self.session,
                user_id=user_id,
            )
        }
        safe_grants = tuple(
            grant
            for grant in grants
            if grant.status == ToolGrantStatus.ALLOWED
            and (
                (
                    grant.tool_source == ToolGrantScope.MCP_SERVER
                    and grant.mcp_server_id is not None
                    and grant.tool_name in allowed_mcp_tools_by_server_id.get(grant.mcp_server_id, ())
                )
                or (
                    grant.tool_source == ToolGrantScope.INTERNAL
                    and grant.risk == ToolRisk.LOW
                    and (
                        grant.tool_name in INBOUND_COMMUNICATION_SAFE_INTERNAL_TOOLS
                        or (peer_is_admin and grant.tool_name in INBOUND_COMMUNICATION_ADMIN_INTERNAL_TOOLS)
                    )
                )
            )
        )
        return policy_snapshot_from_grants(
            user_id=user_id,
            agent_id=agent_id,
            grants=safe_grants,
        )

    async def send_sms(
        self,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        request: CommunicationOutboundSmsRequest,
    ) -> IntegrationActionResponse:
        await self._authorize_integration_tool(
            user_id=user_id,
            agent_id=request.agent_id,
            integration_id=request.integration_id,
            provider=provider,
            tool_name=provider_integration_tool_name(
                integration_id=request.integration_id,
                resource_id=request.resource_id,
                action=TWILIO_SEND_SMS_TOOL_NAME,
            ),
        )
        resource = await self._resource_record(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
        )
        resource_provider = IntegrationProviderType(resource.provider)
        if resource_provider != provider:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        if not resource_supports_action(resource=resource, action=TWILIO_SEND_SMS_TOOL_NAME):
            raise IntegrationResourceError(TWILIO_CAPABILITY_UNSUPPORTED_ERROR)
        status_callback_url = status_callback_url_for_resource(resource)
        if provider == IntegrationProviderType.TWILIO:
            twilio_credentials = await self.load_twilio_credentials(
                user_id=user_id,
                integration_id=request.integration_id,
            )
            provider_account_id = twilio_credentials.account_sid
            try:
                result = await self._twilio_provider().send_sms(
                    credentials=twilio_credentials,
                    from_number=resource.display_name,
                    to_number=request.to_number,
                    body=request.body,
                    status_callback_url=status_callback_url,
                )
            except TwilioProviderError as exc:
                raise IntegrationResourceError(TWILIO_SMS_SEND_FAILED_ERROR) from exc
        elif provider == IntegrationProviderType.TELNYX:
            telnyx_credentials = await self.load_telnyx_credentials(
                user_id=user_id,
                integration_id=request.integration_id,
            )
            telnyx_provider_account_id = telnyx_credentials.connection_id
            if telnyx_provider_account_id is None:
                raise IntegrationCredentialError(TELNYX_CREDENTIAL_METADATA_ERROR)
            provider_account_id = telnyx_provider_account_id
            try:
                result = await self._telnyx_provider().send_sms(
                    credentials=telnyx_credentials,
                    from_number=resource.display_name,
                    to_number=request.to_number,
                    body=request.body,
                    status_callback_url=status_callback_url,
                )
            except TelnyxProviderError as exc:
                raise IntegrationResourceError(TELNYX_SMS_SEND_FAILED_ERROR) from exc
        else:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        await self.record_communication_message(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
            provider=provider,
            direction=IntegrationDirection.OUTBOUND,
            external_message_id=result.external_id or result.id,
            from_number=resource.display_name,
            to_number=request.to_number,
            body=request.body,
            status=result.status,
            sent_at=utc_now(),
            payload_json={provider.value: result.safe_metadata},
        )
        await self._integration_event(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
            provider=provider,
            event_type="sms_outbound",
            direction=IntegrationDirection.OUTBOUND,
            external_event_id=result.external_id or result.id,
            idempotency_key=(
                f"{provider.value}:{provider_account_id}:message:{result.external_id or result.id}:outbound"
            ),
            payload_json={
                "message_sid": result.external_id or result.id,
                "from": resource.display_name,
                "to": request.to_number,
                "body": request.body,
                "status": result.status,
            },
            safe_summary_json={
                "message_sid": result.external_id or result.id,
                "from": resource.display_name,
                "to": request.to_number,
                "status": result.status,
            },
        )
        await self._audit(
            user_id=user_id,
            integration_id=request.integration_id,
            provider=provider,
            action=IntegrationAuditAction.SMS_SENT,
            actor_type=IntegrationAuditActorType.AGENT,
            actor_id=request.agent_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "resource_id": request.resource_id,
                "to": request.to_number,
            },
        )
        await self.session.commit()
        return result

    async def send_twilio_sms(
        self,
        *,
        user_id: str,
        request: TwilioOutboundSmsRequest,
    ) -> IntegrationActionResponse:
        return await self.send_sms(
            user_id=user_id,
            provider=IntegrationProviderType.TWILIO,
            request=request,
        )

    async def start_outbound_call(
        self,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        request: CommunicationOutboundCallRequest,
    ) -> IntegrationActionResponse:
        await self._authorize_integration_tool(
            user_id=user_id,
            agent_id=request.agent_id,
            integration_id=request.integration_id,
            provider=provider,
            tool_name=provider_integration_tool_name(
                integration_id=request.integration_id,
                resource_id=request.resource_id,
                action=TWILIO_PLACE_CALL_TOOL_NAME,
            ),
        )
        resource = await self._resource_record(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
        )
        resource_provider = IntegrationProviderType(resource.provider)
        if resource_provider != provider:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        if not resource_supports_action(resource=resource, action=TWILIO_PLACE_CALL_TOOL_NAME):
            raise IntegrationResourceError(TWILIO_CAPABILITY_UNSUPPORTED_ERROR)
        await self._validate_initiating_run(
            user_id=user_id,
            agent_id=request.agent_id,
            initiating_run_id=request.initiating_run_id,
        )
        if self.voice_call_preflight is None:
            raise VoiceWorkerUnavailableError(WORKER_ROUTING_UNAVAILABLE_CODE)
        await self.voice_call_preflight(session=self.session, user_id=user_id)
        status_url = status_callback_url_for_resource(resource)
        twilio_credentials: TwilioCredentialBundle | None = None
        telnyx_credentials: TelnyxCredentialBundle | None = None
        telnyx_webhook_urls: TwilioWebhookUrls | None = None
        if provider == IntegrationProviderType.TWILIO:
            twilio_credentials = await self.load_twilio_credentials(
                user_id=user_id,
                integration_id=request.integration_id,
            )
        elif provider == IntegrationProviderType.TELNYX:
            telnyx_credentials = await self.load_telnyx_credentials(
                user_id=user_id,
                integration_id=request.integration_id,
            )
            telnyx_webhook_urls = await self._preflight_telnyx_outbound_call(
                resource=resource,
                credentials=telnyx_credentials,
            )
            status_url = telnyx_webhook_urls.status_url
        else:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)

        reservation_id = f"reservation-{new_record_id()}"
        runtime_session_id = new_record_id()
        call = await self.start_communication_call_session(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
            provider=provider,
            direction=IntegrationDirection.OUTBOUND,
            call_sid=reservation_id,
            from_number=resource.display_name,
            to_number=request.to_number,
            payload_json={
                "reservation_id": reservation_id,
                "from": resource.display_name,
                "to": request.to_number,
                "status": IntegrationCallStatus.INITIATING.value,
            },
            agent_id=request.agent_id,
            runtime_session_id=runtime_session_id,
            livekit_room_name_override=livekit_room_name(
                provider=provider,
                call_sid=reservation_id,
            ),
            initial_status=IntegrationCallStatus.INITIATING,
            answered=False,
            outbound_objective=request.objective,
            initiating_run_id=request.initiating_run_id,
        )
        await self.session.commit()
        try:
            await self.wait_for_voice_worker(call)
            await self.dispatch_voice_agent(call)
            await self.wait_for_voice_agent(call)
        except BaseException:
            await self.fail_call_route(
                call,
                failure_code="worker_admission_failed",
            )
            raise

        await self.session.refresh(call)
        call.provider_dial_requested_at = call.provider_dial_requested_at or utc_now()
        call.updated_at = utc_now()
        await self.session.commit()
        if provider == IntegrationProviderType.TWILIO:
            if twilio_credentials is None:
                raise IntegrationCredentialError(TWILIO_CREDENTIAL_METADATA_ERROR)
            try:
                result = await self._twilio_provider().start_outbound_call(
                    credentials=twilio_credentials,
                    from_number=resource.display_name,
                    to_number=request.to_number,
                    twiml_url=outbound_call_callback_url(
                        voice_url_for_resource(resource),
                        call.id,
                    ),
                    status_callback_url=(
                        outbound_call_callback_url(status_url, call.id) if status_url is not None else None
                    ),
                )
            except TwilioProviderError as exc:
                await self.fail_call_route(
                    call,
                    failure_code="provider_call_start_failed",
                )
                raise IntegrationResourceError(TWILIO_CALL_START_FAILED_ERROR) from exc
        elif provider == IntegrationProviderType.TELNYX:
            if telnyx_credentials is None or telnyx_webhook_urls is None:
                raise IntegrationCredentialError(TELNYX_CREDENTIAL_METADATA_ERROR)
            try:
                result = await self._telnyx_provider().start_outbound_call(
                    credentials=telnyx_credentials,
                    from_number=resource.display_name,
                    to_number=request.to_number,
                    twiml_url=outbound_call_callback_url(
                        telnyx_webhook_urls.media_stream_url,
                        call.id,
                    ),
                    status_callback_url=status_url,
                    command_id=call.id,
                    client_state=telnyx_outbound_client_state(call.id),
                )
            except TelnyxProviderError as exc:
                await self.fail_call_route(
                    call,
                    failure_code="provider_call_start_failed",
                )
                raise IntegrationResourceError(TELNYX_CALL_START_FAILED_ERROR) from exc
        call_sid = result.external_id or result.id
        call = await self.bind_outbound_call_session(
            user_id=user_id,
            integration_id=request.integration_id,
            resource_id=request.resource_id,
            provider=provider,
            call_session_id=call.id,
            external_call_id=call_sid,
            payload_json={
                "call_id": call_sid,
                "from": resource.display_name,
                "to": request.to_number,
                "status": result.status,
            },
        )
        await self._audit(
            user_id=user_id,
            integration_id=request.integration_id,
            provider=provider,
            action=IntegrationAuditAction.CALL_STARTED,
            actor_type=IntegrationAuditActorType.AGENT,
            actor_id=request.agent_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={
                "resource_id": request.resource_id,
                "call_session_id": call.id,
            },
        )
        await self.session.commit()
        return result

    async def _validate_initiating_run(
        self,
        *,
        user_id: str,
        agent_id: str,
        initiating_run_id: str | None,
    ) -> None:
        if initiating_run_id is None:
            return
        run = await self.session.get(AgentRunRecord, initiating_run_id)
        if run is None or run.user_id != user_id or run.agent_id != agent_id or run.deleted_at is not None:
            message = "Initiating agent run was not found"
            raise IntegrationResourceError(message)

    async def fail_call_route(
        self,
        call: IntegrationCallSessionRecord,
        *,
        failure_code: str,
    ) -> None:
        now = utc_now()
        call.status = IntegrationCallStatus.FAILED.value
        call.ended_at = now
        call.payload_json = {
            **call.payload_json,
            "failure_code": failure_code,
        }
        call.updated_at = now
        await self.cancel_voice_worker(call, failure_code=failure_code)
        await self.session.commit()

    async def start_twilio_outbound_call(
        self,
        *,
        user_id: str,
        request: TwilioOutboundCallRequest,
    ) -> IntegrationActionResponse:
        return await self.start_outbound_call(
            user_id=user_id,
            provider=IntegrationProviderType.TWILIO,
            request=request,
        )

    async def _telnyx_integration_resource(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> tuple[UserIntegrationRecord, IntegrationResourceRecord]:
        integration = await self._integration_record(
            user_id=user_id,
            integration_id=integration_id,
        )
        if integration.provider != IntegrationProviderType.TELNYX.value:
            raise IntegrationNotFoundError
        resources = await self._resource_records(
            user_id=user_id,
            integration_id=integration_id,
        )
        resource = next(
            (
                item
                for item in resources
                if item.provider == IntegrationProviderType.TELNYX.value
                and item.status == IntegrationResourceStatus.CONNECTED.value
            ),
            None,
        )
        if resource is None:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        return integration, resource

    async def _expected_telnyx_webhook_urls(
        self,
        resource: IntegrationResourceRecord,
    ) -> TwilioWebhookUrls:
        current = current_webhook_settings(resource)
        statement = select(IntegrationWebhookRouteRecord).where(
            col(IntegrationWebhookRouteRecord.user_id) == resource.user_id,
            col(IntegrationWebhookRouteRecord.integration_id) == resource.integration_id,
            col(IntegrationWebhookRouteRecord.resource_id) == resource.id,
            col(IntegrationWebhookRouteRecord.provider) == IntegrationProviderType.TELNYX.value,
            col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
            col(IntegrationWebhookRouteRecord.purpose).in_(
                (
                    IntegrationWebhookPurpose.VOICE.value,
                    IntegrationWebhookPurpose.SMS.value,
                    IntegrationWebhookPurpose.STATUS.value,
                    IntegrationWebhookPurpose.MEDIA_STREAM.value,
                ),
            ),
        )
        routes = {
            IntegrationWebhookPurpose(record.purpose): record for record in (await self.session.exec(statement)).all()
        }
        return TwilioWebhookUrls(
            voice_url=self._validated_current_webhook_url(
                current=current,
                route=routes.get(IntegrationWebhookPurpose.VOICE),
                purpose=IntegrationWebhookPurpose.VOICE,
            ),
            sms_url=self._validated_current_webhook_url(
                current=current,
                route=routes.get(IntegrationWebhookPurpose.SMS),
                purpose=IntegrationWebhookPurpose.SMS,
            ),
            status_url=self._validated_current_webhook_url(
                current=current,
                route=routes.get(IntegrationWebhookPurpose.STATUS),
                purpose=IntegrationWebhookPurpose.STATUS,
            ),
            media_stream_url=self._validated_current_webhook_url(
                current=current,
                route=routes.get(IntegrationWebhookPurpose.MEDIA_STREAM),
                purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
            ),
        )

    async def _preflight_telnyx_outbound_call(
        self,
        *,
        resource: IntegrationResourceRecord,
        credentials: TelnyxCredentialBundle,
    ) -> TwilioWebhookUrls:
        outbound_voice_profile_id = metadata_string_value(
            resource.safe_metadata,
            "outbound_voice_profile_id",
        )
        if outbound_voice_profile_id is None:
            raise IntegrationCredentialError(TELNYX_CREDENTIAL_METADATA_ERROR)
        webhook_urls = await self._expected_telnyx_webhook_urls(resource)
        try:
            configuration = await self._telnyx_provider().inspect_phone_number_configuration(
                credentials=credentials,
                phone_number_id=resource.external_id,
                outbound_voice_profile_id=outbound_voice_profile_id,
                expected_voice_url=webhook_urls.voice_url,
                expected_sms_url=webhook_urls.sms_url,
            )
        except TelnyxProviderError as exc:
            raise IntegrationResourceError(TELNYX_OUTBOUND_VOICE_NOT_READY_ERROR) from exc
        outbound_voice_check = next(
            (check for check in telnyx_provider_readiness_checks(configuration) if check.name == "outbound_voice"),
            None,
        )
        if outbound_voice_check is None or outbound_voice_check.status != IntegrationReadinessStatus.READY:
            raise IntegrationResourceError(TELNYX_OUTBOUND_VOICE_NOT_READY_ERROR)
        return webhook_urls

    def _validated_current_webhook_url(
        self,
        *,
        current: Mapping[str, object],
        route: IntegrationWebhookRouteRecord | None,
        purpose: IntegrationWebhookPurpose,
    ) -> str:
        key_by_purpose = {
            IntegrationWebhookPurpose.VOICE: "voice_url",
            IntegrationWebhookPurpose.SMS: "sms_url",
            IntegrationWebhookPurpose.STATUS: "status_callback",
            IntegrationWebhookPurpose.MEDIA_STREAM: "media_stream_url",
        }
        key = key_by_purpose[purpose]
        stored_url = current.get(key)
        if not isinstance(stored_url, str) or route is None:
            raise IntegrationWebhookError(TELNYX_WEBHOOK_ROUTE_METADATA_ERROR)
        parsed = urlsplit(stored_url)
        prefix = (
            f"/api/integrations/streams/communications/{IntegrationProviderType.TELNYX.value}/media/"
            if purpose == IntegrationWebhookPurpose.MEDIA_STREAM
            else (f"/api/integrations/webhooks/communications/{IntegrationProviderType.TELNYX.value}/{purpose.value}/")
        )
        if not parsed.path.startswith(prefix):
            raise IntegrationWebhookError(TELNYX_WEBHOOK_ROUTE_METADATA_ERROR)
        route_key = parsed.path.removeprefix(prefix)
        if not route_key or "/" in route_key or route_key_hash(route_key) != route.route_key_hash:
            raise IntegrationWebhookError(TELNYX_WEBHOOK_ROUTE_METADATA_ERROR)
        public_base_url = require_public_base_url(self.public_base_url)
        if purpose == IntegrationWebhookPurpose.MEDIA_STREAM:
            return stream_url_from_public_base(
                public_base_url,
                IntegrationProviderType.TELNYX,
                route_key,
            )
        return f"{public_base_url}{parsed.path}"

    async def _telnyx_readiness_response(
        self,
        *,
        user_id: str,
        integration: UserIntegrationRecord,
        resource: IntegrationResourceRecord,
        configuration: TelnyxPhoneNumberConfiguration,
        repaired: bool,
    ) -> TelnyxReadinessResponse:
        checks = list(telnyx_provider_readiness_checks(configuration))
        checks.append(
            await self._agent_run_readiness_check(
                user_id=user_id,
                integration_id=integration.id,
                resource=resource,
            ),
        )
        checks.append(await self._livekit_readiness_check(user_id=user_id))
        return TelnyxReadinessResponse(
            integration_id=integration.id,
            resource_id=resource.id,
            checked_at=utc_now(),
            repaired=repaired,
            provider=telnyx_provider_readiness_response(
                configuration,
                phone_number=resource.display_name,
            ),
            checks=tuple(checks),
        )

    async def _agent_run_readiness_check(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource: IntegrationResourceRecord,
    ) -> IntegrationReadinessCheck:
        preference = (
            await self.session.exec(
                select(UserTextPreferenceRecord).where(
                    col(UserTextPreferenceRecord.user_id) == user_id,
                    col(UserTextPreferenceRecord.default_credential_id).is_not(None),
                ),
            )
        ).one_or_none()
        if preference is None:
            return readiness_check(
                name="agent_run_processing",
                ready=False,
                code="text_provider_not_configured",
                message="Configure a text provider before processing inbound SMS agent runs.",
            )
        agent = (
            await self.session.exec(
                select(UserAgentRecord).where(
                    col(UserAgentRecord.owner_user_id) == user_id,
                    col(UserAgentRecord.is_default).is_(True),
                ),
            )
        ).first()
        if agent is None:
            return readiness_check(
                name="agent_run_processing",
                ready=False,
                code="default_agent_missing",
                message="Create a default agent before processing inbound SMS.",
            )
        tool_name = provider_integration_tool_name(
            integration_id=integration_id,
            resource_id=resource.id,
            action=TWILIO_SEND_SMS_TOOL_NAME,
        )
        if self.authorization_service is None:
            return readiness_check(
                name="agent_run_processing",
                ready=None,
                code="tool_grant_not_checked",
                message="The SMS tool grant could not be checked in this process.",
            )
        decision = await self.authorization_service.authorize_tool(
            scope=agent_scope_for_tool(user_id=user_id, agent_id=agent.id),
            reference=ToolReference(
                tool_name=tool_name,
                tool_source=ToolGrantScope.INTEGRATION,
                integration_id=integration_id,
                risk=ToolRisk.HIGH,
            ),
        )
        if not decision.allowed:
            return readiness_check(
                name="agent_run_processing",
                ready=False,
                code="sms_tool_grant_denied",
                message="Allow the generated Telnyx send-SMS tool for the default agent.",
            )
        recent_run = (
            await self.session.exec(
                select(AgentRunRecord)
                .where(
                    col(AgentRunRecord.user_id) == user_id,
                    col(AgentRunRecord.idempotency_key).like(
                        f"inbound-sms:{IntegrationProviderType.TELNYX.value}:{integration_id}:{resource.id}:%",
                    ),
                )
                .order_by(col(AgentRunRecord.created_at).desc())
                .limit(1),
            )
        ).one_or_none()
        if recent_run is None:
            return readiness_check(
                name="agent_run_processing",
                ready=None,
                code="worker_liveness_unobserved",
                message="Configuration is ready; run-worker liveness has not yet been observed.",
            )
        if recent_run.status == AgentRunStatus.QUEUED.value and recent_run.created_at < utc_now() - timedelta(
            minutes=2
        ):
            return readiness_check(
                name="agent_run_processing",
                ready=False,
                code="agent_run_worker_not_processing",
                message="An inbound SMS run is still queued; start the durable agent run worker.",
            )
        if recent_run.status in {
            AgentRunStatus.FAILED.value,
            AgentRunStatus.LOST.value,
        }:
            return readiness_check(
                name="agent_run_processing",
                ready=False,
                code=f"agent_run_{recent_run.status}",
                message="The most recent inbound SMS agent run did not complete successfully.",
            )
        return readiness_check(
            name="agent_run_processing",
            ready=True,
            code="agent_run_worker_observed",
            message="The durable agent run path is configured and processing has been observed.",
        )

    async def _livekit_readiness_check(self, *, user_id: str) -> IntegrationReadinessCheck:
        if self.voice_call_preflight is None:
            return readiness_check(
                name="livekit_routing",
                ready=False,
                code=WORKER_ROUTING_UNAVAILABLE_CODE,
                message="LiveKit call routing is not configured in this API process.",
            )
        try:
            await self.voice_call_preflight(session=self.session, user_id=user_id)
        except Exception:
            return readiness_check(
                name="livekit_routing",
                ready=False,
                code="livekit_preflight_failed",
                message="LiveKit settings or voice-provider credentials are incomplete.",
            )
        return readiness_check(
            name="livekit_routing",
            ready=True,
            code="livekit_preflight_ready",
            message="LiveKit settings and voice-provider credentials passed preflight.",
        )

    async def _record_telnyx_readiness(
        self,
        *,
        integration: UserIntegrationRecord,
        resource: IntegrationResourceRecord,
        response: TelnyxReadinessResponse,
    ) -> None:
        now = response.checked_at
        integration.last_checked_at = now
        integration.updated_at = now
        resource.retained_metadata_json = {
            **resource.retained_metadata_json,
            "last_readiness": {
                "checked_at": now.isoformat(),
                "checks": {check.name: check.status.value for check in response.checks},
            },
        }
        resource.updated_at = now
        await self.session.commit()

    async def _agent_runs_by_message_id(
        self,
        *,
        user_id: str,
        records: list[IntegrationMessageRecord],
    ) -> dict[str, AgentRunRecord]:
        idempotency_keys = {
            (
                f"inbound-sms:{record.provider}:{record.integration_id}:"
                f"{record.resource_id}:{record.external_message_id}"
            ): record.external_message_id
            for record in records
            if record.direction == IntegrationDirection.INBOUND.value
        }
        if not idempotency_keys:
            return {}
        statement = select(AgentRunRecord).where(
            col(AgentRunRecord.user_id) == user_id,
            col(AgentRunRecord.idempotency_key).in_(tuple(idempotency_keys)),
        )
        runs = (await self.session.exec(statement)).all()
        return {idempotency_keys[run.idempotency_key]: run for run in runs if run.idempotency_key in idempotency_keys}

    async def _replies_by_message_id(
        self,
        *,
        user_id: str,
        records: list[IntegrationMessageRecord],
    ) -> dict[str, IntegrationReplyRecord]:
        message_ids = tuple(record.id for record in records)
        if not message_ids:
            return {}
        replies = (
            await self.session.exec(
                select(IntegrationReplyRecord).where(
                    col(IntegrationReplyRecord.user_id) == user_id,
                    or_(
                        col(IntegrationReplyRecord.inbound_message_id).in_(message_ids),
                        col(IntegrationReplyRecord.outbound_message_id).in_(message_ids),
                    ),
                ),
            )
        ).all()
        result: dict[str, IntegrationReplyRecord] = {}
        for reply in replies:
            result[reply.inbound_message_id] = reply
            if reply.outbound_message_id is not None:
                result[reply.outbound_message_id] = reply
        return result

    async def _agent_runs_by_reply_id(
        self,
        *,
        user_id: str,
        replies: tuple[IntegrationReplyRecord, ...],
    ) -> dict[str, AgentRunRecord]:
        run_ids = tuple(
            {reply.agent_run_id for reply in replies if reply.agent_run_id is not None},
        )
        if not run_ids:
            return {}
        runs = (
            await self.session.exec(
                select(AgentRunRecord).where(
                    col(AgentRunRecord.user_id) == user_id,
                    col(AgentRunRecord.id).in_(run_ids),
                ),
            )
        ).all()
        return {run.id: run for run in runs}

    async def _integration_record(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> UserIntegrationRecord:
        record = await self.session.get(UserIntegrationRecord, integration_id)
        if record is None or record.user_id != user_id or record.deleted_at is not None:
            raise IntegrationNotFoundError
        return record

    async def _ensure_label_available(self, *, user_id: str, label: str) -> None:
        statement = select(UserIntegrationRecord.id).where(
            col(UserIntegrationRecord.user_id) == user_id,
            col(UserIntegrationRecord.label) == label,
            col(UserIntegrationRecord.deleted_at).is_(None),
        )
        result = await self.session.exec(statement)
        if result.first() is not None:
            raise IntegrationCredentialError(INTEGRATION_LABEL_EXISTS_ERROR)

    async def _resource_record(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
    ) -> IntegrationResourceRecord:
        active_integration = await self.integration_is_connected(
            user_id=user_id,
            integration_id=integration_id,
        )
        if not active_integration:
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        record = await self.session.get(IntegrationResourceRecord, resource_id)
        if (
            record is None
            or record.user_id != user_id
            or record.integration_id != integration_id
            or record.deleted_at is not None
            or record.status != IntegrationResourceStatus.CONNECTED.value
        ):
            raise IntegrationResourceError(INTEGRATION_RESOURCE_NOT_FOUND_ERROR)
        return record

    async def integration_is_connected(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> bool:
        record = await self.session.get(UserIntegrationRecord, integration_id)
        return bool(
            record is not None
            and record.user_id == user_id
            and record.deleted_at is None
            and record.enabled
            and record.status == IntegrationStatus.CONNECTED.value,
        )

    async def _resource_records(
        self,
        *,
        user_id: str,
        integration_id: str,
    ) -> tuple[IntegrationResourceRecord, ...]:
        statement = select(IntegrationResourceRecord).where(
            col(IntegrationResourceRecord.user_id) == user_id,
            col(IntegrationResourceRecord.integration_id) == integration_id,
            col(IntegrationResourceRecord.deleted_at).is_(None),
        )
        result = await self.session.exec(statement)
        return tuple(result.all())

    async def _upsert_credential(
        self,
        *,
        user_id: str,
        integration_id: str,
        kind: IntegrationCredentialKind,
        public_identifier: str,
        secret: SecretStr,
        provider: IntegrationProviderType = IntegrationProviderType.TWILIO,
    ) -> IntegrationCredentialRecord:
        statement = select(IntegrationCredentialRecord).where(
            col(IntegrationCredentialRecord.user_id) == user_id,
            col(IntegrationCredentialRecord.integration_id) == integration_id,
            col(IntegrationCredentialRecord.secret_kind) == kind.value,
            col(IntegrationCredentialRecord.revoked_at).is_(None),
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            record = IntegrationCredentialRecord(
                user_id=user_id,
                integration_id=integration_id,
                provider=provider.value,
                secret_kind=kind.value,
                public_identifier=public_identifier,
            )
            self.session.add(record)
            await self.session.flush()
        record.provider = provider.value
        record.status = IntegrationCredentialStatus.ACTIVE.value
        record.public_identifier = public_identifier
        record.last_four = last_four(secret.get_secret_value())
        record.updated_at = utc_now()
        await self.secret_vault.seal_secret(
            session=self.session,
            secret=secret,
            reference=integration_secret_reference(
                user_id=user_id,
                provider=provider,
                credential_id=record.id,
            ),
        )
        return record

    async def _load_secret(
        self,
        *,
        user_id: str,
        integration_id: str,
        kind: IntegrationCredentialKind,
    ) -> SecretStr:
        statement = select(IntegrationCredentialRecord).where(
            col(IntegrationCredentialRecord.user_id) == user_id,
            col(IntegrationCredentialRecord.integration_id) == integration_id,
            col(IntegrationCredentialRecord.secret_kind) == kind.value,
            col(IntegrationCredentialRecord.status) == IntegrationCredentialStatus.ACTIVE.value,
            col(IntegrationCredentialRecord.revoked_at).is_(None),
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            raise IntegrationCredentialError(INTEGRATION_CREDENTIAL_METADATA_MISSING_ERROR)
        secret = await self.secret_vault.unseal_secret(
            session=self.session,
            reference=integration_secret_reference(
                user_id=user_id,
                provider=IntegrationProviderType(record.provider),
                credential_id=record.id,
            ),
        )
        if secret is None:
            raise IntegrationCredentialError(INTEGRATION_CREDENTIAL_VALUE_MISSING_ERROR)
        return secret

    async def _upsert_telnyx_phone_number_resource(
        self,
        *,
        user_id: str,
        integration_id: str,
        request: TelnyxPhoneSetupRequest,
    ) -> IntegrationResourceRecord:
        statement = select(IntegrationResourceRecord).where(
            col(IntegrationResourceRecord.user_id) == user_id,
            col(IntegrationResourceRecord.provider) == IntegrationProviderType.TELNYX.value,
            col(IntegrationResourceRecord.resource_type) == IntegrationResourceType.PHONE_NUMBER.value,
            col(IntegrationResourceRecord.external_id) == request.phone_number,
            col(IntegrationResourceRecord.deleted_at).is_(None),
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            record = IntegrationResourceRecord(
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.TELNYX.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id=request.phone_number,
                display_name=request.phone_number,
            )
            self.session.add(record)
            await self.session.flush()
        record.integration_id = integration_id
        record.status = IntegrationResourceStatus.CONNECTED.value
        record.capabilities = [
            IntegrationCapability.INBOUND_VOICE.value,
            IntegrationCapability.OUTBOUND_VOICE.value,
            IntegrationCapability.SMS_READ.value,
            IntegrationCapability.SMS_SEND.value,
            IntegrationCapability.MEDIA_STREAM.value,
            IntegrationCapability.TOOL_ACTIONS.value,
        ]
        record.safe_metadata = {
            "phone_number": request.phone_number,
            "transcript_storage_enabled": request.transcript_storage_enabled,
        }
        record.updated_at = utc_now()
        return record

    async def _upsert_twilio_phone_number_resource(
        self,
        *,
        user_id: str,
        integration_id: str,
        candidate: TwilioPhoneNumberCandidate,
        capabilities: tuple[IntegrationCapability, ...],
        transcript_storage_enabled: bool,
    ) -> IntegrationResourceRecord:
        statement = select(IntegrationResourceRecord).where(
            col(IntegrationResourceRecord.user_id) == user_id,
            col(IntegrationResourceRecord.provider) == IntegrationProviderType.TWILIO.value,
            col(IntegrationResourceRecord.resource_type) == IntegrationResourceType.PHONE_NUMBER.value,
            col(IntegrationResourceRecord.external_id) == candidate.sid,
            col(IntegrationResourceRecord.deleted_at).is_(None),
        )
        result = await self.session.exec(statement)
        record = result.one_or_none()
        if record is None:
            record = IntegrationResourceRecord(
                user_id=user_id,
                integration_id=integration_id,
                provider=IntegrationProviderType.TWILIO.value,
                resource_type=IntegrationResourceType.PHONE_NUMBER.value,
                external_id=candidate.sid,
                display_name=candidate.phone_number,
            )
            self.session.add(record)
            await self.session.flush()
        record.integration_id = integration_id
        record.status = IntegrationResourceStatus.CONNECTED.value
        record.capabilities = [capability.value for capability in capabilities]
        record.safe_metadata = {
            "friendly_name": candidate.friendly_name,
            "phone_number": candidate.phone_number,
            "transcript_storage_enabled": transcript_storage_enabled,
        }
        record.updated_at = utc_now()
        return record

    def _create_webhook_routes(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
    ) -> CreatedWebhookRoutes:
        public_base_url = require_public_base_url(self.public_base_url)
        voice_key = new_route_key()
        sms_key = new_route_key()
        status_key = new_route_key()
        stream_key = new_route_key()
        voice_route = self._webhook_route(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            purpose=IntegrationWebhookPurpose.VOICE,
            route_key=voice_key,
        )
        sms_route = self._webhook_route(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            purpose=IntegrationWebhookPurpose.SMS,
            route_key=sms_key,
        )
        status_route = self._webhook_route(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            purpose=IntegrationWebhookPurpose.STATUS,
            route_key=status_key,
        )
        media_stream_route = self._webhook_route(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
            route_key=stream_key,
        )
        urls = TwilioWebhookUrls(
            voice_url=communication_webhook_url(
                public_base_url=public_base_url,
                provider=provider,
                purpose=IntegrationWebhookPurpose.VOICE,
                route_key=voice_key,
            ),
            sms_url=communication_webhook_url(
                public_base_url=public_base_url,
                provider=provider,
                purpose=IntegrationWebhookPurpose.SMS,
                route_key=sms_key,
            ),
            status_url=communication_webhook_url(
                public_base_url=public_base_url,
                provider=provider,
                purpose=IntegrationWebhookPurpose.STATUS,
                route_key=status_key,
            ),
            media_stream_url=stream_url_from_public_base(
                public_base_url=public_base_url,
                provider=provider,
                route_key=stream_key,
            ),
        )
        return CreatedWebhookRoutes(
            voice_route=voice_route,
            sms_route=sms_route,
            status_route=status_route,
            media_stream_route=media_stream_route,
            urls=urls,
        )

    def _webhook_route(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        purpose: IntegrationWebhookPurpose,
        route_key: str,
    ) -> IntegrationWebhookRouteRecord:
        record = IntegrationWebhookRouteRecord(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider.value,
            purpose=purpose.value,
            route_key_hash=route_key_hash(route_key),
            status=IntegrationRouteStatus.ACTIVE.value,
        )
        self.session.add(record)
        return record

    async def _disable_routes(self, *, user_id: str, integration_id: str) -> None:
        statement = select(IntegrationWebhookRouteRecord).where(
            col(IntegrationWebhookRouteRecord.user_id) == user_id,
            col(IntegrationWebhookRouteRecord.integration_id) == integration_id,
            col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
        )
        result = await self.session.exec(statement)
        for route in result.all():
            route.status = IntegrationRouteStatus.DISABLED.value
            route.disabled_at = utc_now()

    async def _disable_routes_for_resource(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
    ) -> None:
        statement = select(IntegrationWebhookRouteRecord).where(
            col(IntegrationWebhookRouteRecord.user_id) == user_id,
            col(IntegrationWebhookRouteRecord.integration_id) == integration_id,
            col(IntegrationWebhookRouteRecord.resource_id) == resource_id,
            col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
        )
        result = await self.session.exec(statement)
        for route in result.all():
            route.status = IntegrationRouteStatus.DISABLED.value
            route.disabled_at = utc_now()

    async def _revoke_credentials(self, *, user_id: str, integration_id: str) -> None:
        statement = select(IntegrationCredentialRecord).where(
            col(IntegrationCredentialRecord.user_id) == user_id,
            col(IntegrationCredentialRecord.integration_id) == integration_id,
            col(IntegrationCredentialRecord.status) == IntegrationCredentialStatus.ACTIVE.value,
            col(IntegrationCredentialRecord.revoked_at).is_(None),
        )
        result = await self.session.exec(statement)
        revoked_at = utc_now()
        for credential in result.all():
            await self.secret_vault.delete_secret(
                session=self.session,
                reference=integration_secret_reference(
                    user_id=user_id,
                    provider=IntegrationProviderType(credential.provider),
                    credential_id=credential.id,
                ),
            )
            credential.status = IntegrationCredentialStatus.REVOKED.value
            credential.revoked_at = revoked_at
            credential.updated_at = revoked_at

    async def _disconnect_local_integration(
        self,
        *,
        user_id: str,
        integration: UserIntegrationRecord,
        provider: IntegrationProviderType,
    ) -> None:
        resources = await self._resource_records(
            user_id=user_id,
            integration_id=integration.id,
        )
        deleted_at = utc_now()
        for resource in resources:
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.deleted_at = deleted_at
            resource.updated_at = deleted_at
        await self._disable_routes(user_id=user_id, integration_id=integration.id)
        await self._revoke_credentials(user_id=user_id, integration_id=integration.id)
        integration.enabled = False
        integration.status = IntegrationStatus.REVOKED.value
        integration.status_message = None
        integration.deleted_at = deleted_at
        integration.updated_at = deleted_at
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=provider,
            action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={"provider": integration.provider},
        )
        await self.session.commit()

    async def _disconnect_telnyx_integration(
        self,
        *,
        user_id: str,
        integration: UserIntegrationRecord,
    ) -> None:
        credentials = await self.load_telnyx_credentials(
            user_id=user_id,
            integration_id=integration.id,
        )
        provider = self._telnyx_provider()
        resources = await self._resource_records(
            user_id=user_id,
            integration_id=integration.id,
        )
        cleanup_failed = False
        deleted_at = utc_now()
        for resource in resources:
            try:
                await provider.release_phone_number(
                    credentials=credentials,
                    cleanup=telnyx_cleanup_from_resource(resource),
                )
            except TelnyxProviderError as exc:
                resource.status = IntegrationResourceStatus.FAILED_DISCONNECT_CLEANUP.value
                resource.retained_metadata_json = {
                    **resource.retained_metadata_json,
                    "disconnect_cleanup_error": str(exc),
                }
                resource.updated_at = utc_now()
                cleanup_failed = True
                continue
            resource.status = IntegrationResourceStatus.DISABLED.value
            resource.deleted_at = deleted_at
            resource.updated_at = deleted_at
            await self._disable_routes_for_resource(
                user_id=user_id,
                integration_id=integration.id,
                resource_id=resource.id,
            )
        if cleanup_failed:
            integration.enabled = False
            integration.status = IntegrationStatus.FAILED.value
            integration.status_message = TELNYX_DISCONNECT_CLEANUP_FAILED_ERROR
            integration.updated_at = utc_now()
            await self._audit(
                user_id=user_id,
                integration_id=integration.id,
                provider=IntegrationProviderType.TELNYX,
                action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
                actor_type=IntegrationAuditActorType.USER,
                actor_id=user_id,
                outcome=IntegrationAuditOutcome.FAILED,
                safe_metadata={},
            )
            await self.session.commit()
            return
        await self._disable_routes(user_id=user_id, integration_id=integration.id)
        await self._revoke_credentials(user_id=user_id, integration_id=integration.id)
        integration.enabled = False
        integration.status = IntegrationStatus.REVOKED.value
        integration.status_message = None
        integration.deleted_at = deleted_at
        integration.updated_at = deleted_at
        await self._audit(
            user_id=user_id,
            integration_id=integration.id,
            provider=IntegrationProviderType.TELNYX,
            action=IntegrationAuditAction.INTEGRATION_DISCONNECTED,
            actor_type=IntegrationAuditActorType.USER,
            actor_id=user_id,
            outcome=IntegrationAuditOutcome.SUCCEEDED,
            safe_metadata={},
        )
        await self.session.commit()

    async def preapprove_resource_tools(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource: IntegrationResourceRecord,
    ) -> None:
        if self.authorization_service is None:
            return
        route_context = await AgentIdentityService(
            session_factory=self.session_factory,
        ).get_or_create_default_agent(user_id)
        actions = tuple(
            action
            for action in (
                TWILIO_SEND_SMS_TOOL_NAME,
                TWILIO_PLACE_CALL_TOOL_NAME,
                WHATSAPP_SEND_MESSAGE_TOOL_NAME,
            )
            if resource_supports_action(resource=resource, action=action)
        )
        if not actions:
            return
        await self.authorization_service.set_grants(
            user_id=user_id,
            agent_id=route_context.id,
            grants=tuple(
                ToolGrantUpdate(
                    tool_name=provider_integration_tool_name(
                        integration_id=integration_id,
                        resource_id=resource.id,
                        action=action,
                    ),
                    tool_source=ToolGrantScope.INTEGRATION,
                    integration_id=integration_id,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.HIGH,
                )
                for action in actions
            ),
        )
        if not {
            TWILIO_PLACE_CALL_TOOL_NAME,
            WHATSAPP_SEND_MESSAGE_TOOL_NAME,
        }.intersection(actions):
            return
        contact_search_decision = await self.authorization_service.get_explicit_grant(
            user_id=user_id,
            agent_id=route_context.id,
            reference=ToolReference(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                risk=ToolRisk.LOW,
            ),
        )
        if contact_search_decision is not None:
            return
        await self.authorization_service.set_grants(
            user_id=user_id,
            agent_id=route_context.id,
            grants=(
                ToolGrantUpdate(
                    tool_name="search_contacts",
                    tool_source=ToolGrantScope.INTERNAL,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.LOW,
                ),
            ),
            audit_reason="outbound_contact_search_prerequisite",
        )

    async def _event_by_idempotency_key(
        self,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        idempotency_key: str,
    ) -> IntegrationEventRecord | None:
        statement = select(IntegrationEventRecord).where(
            col(IntegrationEventRecord.user_id) == user_id,
            col(IntegrationEventRecord.provider) == provider.value,
            col(IntegrationEventRecord.idempotency_key) == idempotency_key,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def _message_by_external_id(
        self,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        message_sid: str,
    ) -> IntegrationMessageRecord | None:
        statement = select(IntegrationMessageRecord).where(
            col(IntegrationMessageRecord.user_id) == user_id,
            col(IntegrationMessageRecord.provider) == provider.value,
            col(IntegrationMessageRecord.external_message_id) == message_sid,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def _call_session_by_external_id(
        self,
        *,
        user_id: str,
        provider: IntegrationProviderType,
        call_sid: str,
    ) -> IntegrationCallSessionRecord | None:
        statement = select(IntegrationCallSessionRecord).where(
            col(IntegrationCallSessionRecord.user_id) == user_id,
            col(IntegrationCallSessionRecord.provider) == provider.value,
            col(IntegrationCallSessionRecord.external_call_id) == call_sid,
        )
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def _insert_event_ignore(
        self,
        values: dict[str, object | None],
    ) -> bool:
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            result = await self.session.exec(
                postgresql_insert(IntegrationEventRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_integration_events_idempotency",
                ),
            )
            return row_was_inserted(result)
        if dialect_name == "sqlite":
            result = await self.session.exec(
                sqlite_insert(IntegrationEventRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=("user_id", "provider", "idempotency_key"),
                ),
            )
            return row_was_inserted(result)
        return raise_unsupported_idempotent_insert(dialect_name)

    async def _insert_message_ignore(
        self,
        values: dict[str, object | None],
    ) -> None:
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            await self.session.exec(
                postgresql_insert(IntegrationMessageRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_integration_messages_external",
                ),
            )
            return
        if dialect_name == "sqlite":
            await self.session.exec(
                sqlite_insert(IntegrationMessageRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=("user_id", "provider", "external_message_id"),
                ),
            )
            return
        raise_unsupported_idempotent_insert(dialect_name)

    async def _insert_call_session_ignore(
        self,
        values: dict[str, object | None],
    ) -> None:
        dialect_name = self.session.get_bind().dialect.name
        if dialect_name == "postgresql":
            await self.session.exec(
                postgresql_insert(IntegrationCallSessionRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_integration_call_sessions_external",
                ),
            )
            return
        if dialect_name == "sqlite":
            await self.session.exec(
                sqlite_insert(IntegrationCallSessionRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    index_elements=("user_id", "provider", "external_call_id"),
                ),
            )
            return
        raise_unsupported_idempotent_insert(dialect_name)

    async def _integration_event(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str | None,
        provider: IntegrationProviderType,
        event_type: str,
        direction: IntegrationDirection,
        external_event_id: str | None,
        idempotency_key: str,
        payload_json: dict[str, object],
        safe_summary_json: dict[str, object],
        status: IntegrationEventStatus = IntegrationEventStatus.ACCEPTED,
    ) -> IntegrationEventRecord:
        record, _created = await self._claim_integration_event(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            event_type=event_type,
            direction=direction,
            external_event_id=external_event_id,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            safe_summary_json=safe_summary_json,
            status=status,
        )
        return record

    async def _claim_integration_event(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str | None,
        provider: IntegrationProviderType,
        event_type: str,
        direction: IntegrationDirection,
        external_event_id: str | None,
        idempotency_key: str,
        payload_json: dict[str, object],
        safe_summary_json: dict[str, object],
        status: IntegrationEventStatus = IntegrationEventStatus.ACCEPTED,
        retry_failed: bool = False,
        retry_processing_after_seconds: float | None = None,
    ) -> tuple[IntegrationEventRecord, bool]:
        existing = await self._event_by_idempotency_key(
            user_id=user_id,
            provider=provider,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            now = utc_now()
            if retry_failed and existing.status == IntegrationEventStatus.FAILED.value:
                retry_statement = (
                    update(IntegrationEventRecord)
                    .where(
                        col(IntegrationEventRecord.id) == existing.id,
                        col(IntegrationEventRecord.user_id) == user_id,
                        col(IntegrationEventRecord.status) == IntegrationEventStatus.FAILED.value,
                    )
                    .values(
                        status=status.value,
                        error_type=None,
                        error_message=None,
                        received_at=now,
                        processed_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                retry_result = await self.session.exec(retry_statement)
                if retry_result.rowcount == 1:
                    await self.session.flush()
                    await self.session.refresh(existing)
                    return existing, True
            if (
                retry_processing_after_seconds is not None
                and existing.status == IntegrationEventStatus.PROCESSING.value
            ):
                processing_cutoff = now - timedelta(
                    seconds=retry_processing_after_seconds,
                )
                retry_statement = (
                    update(IntegrationEventRecord)
                    .where(
                        col(IntegrationEventRecord.id) == existing.id,
                        col(IntegrationEventRecord.user_id) == user_id,
                        col(IntegrationEventRecord.status) == IntegrationEventStatus.PROCESSING.value,
                        col(IntegrationEventRecord.received_at) <= processing_cutoff,
                    )
                    .values(
                        status=status.value,
                        error_type=None,
                        error_message=None,
                        received_at=now,
                        processed_at=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                retry_result = await self.session.exec(retry_statement)
                if retry_result.rowcount == 1:
                    await self.session.flush()
                    await self.session.refresh(existing)
                    return existing, True
            return existing, False
        now = utc_now()
        inserted = await self._insert_event_ignore(
            {
                "id": new_record_id(),
                "user_id": user_id,
                "integration_id": integration_id,
                "resource_id": resource_id,
                "provider": provider.value,
                "event_type": event_type,
                "direction": direction.value,
                "external_event_id": external_event_id,
                "idempotency_key": idempotency_key,
                "status": status.value,
                "payload_json": payload_json,
                "safe_summary_json": safe_summary_json,
                "error_type": None,
                "error_message": None,
                "received_at": now,
                "processed_at": None if status == IntegrationEventStatus.PROCESSING else now,
                "created_at": now,
            },
        )
        record = await self._event_by_idempotency_key(
            user_id=user_id,
            provider=provider,
            idempotency_key=idempotency_key,
        )
        if record is None:
            raise IntegrationResourceError(TWILIO_EVENT_PERSISTENCE_ERROR)
        return record, inserted

    async def claim_communication_event(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str | None,
        provider: IntegrationProviderType,
        event_type: str,
        direction: IntegrationDirection,
        external_event_id: str | None,
        idempotency_key: str,
        payload_json: dict[str, object],
        safe_summary_json: dict[str, object],
        status: IntegrationEventStatus = IntegrationEventStatus.ACCEPTED,
        retry_failed: bool = False,
        retry_processing_after_seconds: float | None = None,
    ) -> tuple[IntegrationEventRecord, bool]:
        return await self._claim_integration_event(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            event_type=event_type,
            direction=direction,
            external_event_id=external_event_id,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            safe_summary_json=safe_summary_json,
            status=status,
            retry_failed=retry_failed,
            retry_processing_after_seconds=retry_processing_after_seconds,
        )

    async def record_communication_event(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str | None,
        provider: IntegrationProviderType,
        event_type: str,
        direction: IntegrationDirection,
        external_event_id: str | None,
        idempotency_key: str,
        payload_json: dict[str, object],
        safe_summary_json: dict[str, object],
        status: IntegrationEventStatus = IntegrationEventStatus.ACCEPTED,
    ) -> IntegrationEventRecord:
        return await self._integration_event(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            event_type=event_type,
            direction=direction,
            external_event_id=external_event_id,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            safe_summary_json=safe_summary_json,
            status=status,
        )

    async def record_twilio_event(
        self,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str | None,
        event_type: str,
        direction: IntegrationDirection,
        external_event_id: str | None,
        idempotency_key: str,
        payload_json: dict[str, object],
        safe_summary_json: dict[str, object],
        status: IntegrationEventStatus = IntegrationEventStatus.ACCEPTED,
    ) -> IntegrationEventRecord:
        return await self.record_communication_event(
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.TWILIO,
            event_type=event_type,
            direction=direction,
            external_event_id=external_event_id,
            idempotency_key=idempotency_key,
            payload_json=payload_json,
            safe_summary_json=safe_summary_json,
            status=status,
        )

    async def record_audit(
        self,
        *,
        user_id: str,
        integration_id: str | None,
        provider: IntegrationProviderType = IntegrationProviderType.TWILIO,
        action: IntegrationAuditAction,
        actor_type: IntegrationAuditActorType,
        actor_id: str | None,
        outcome: IntegrationAuditOutcome,
        safe_metadata: dict[str, object],
    ) -> None:
        await self._audit(
            user_id=user_id,
            integration_id=integration_id,
            provider=provider,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            outcome=outcome,
            safe_metadata=safe_metadata,
        )

    async def _audit(
        self,
        *,
        user_id: str,
        integration_id: str | None,
        provider: IntegrationProviderType = IntegrationProviderType.TWILIO,
        action: IntegrationAuditAction,
        actor_type: IntegrationAuditActorType,
        actor_id: str | None,
        outcome: IntegrationAuditOutcome,
        safe_metadata: dict[str, object],
    ) -> None:
        self.session.add(
            IntegrationAuditLogRecord(
                user_id=user_id,
                integration_id=integration_id,
                provider=provider.value,
                action=action.value,
                actor_type=actor_type.value,
                actor_id=actor_id,
                outcome=outcome.value,
                safe_metadata=safe_metadata,
            ),
        )

    async def _authorize_integration_tool(
        self,
        *,
        user_id: str,
        agent_id: str,
        integration_id: str,
        provider: IntegrationProviderType = IntegrationProviderType.TWILIO,
        tool_name: str,
    ) -> None:
        if self.authorization_service is None:
            return
        decision = await self.authorization_service.authorize_tool(
            scope=agent_scope_for_tool(user_id=user_id, agent_id=agent_id),
            reference=ToolReference(
                tool_name=tool_name,
                tool_source=ToolGrantScope.INTEGRATION,
                integration_id=integration_id,
                risk=ToolRisk.HIGH,
            ),
        )
        await self._audit(
            user_id=user_id,
            integration_id=integration_id,
            provider=provider,
            action=(IntegrationAuditAction.TOOL_ALLOWED if decision.allowed else IntegrationAuditAction.TOOL_DENIED),
            actor_type=IntegrationAuditActorType.AGENT,
            actor_id=agent_id,
            outcome=(IntegrationAuditOutcome.SUCCEEDED if decision.allowed else IntegrationAuditOutcome.DENIED),
            safe_metadata={"tool_name": tool_name, "reason": decision.reason},
        )
        if not decision.allowed:
            raise IntegrationToolPermissionError(decision.reason)

    def _twilio_provider(self) -> TwilioProviderCapabilities:
        return self.provider_registry.twilio_provider_for()

    def _telnyx_provider(self) -> TelnyxProviderCapabilities:
        return self.provider_registry.telnyx_provider_for()


@dataclass(frozen=True, slots=True)
class IntegrationWebhookRouteLookupService:
    session: AsyncSession

    async def resolve(
        self,
        *,
        provider: IntegrationProviderType,
        purpose: IntegrationWebhookPurpose,
        route_key: str,
    ) -> IntegrationWebhookRouteContext:
        hashed_key = route_key_hash(route_key)
        statement = select(IntegrationWebhookRouteRecord).where(
            col(IntegrationWebhookRouteRecord.provider) == provider.value,
            col(IntegrationWebhookRouteRecord.purpose) == purpose.value,
            col(IntegrationWebhookRouteRecord.route_key_hash) == hashed_key,
            col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
        )
        result = await self.session.exec(statement)
        route = result.one_or_none()
        if route is None:
            raise IntegrationWebhookError(WEBHOOK_ROUTE_NOT_FOUND_ERROR)
        return IntegrationWebhookRouteContext(route=route, route_key_hash=hashed_key)


def integration_response(record: UserIntegrationRecord) -> IntegrationResponse:
    provider = IntegrationProviderType(record.provider)
    return IntegrationResponse(
        id=record.id,
        provider=provider,
        label=record.label,
        status=IntegrationStatus(record.status),
        enabled=record.enabled,
        auth_type=IntegrationAuthType(record.auth_type),
        setup_state=record.setup_state,
        default_agent_id=record.default_agent_id,
        safe_metadata=record.safe_metadata,
        logo=logo_for_integration_provider(provider.value),
        last_checked_at=record.last_checked_at,
        status_message=record.status_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def credential_summary(record: IntegrationCredentialRecord) -> IntegrationCredentialSummary:
    return IntegrationCredentialSummary(
        id=record.id,
        secret_kind=IntegrationCredentialKind(record.secret_kind),
        status=IntegrationCredentialStatus(record.status),
        public_identifier=record.public_identifier,
        last_four=record.last_four,
        created_at=record.created_at,
        updated_at=record.updated_at,
        revoked_at=record.revoked_at,
    )


def resource_response(record: IntegrationResourceRecord) -> IntegrationResourceResponse:
    return IntegrationResourceResponse(
        id=record.id,
        integration_id=record.integration_id,
        provider=IntegrationProviderType(record.provider),
        resource_type=IntegrationResourceType(record.resource_type),
        external_id=record.external_id,
        display_name=record.display_name,
        status=IntegrationResourceStatus(record.status),
        capabilities=tuple(IntegrationCapability(capability) for capability in record.capabilities),
        safe_metadata=record.safe_metadata,
        retained_metadata_json=redacted_retained_metadata(record.retained_metadata_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def readiness_check(
    *,
    name: str,
    ready: bool | None,
    code: str,
    message: str,
) -> IntegrationReadinessCheck:
    status = (
        IntegrationReadinessStatus.UNKNOWN
        if ready is None
        else IntegrationReadinessStatus.READY
        if ready
        else IntegrationReadinessStatus.NOT_READY
    )
    return IntegrationReadinessCheck(
        name=name,
        status=status,
        code=code,
        message=message,
    )


def telnyx_provider_readiness_checks(
    configuration: TelnyxPhoneNumberConfiguration,
) -> tuple[IntegrationReadinessCheck, ...]:
    capabilities = set(configuration.capabilities)
    inbound_sms_ready = (
        IntegrationCapability.SMS_READ in capabilities
        and configuration.messaging_assignment_matches
        and configuration.messaging_profile_found
        and configuration.messaging_profile_enabled is True
        and configuration.messaging_webhook_matches
        and configuration.messaging_webhook_api_version == "2"
    )
    outbound_sms_ready = (
        IntegrationCapability.SMS_SEND in capabilities
        and configuration.messaging_assignment_matches
        and configuration.messaging_profile_found
        and configuration.messaging_profile_enabled is True
        and bool(configuration.messaging_destinations)
    )
    inbound_voice_ready = (
        IntegrationCapability.INBOUND_VOICE in capabilities
        and configuration.connection_assignment_matches
        and configuration.voice_application_found
        and configuration.voice_application_active is True
        and configuration.voice_webhook_matches
        and configuration.voice_webhook_api_version == "2"
    )
    outbound_voice_ready = (
        IntegrationCapability.OUTBOUND_VOICE in capabilities
        and configuration.connection_assignment_matches
        and configuration.voice_application_found
        and configuration.voice_application_active is True
        and configuration.outbound_voice_profile_found
        and configuration.outbound_voice_profile_enabled is True
        and configuration.outbound_voice_profile_assignment_matches
        and bool(configuration.voice_destinations)
    )
    return (
        readiness_check(
            name="inbound_sms",
            ready=inbound_sms_ready,
            code=("inbound_sms_ready" if inbound_sms_ready else "inbound_sms_routing_incomplete"),
            message=(
                "The number is assigned to an enabled API v2 messaging profile with the Voxos SMS webhook."
                if inbound_sms_ready
                else "Check SMS capability, number assignment, profile enabled state, API version, and webhook routing."
            ),
        ),
        readiness_check(
            name="outbound_sms",
            ready=outbound_sms_ready,
            code=("outbound_sms_ready" if outbound_sms_ready else "outbound_sms_routing_incomplete"),
            message=(
                "The number and messaging profile are ready for allowed outbound destinations."
                if outbound_sms_ready
                else "Check SMS capability, messaging assignment, profile enabled state, and destination allowlist."
            ),
        ),
        readiness_check(
            name="inbound_voice",
            ready=inbound_voice_ready,
            code=("inbound_voice_ready" if inbound_voice_ready else "inbound_voice_routing_incomplete"),
            message=(
                "The number is assigned to an active API v2 Voice application with the Voxos webhook."
                if inbound_voice_ready
                else (
                    "Check voice capability, number connection, Voice application state, "
                    "API version, and webhook routing."
                )
            ),
        ),
        readiness_check(
            name="outbound_voice",
            ready=outbound_voice_ready,
            code=("outbound_voice_ready" if outbound_voice_ready else "outbound_voice_routing_incomplete"),
            message=(
                "The Voice application and enabled outbound profile allow configured destinations."
                if outbound_voice_ready
                else (
                    "Check voice capability, Voice application assignment, outbound profile "
                    "state, and destination allowlist."
                )
            ),
        ),
    )


def telnyx_provider_readiness_response(
    configuration: TelnyxPhoneNumberConfiguration,
    *,
    phone_number: str,
) -> TelnyxProviderReadiness:
    return TelnyxProviderReadiness(
        **configuration.model_dump(),
        phone_number=phone_number,
    )


def message_activity_response(
    record: IntegrationMessageRecord,
    *,
    run: AgentRunRecord | None,
    reply: IntegrationReplyRecord | None,
) -> IntegrationMessageActivity:
    direction = IntegrationDirection(record.direction)
    remote_party = remote_party_phone_number(
        direction=direction,
        from_number=record.from_number,
        to_number=record.to_number,
    )
    failure_code, failure_title = message_failure_details(record.payload_json)
    if failure_code is None and reply is not None and reply.error_code is not None:
        failure_code = reply.error_code
        failure_title = "Reply delivery requires attention"
    return IntegrationMessageActivity(
        id=record.id,
        integration_id=record.integration_id,
        resource_id=record.resource_id,
        provider=IntegrationProviderType(record.provider),
        direction=direction,
        remote_party=remote_party,
        status=IntegrationMessageStatus(record.status),
        external_message_id=record.external_message_id,
        agent_run_id=None if run is None else run.id,
        agent_run_status=None if run is None else run.status,
        conversation_id=None if reply is None else reply.conversation_id,
        reply_id=None if reply is None else reply.id,
        reply_status=(None if reply is None else IntegrationReplyStatus(reply.status)),
        response_profile=None if reply is None else reply.response_profile,
        sms_encoding=None if reply is None else reply.encoding,
        estimated_segments=None if reply is None else reply.estimated_segments,
        condensation_attempted=False if reply is None else reply.condensation_attempted,
        overflow_fallback_used=False if reply is None else reply.overflow_fallback_used,
        failure_code=failure_code,
        failure_title=failure_title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def call_activity_response(record: IntegrationCallSessionRecord) -> IntegrationCallActivity:
    direction = IntegrationDirection(record.direction)
    remote_party = remote_party_phone_number(
        direction=direction,
        from_number=record.from_number,
        to_number=record.to_number,
    )
    return IntegrationCallActivity(
        id=record.id,
        integration_id=record.integration_id,
        resource_id=record.resource_id,
        provider=IntegrationProviderType(record.provider),
        direction=direction,
        remote_party=remote_party,
        status=IntegrationCallStatus(record.status),
        external_call_id=record.external_call_id,
        contact_id=record.contact_id,
        contact_display_name=record.contact_display_name,
        agent_id=record.agent_id,
        failure_code=metadata_string_value(record.payload_json, "failure_code"),
        started_at=record.started_at,
        answered_at=record.answered_at,
        ended_at=record.ended_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def message_failure_details(
    payload_json: Mapping[str, object],
) -> tuple[str | None, str | None]:
    latest_status = payload_json.get("latest_status")
    if not isinstance(latest_status, dict):
        return None, None
    errors = latest_status.get("errors")
    if not isinstance(errors, list) or not errors:
        return None, None
    first = errors[0]
    if not isinstance(first, dict):
        return None, None
    code = first.get("code")
    title = first.get("title")
    safe_code = str(code)[:128] if isinstance(code, (str, int)) else None
    safe_title = title.strip()[:256] if isinstance(title, str) and title.strip() else None
    return safe_code, safe_title


def redacted_retained_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {key: redacted_metadata_value(key=key, value=value) for key, value in metadata.items()}


def redacted_metadata_value(*, key: str, value: object) -> object:
    if key in WEBHOOK_URL_METADATA_KEYS and isinstance(value, str) and value.strip() != "":
        return REDACTED_WEBHOOK_VALUE
    if isinstance(value, dict):
        return {
            child_key: redacted_metadata_value(key=str(child_key), value=child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [redacted_metadata_value(key=key, value=child_value) for child_value in value]
    return value


def integration_secret_reference(
    *,
    user_id: str,
    provider: IntegrationProviderType,
    credential_id: str,
) -> SecretReference:
    return SecretReference(
        owner=SecretOwner(user_id=user_id),
        provider=provider.value,
        credential_id=credential_id,
        purpose=SecretPurpose.INTEGRATION_CREDENTIAL,
    )


def new_route_key() -> str:
    return secrets.token_urlsafe(32)


def new_stream_auth_token() -> str:
    return secrets.token_urlsafe(32)


def route_key_hash(route_key: str) -> str:
    return sha256(route_key.encode("utf-8")).hexdigest()


def update_existing_call_session(
    *,
    existing: IntegrationCallSessionRecord,
    agent_id: str,
    call_sid: str,
    payload_json: dict[str, object],
    initial_status: IntegrationCallStatus,
    answered: bool,
) -> IntegrationCallSessionRecord:
    existing.status = initial_status.value
    if initial_status in {
        IntegrationCallStatus.INITIATING,
        IntegrationCallStatus.RINGING,
        IntegrationCallStatus.IN_PROGRESS,
    }:
        existing.ended_at = None
    existing.agent_id = existing.agent_id or agent_id
    existing.runtime_session_id = existing.runtime_session_id or new_record_id()
    existing.livekit_room_name = existing.livekit_room_name or livekit_room_name(
        provider=IntegrationProviderType(existing.provider),
        call_sid=call_sid,
    )
    if answered:
        existing.answered_at = existing.answered_at or utc_now()
    existing.payload_json = {**existing.payload_json, "latest_webhook": payload_json}
    existing.updated_at = utc_now()
    return existing


def remote_party_phone_number(
    *,
    direction: IntegrationDirection,
    from_number: str | None,
    to_number: str | None,
) -> str | None:
    if direction == IntegrationDirection.INBOUND:
        return from_number
    if direction == IntegrationDirection.OUTBOUND:
        return to_number
    return None


def outbound_media_participant_identity(call_session_id: str) -> str:
    return f"call-{call_session_id}"


def elapsed_milliseconds(
    started_at: datetime | None,
    ended_at: datetime | None,
) -> int | None:
    if started_at is None or ended_at is None:
        return None
    elapsed = ended_at - started_at
    milliseconds = round(elapsed.total_seconds() * 1000)
    return milliseconds if milliseconds >= 0 else None


def latency_percentiles(
    samples: Iterable[int | None],
) -> IntegrationCallLatencyPercentiles:
    values = sorted(value for value in samples if isinstance(value, int) and not isinstance(value, bool))
    return IntegrationCallLatencyPercentiles(
        sample_count=len(values),
        p50_ms=nearest_rank_percentile(values, 50),
        p95_ms=nearest_rank_percentile(values, 95),
    )


def nearest_rank_percentile(
    values: list[int],
    percentile: int,
) -> int | None:
    if not values:
        return None
    rank = max(1, (percentile * len(values) + 99) // 100)
    return values[rank - 1]


def raise_unsupported_idempotent_insert(dialect_name: str) -> NoReturn:
    message = f"Unsupported database dialect for idempotent Twilio writes: {dialect_name}"
    raise RuntimeError(message)


def row_was_inserted(result: object) -> bool:
    rowcount = getattr(result, "rowcount", None)
    if not isinstance(rowcount, int):
        return True
    return rowcount > 0


def telnyx_cleanup_from_resource(resource: IntegrationResourceRecord) -> TelnyxPhoneNumberCleanup:
    previous = resource.retained_metadata_json.get("telnyx_previous_number")
    previous_metadata = previous if isinstance(previous, dict) else {}
    return TelnyxPhoneNumberCleanup(
        phone_number_id=resource.external_id,
        connection_id=metadata_string_value(resource.safe_metadata, "connection_id"),
        messaging_profile_id=metadata_string_value(resource.safe_metadata, "messaging_profile_id"),
        outbound_voice_profile_id=metadata_string_value(resource.safe_metadata, "outbound_voice_profile_id"),
        previous_connection_id=metadata_string_value(previous_metadata, "connection_id"),
        previous_messaging_profile_id=metadata_string_value(previous_metadata, "messaging_profile_id"),
    )


def ensure_selected_capabilities_supported(
    selected: tuple[IntegrationCapability, ...],
    candidate: TwilioPhoneNumberCandidate,
) -> None:
    available = set(candidate.capabilities)
    missing = tuple(capability for capability in selected if capability not in available)
    if missing:
        raise IntegrationResourceError(TWILIO_CAPABILITY_UNSUPPORTED_ERROR)


def resource_supports_action(*, resource: IntegrationResourceRecord, action: str) -> bool:
    if action == TWILIO_SEND_SMS_TOOL_NAME:
        return IntegrationCapability.SMS_SEND.value in resource.capabilities
    if action == TWILIO_PLACE_CALL_TOOL_NAME:
        return IntegrationCapability.OUTBOUND_VOICE.value in resource.capabilities
    if action == WHATSAPP_SEND_MESSAGE_TOOL_NAME:
        return (
            resource.provider == IntegrationProviderType.WHATSAPP.value
            and IntegrationCapability.WHATSAPP_SEND.value in resource.capabilities
        )
    return False


def webhook_settings_from_candidate(candidate: TwilioPhoneNumberCandidate) -> TwilioWebhookState:
    return TwilioWebhookState(
        voice_url=candidate.voice_url,
        voice_method=candidate.voice_method,
        sms_url=candidate.sms_url,
        sms_method=candidate.sms_method,
        status_callback=candidate.status_callback,
        status_callback_method=candidate.status_callback_method,
    )


def previous_webhook_settings_for_reconnect(
    *,
    candidate: TwilioPhoneNumberCandidate,
    resource: IntegrationResourceRecord,
) -> TwilioWebhookState:
    previous = previous_webhook_settings(resource)
    if previous is not None and candidate_matches_current_webhooks(
        candidate=candidate,
        resource=resource,
    ):
        return previous
    return webhook_settings_from_candidate(candidate)


def candidate_matches_current_webhooks(
    *,
    candidate: TwilioPhoneNumberCandidate,
    resource: IntegrationResourceRecord,
) -> bool:
    current = current_webhook_settings(resource)
    return (
        current.get("voice_url") == candidate.voice_url
        and current.get("voice_method") == candidate.voice_method
        and current.get("sms_url") == candidate.sms_url
        and current.get("sms_method") == candidate.sms_method
        and current.get("status_callback") == candidate.status_callback
        and current.get("status_callback_method") == candidate.status_callback_method
    )


def previous_webhook_settings(
    resource: IntegrationResourceRecord,
) -> TwilioWebhookState | None:
    previous = resource.retained_metadata_json.get("previous_webhooks")
    if not isinstance(previous, dict):
        return None
    return TwilioWebhookState(
        voice_url=metadata_string_value(previous, "voice_url"),
        voice_method=metadata_string_value(previous, "voice_method"),
        sms_url=metadata_string_value(previous, "sms_url"),
        sms_method=metadata_string_value(previous, "sms_method"),
        status_callback=metadata_string_value(previous, "status_callback"),
        status_callback_method=metadata_string_value(previous, "status_callback_method"),
    )


def metadata_string_value(metadata: Mapping[str, object], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) else None


def current_webhook_settings(resource: IntegrationResourceRecord) -> dict[str, object]:
    current = resource.retained_metadata_json.get("current_webhooks")
    return current if isinstance(current, dict) else {}


def voice_url_for_resource(resource: IntegrationResourceRecord) -> str:
    value = current_webhook_settings(resource).get("voice_url")
    if not isinstance(value, str) or not value.strip():
        raise IntegrationWebhookError(TWILIO_VOICE_WEBHOOK_MISSING_ERROR)
    return value


def status_callback_url_for_resource(resource: IntegrationResourceRecord) -> str | None:
    value = current_webhook_settings(resource).get("status_callback")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def outbound_call_callback_url(url: str, call_session_id: str) -> str:
    parts = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != OUTBOUND_CALL_SESSION_QUERY_PARAMETER
    ]
    query.append((OUTBOUND_CALL_SESSION_QUERY_PARAMETER, call_session_id))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        ),
    )


def telnyx_outbound_client_state(call_session_id: str) -> str:
    payload = orjson.dumps(
        {OUTBOUND_CALL_SESSION_QUERY_PARAMETER: call_session_id},
    )
    return base64.b64encode(payload).decode("ascii")


def media_stream_url_for_resource(resource: IntegrationResourceRecord) -> str:
    value = current_webhook_settings(resource).get("media_stream_url")
    if not isinstance(value, str) or not value.strip():
        raise IntegrationWebhookError(TWILIO_MEDIA_STREAM_WEBHOOK_MISSING_ERROR)
    return value


def communication_webhook_url(
    *,
    public_base_url: str,
    provider: IntegrationProviderType,
    purpose: IntegrationWebhookPurpose,
    route_key: str,
) -> str:
    return f"{public_base_url}/api/integrations/webhooks/communications/{provider.value}/{purpose.value}/{route_key}"


def stream_url_from_public_base(
    public_base_url: str,
    provider: IntegrationProviderType,
    route_key: str,
) -> str:
    stripped_base = public_base_url.rstrip("/")
    if not stripped_base.startswith("https://"):
        raise IntegrationWebhookError(TWILIO_PUBLIC_BASE_URL_SCHEME_ERROR)
    websocket_base = f"wss://{stripped_base.removeprefix('https://')}"
    return f"{websocket_base}/api/integrations/streams/communications/{provider.value}/media/{route_key}"


def require_public_base_url(public_base_url: str | None) -> str:
    if public_base_url is None:
        raise IntegrationWebhookError(TWILIO_PUBLIC_BASE_URL_REQUIRED_ERROR)
    return public_base_url.rstrip("/")


def livekit_room_name(*, provider: IntegrationProviderType, call_sid: str) -> str:
    safe_suffix = "".join(char for char in call_sid if char.isalnum() or char in ("-", "_"))
    return f"{provider.value}-{safe_suffix[:96]}"


def normalize_twilio_call_status(status: str | None) -> IntegrationCallStatus:
    if status in {"in-progress", "answered"}:
        return IntegrationCallStatus.IN_PROGRESS
    if status in {"completed", "busy", "no-answer", "canceled"}:
        return IntegrationCallStatus.COMPLETED
    if status == "ringing":
        return IntegrationCallStatus.RINGING
    if status == "failed":
        return IntegrationCallStatus.FAILED
    return IntegrationCallStatus.INITIATING


def normalize_twilio_message_status(status: str | None) -> IntegrationMessageStatus:
    if status == "received":
        return IntegrationMessageStatus.RECEIVED
    if status == "queued":
        return IntegrationMessageStatus.QUEUED
    if status == "sent":
        return IntegrationMessageStatus.SENT
    if status == "delivered":
        return IntegrationMessageStatus.DELIVERED
    if status in {"failed", "undelivered"}:
        return IntegrationMessageStatus.FAILED
    return IntegrationMessageStatus.UNKNOWN


def inbound_sms_agent_prompt() -> str:
    return inbound_communication_agent_prompt(CommunicationMedium.SMS)


def inbound_communication_agent_prompt(
    medium: CommunicationMedium,
    *,
    contact_display_name: str | None = None,
    is_group: bool = False,
) -> str:
    medium_label = "WhatsApp" if medium == CommunicationMedium.WHATSAPP else medium.value.upper()
    contact_context = (
        f"The sender is labelled {orjson.dumps(contact_display_name).decode('utf-8')}. "
        if contact_display_name is not None
        else ""
    )
    conversation_context = " in the group" if is_group else ""
    return (
        f"Reply to the latest inbound {medium_label} message{conversation_context} conversationally. "
        f"{contact_context}"
        "Return the response text only; "
        "Voxos validates and delivers it through the verified inbound route. If an available "
        "tool cannot establish a current fact, state that limitation briefly instead of guessing."
    )


def agent_scope_for_tool(user_id: str, agent_id: str) -> AgentScope:
    return AgentScope(
        owner_user_id=user_id,
        agent_id=agent_id,
        channel="text",
        source="integrations.phone.actions",
    )

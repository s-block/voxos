import asyncio
import base64
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Self, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import orjson
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import WebSocket
from livekit import rtc
from pydantic import SecretStr
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.websockets import WebSocketDisconnect

from voxos.agent_identity.models import AgentScope
from voxos.agent_runtime.records import AgentRunRecord, AgentSessionRecord, AgentTranscriptRecord
from voxos.agent_runtime.run_execution import AgentRunExecutionResult, TextAgentRunExecutor
from voxos.agent_runtime.run_models import AgentRunKind, AgentRunStatus
from voxos.agent_runtime.run_settings import AgentRunWorkerSettings
from voxos.agent_runtime.run_store import SqlModelAgentRunStore
from voxos.agent_runtime.run_worker import AgentRunWorker
from voxos.agent_runtime.tool_policy import (
    ToolAuthorizationService,
    ToolGrant,
    ToolGrantScope,
    ToolGrantStatus,
    ToolRisk,
)
from voxos.api.chats import build_text_chat_service
from voxos.contacts.models import ContactCreateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.errors import (
    IntegrationCredentialError,
    IntegrationResourceError,
    IntegrationUpstreamError,
    IntegrationWebhookError,
)
from voxos.integrations.inbound_access import InboundCommunicationReplyDisposition
from voxos.integrations.models import (
    CommunicationOutboundCallRequest,
    CommunicationOutboundSmsRequest,
    IntegrationActionResponse,
    IntegrationAuditAction,
    IntegrationAuthType,
    IntegrationCallStatus,
    IntegrationCapability,
    IntegrationCredentialStatus,
    IntegrationDirection,
    IntegrationEventStatus,
    IntegrationProviderType,
    IntegrationReadinessStatus,
    IntegrationReplyStatus,
    IntegrationResourceStatus,
    IntegrationRouteStatus,
    IntegrationStatus,
    IntegrationWebhookPurpose,
    TelnyxPhoneSetupRequest,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    IntegrationProviderDefinition,
    TelnyxCredentialBundle,
    TelnyxPhoneNumberCleanup,
    TelnyxPhoneNumberConfiguration,
    TelnyxPhoneNumberProvisioning,
)
from voxos.integrations.providers.telnyx import media_streams as telnyx_media_streams
from voxos.integrations.providers.telnyx.media_streams import (
    TELNYX_MEDIA_CHANNELS,
    TELNYX_MEDIA_CODEC,
    TELNYX_MEDIA_FRAME_DURATION_MS,
    TELNYX_MEDIA_PCMU_BYTES_PER_FRAME,
    TELNYX_MEDIA_SAMPLE_RATE,
    TELNYX_MEDIA_SAMPLES_PER_FRAME,
    LiveKitTelnyxAudioBridgeSession,
    TelnyxAudioBridgeSession,
    TelnyxMediaDownlinkError,
    TelnyxMediaStreamBridge,
    telnyx_downlink_media_message,
)
from voxos.integrations.providers.telnyx.provider import (
    TelnyxIntegrationProvider,
    TelnyxPhoneNumberCapabilityError,
    TelnyxProviderError,
    ensure_required_phone_number_capabilities,
    telnyx_number_capabilities,
)
from voxos.integrations.providers.telnyx.webhooks import (
    TELNYX_PUBLIC_KEY_INVALID_ERROR,
    TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME,
)
from voxos.integrations.records import (
    IntegrationAuditLogRecord,
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationConversationRecord,
    IntegrationCredentialRecord,
    IntegrationEventRecord,
    IntegrationMessageRecord,
    IntegrationReplyRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    UserIntegrationRecord,
)
from voxos.integrations.reply_dispatch import CommunicationReplyDispatcher
from voxos.integrations.services import (
    IntegrationWebhookRouteContext,
    UserIntegrationService,
    VoiceCallPreflight,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
    telnyx_outbound_client_state,
)
from voxos.integrations.telnyx_webhooks import (
    TELNYX_EVENT_PROCESSING_LEASE_SECONDS,
    TelnyxWebhookProcessor,
    normalize_telnyx_call_status,
    normalize_telnyx_message_status,
    outbound_call_session_id_from_payload,
    whatsapp_verification_prompt_digit,
)
from voxos.text.models import TextProvider, UserTextPreferences
from voxos.text.providers import (
    TextProviderClient,
    TextProviderEventKind,
    TextProviderRequest,
    TextProviderStreamEvent,
)
from voxos.text.storage import UserTextCredentialService, UserTextPreferenceService
from voxos.voice.demand import VoiceCallDemandRecord
from voxos.voice.errors import MissingLiveKitCredentialsError
from voxos.whatsapp.mobile_registration import (
    WhatsAppMobileRegistrationService,
)
from voxos.whatsapp.models import WhatsAppMobileRegistrationChannel
from voxos.whatsapp.service import WhatsAppIntegrationService
from voxos.whatsapp.settings import WhatsAppWorkerSettings

PHONE_NUMBER = "+447418606704"
SENDER_NUMBER = "+15551234567"
TELNYX_CONNECTION_ID = "1293384261075731499"
TELNYX_MESSAGING_PROFILE_ID = "182bd5e5-6e1a-4fe4-a799-aa6d9a6ab26e"
TELNYX_RELEASE_FAILED_ERROR = "release failed"
TELNYX_ANSWER_FAILED_ERROR = "answer failed"
BRIDGE_CONNECTION_FAILED_ERROR = "bridge connection failed"


def test_telnyx_streaming_started_is_media_not_answer_state() -> None:
    assert normalize_telnyx_call_status("streaming.started") == (IntegrationCallStatus.INITIATING)
    assert normalize_telnyx_call_status("call.answered") == (IntegrationCallStatus.IN_PROGRESS)


PROTOCOL_DETAIL_MARKER = "protocol-detail-must-not-be-logged"
PUBLIC_BASE_URL = "https://api.example.com"


@dataclass(frozen=True, slots=True)
class StaticGrantReader:
    grants: tuple[ToolGrant, ...]

    async def list_effective_grants(self, scope: AgentScope) -> tuple[ToolGrant, ...]:
        del scope
        return self.grants


@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [
        ("delivered", "delivered"),
        ("delivery_unconfirmed", "delivery_unconfirmed"),
        ("sending_failed", "failed"),
        ("delivery_failed", "failed"),
        ("message.finalized", "unknown"),
    ],
)
def test_telnyx_message_status_mapping(
    provider_status: str,
    expected: str,
) -> None:
    assert normalize_telnyx_message_status(provider_status).value == expected


@dataclass(slots=True)
class FakeTelnyxProvider:
    previous_connection_id: str | None = "previous-connection-id"
    previous_messaging_profile_id: str | None = "previous-messaging-profile-id"
    provisioned_numbers: list[dict[str, object]] = field(default_factory=list)
    released_numbers: list[TelnyxPhoneNumberCleanup] = field(default_factory=list)
    sent_messages: list[dict[str, object]] = field(default_factory=list)
    started_calls: list[dict[str, object]] = field(default_factory=list)
    answered_calls: list[dict[str, object]] = field(default_factory=list)
    answered_verification_calls: list[dict[str, object]] = field(default_factory=list)
    sent_call_dtmf: list[dict[str, object]] = field(default_factory=list)
    fail_release: bool = False
    fail_answer: bool = False
    outbound_started: asyncio.Event | None = None
    outbound_release: asyncio.Event | None = None
    inspected_configurations: list[dict[str, object]] = field(default_factory=list)
    repaired_configurations: list[dict[str, object]] = field(default_factory=list)
    inspected_configuration: TelnyxPhoneNumberConfiguration | None = None

    def definition(self) -> IntegrationProviderDefinition:
        return IntegrationProviderDefinition(
            provider=IntegrationProviderType.TELNYX,
            label="Telnyx",
            auth_types=(IntegrationAuthType.API_KEY,),
            capabilities=(
                IntegrationCapability.WEBHOOK_EVENTS,
                IntegrationCapability.INBOUND_VOICE,
                IntegrationCapability.OUTBOUND_VOICE,
                IntegrationCapability.SMS_READ,
                IntegrationCapability.SMS_SEND,
                IntegrationCapability.MEDIA_STREAM,
                IntegrationCapability.TOOL_ACTIONS,
            ),
        )

    async def validate_credentials(
        self,
        credentials: TelnyxCredentialBundle,
    ) -> IntegrationCredentialValidation:
        return IntegrationCredentialValidation(
            valid=True,
            safe_metadata={
                "webhook_public_key": credentials.webhook_public_key,
            },
        )

    async def provision_phone_number(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number: str,
        label: str,
        webhook_event_url: str,
        webhook_url: str,
    ) -> TelnyxPhoneNumberProvisioning:
        payload = {
            "label": label,
            "phone_number": phone_number,
            "webhook_event_url": webhook_event_url,
            "webhook_url": webhook_url,
        }
        self.provisioned_numbers.append(payload)
        return TelnyxPhoneNumberProvisioning(
            phone_number_id="telnyx-number-id",
            connection_id=TELNYX_CONNECTION_ID,
            messaging_profile_id=TELNYX_MESSAGING_PROFILE_ID,
            outbound_voice_profile_id="outbound-profile-id",
            capabilities=(
                IntegrationCapability.INBOUND_VOICE,
                IntegrationCapability.OUTBOUND_VOICE,
                IntegrationCapability.SMS_READ,
                IntegrationCapability.SMS_SEND,
                IntegrationCapability.MEDIA_STREAM,
                IntegrationCapability.TOOL_ACTIONS,
            ),
            previous_number={
                "connection_id": self.previous_connection_id,
                "messaging_profile_id": self.previous_messaging_profile_id,
            },
            number={"phone_number": phone_number},
            voice_application={"id": TELNYX_CONNECTION_ID, "webhook_event_url": webhook_event_url},
            messaging_profile={"id": TELNYX_MESSAGING_PROFILE_ID, "webhook_url": webhook_url},
            outbound_voice_profile={"id": "outbound-profile-id"},
        )

    async def release_phone_number(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        cleanup: TelnyxPhoneNumberCleanup,
    ) -> None:
        if self.fail_release:
            raise TelnyxProviderError(TELNYX_RELEASE_FAILED_ERROR)
        self.released_numbers.append(cleanup)

    async def inspect_phone_number_configuration(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str | None,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        del credentials
        self.inspected_configurations.append(
            {
                "phone_number_id": phone_number_id,
                "outbound_voice_profile_id": outbound_voice_profile_id,
                "expected_voice_url": expected_voice_url,
                "expected_sms_url": expected_sms_url,
            },
        )
        return self.inspected_configuration or ready_telnyx_configuration(
            phone_number_id=phone_number_id,
        )

    async def repair_phone_number_configuration(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        phone_number_id: str,
        outbound_voice_profile_id: str,
        label: str,
        expected_voice_url: str,
        expected_sms_url: str,
    ) -> TelnyxPhoneNumberConfiguration:
        del credentials
        self.repaired_configurations.append(
            {
                "phone_number_id": phone_number_id,
                "outbound_voice_profile_id": outbound_voice_profile_id,
                "label": label,
                "expected_voice_url": expected_voice_url,
                "expected_sms_url": expected_sms_url,
            },
        )
        return ready_telnyx_configuration(phone_number_id=phone_number_id)

    async def send_sms(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        self.sent_messages.append(
            {
                "from": from_number,
                "to": to_number,
                "body": body,
                "status_callback_url": status_callback_url,
            },
        )
        return IntegrationActionResponse(
            id="7a2d4f40-7e17-4a85-b679-e1f131e7a110",
            provider=IntegrationProviderType.TELNYX,
            action="send_sms",
            external_id="7a2d4f40-7e17-4a85-b679-e1f131e7a110",
            status="queued",
            safe_metadata={},
        )

    async def start_outbound_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
        command_id: str | None = None,
        client_state: str | None = None,
    ) -> IntegrationActionResponse:
        self.started_calls.append(
            {
                "from": from_number,
                "to": to_number,
                "stream_url": twiml_url,
                "status_callback_url": status_callback_url,
                "command_id": command_id,
                "client_state": client_state,
            },
        )
        if self.outbound_started is not None:
            self.outbound_started.set()
        if self.outbound_release is not None:
            await self.outbound_release.wait()
        return IntegrationActionResponse(
            id="call-leg-id",
            provider=IntegrationProviderType.TELNYX,
            action="place_call",
            external_id="v3:call-control-id",
            status="initiating",
            safe_metadata={},
        )

    async def answer_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        stream_url: str,
        webhook_url: str | None,
        command_id: str,
    ) -> IntegrationActionResponse:
        if self.fail_answer:
            raise TelnyxProviderError(TELNYX_ANSWER_FAILED_ERROR)
        self.answered_calls.append(
            {
                "call_control_id": call_control_id,
                "stream_url": stream_url,
                "webhook_url": webhook_url,
                "command_id": command_id,
            },
        )
        return IntegrationActionResponse(
            id=command_id,
            provider=IntegrationProviderType.TELNYX,
            action="answer_call",
            external_id=call_control_id,
            status="accepted",
            safe_metadata={},
        )

    async def answer_verification_call(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        webhook_url: str | None,
        command_id: str,
    ) -> IntegrationActionResponse:
        del credentials
        if self.fail_answer:
            raise TelnyxProviderError(TELNYX_ANSWER_FAILED_ERROR)
        self.answered_verification_calls.append(
            {
                "call_control_id": call_control_id,
                "webhook_url": webhook_url,
                "command_id": command_id,
            },
        )
        return IntegrationActionResponse(
            id=command_id,
            provider=IntegrationProviderType.TELNYX,
            action="answer_verification_call",
            external_id=call_control_id,
            status="accepted",
            safe_metadata={},
        )

    async def send_call_dtmf(
        self,
        *,
        credentials: TelnyxCredentialBundle,
        call_control_id: str,
        digits: str,
        command_id: str,
    ) -> IntegrationActionResponse:
        del credentials
        self.sent_call_dtmf.append(
            {
                "call_control_id": call_control_id,
                "digits": digits,
                "command_id": command_id,
            },
        )
        return IntegrationActionResponse(
            id=command_id,
            provider=IntegrationProviderType.TELNYX,
            action="send_call_dtmf",
            external_id=call_control_id,
            status="ok",
            safe_metadata={},
        )


@dataclass(slots=True)
class FakeSmsReplyTextProvider(TextProviderClient):
    requests: list[TextProviderRequest] = field(default_factory=list)

    async def stream_chat(
        self,
        request: TextProviderRequest,
    ) -> AsyncIterator[TextProviderStreamEvent]:
        self.requests.append(request)
        assert all(not tool.name.endswith("twilio_send_sms") for tool in request.tools)
        yield TextProviderStreamEvent(
            kind=TextProviderEventKind.MESSAGE_DELTA,
            delta="Automated reply",
        )


@dataclass(slots=True)
class UnexpectedRunExecutor:
    async def execute(self, run: AgentRunRecord) -> AgentRunExecutionResult:
        del run
        message = "The SMS run should use the text executor"
        raise AssertionError(message)


@pytest.mark.asyncio
async def test_telnyx_setup_configures_number_routes_and_secrets(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = public_key_hex(private_key)
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        response = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key),
        )
        routes = (
            await session.exec(
                select(IntegrationWebhookRouteRecord).where(
                    col(IntegrationWebhookRouteRecord.provider) == IntegrationProviderType.TELNYX.value,
                    col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
                ),
            )
        ).all()

    assert response.integration.status == IntegrationStatus.CONNECTED
    assert response.integration.enabled is True
    assert response.integration.logo.domain == "telnyx.com"
    assert response.integration.logo.label == "Telnyx"
    assert response.resources[0].status == IntegrationResourceStatus.CONNECTED
    assert response.resources[0].display_name == PHONE_NUMBER
    assert response.credentials[0].last_four == "cret"
    assert len(routes) == 4
    assert provider.provisioned_numbers == [
        {
            "label": "Telnyx",
            "phone_number": PHONE_NUMBER,
            "webhook_event_url": provider.provisioned_numbers[0]["webhook_event_url"],
            "webhook_url": provider.provisioned_numbers[0]["webhook_url"],
        },
    ]
    assert "/communications/telnyx/voice/" in provider.provisioned_numbers[0]["webhook_event_url"]
    assert "/communications/telnyx/sms/" in provider.provisioned_numbers[0]["webhook_url"]


@pytest.mark.asyncio
async def test_telnyx_readiness_is_read_only_and_repair_requires_confirmation(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    repaired_public_base_url = "https://repaired.example.com"
    scoped_session_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )

    async with scoped_session_factory() as session:
        service = telnyx_service(
            session=session,
            session_factory=scoped_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        service = telnyx_service(
            session=session,
            session_factory=scoped_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
            public_base_url=repaired_public_base_url,
        )

        readiness = await service.verify_telnyx_phone_configuration(
            user_id="user-1",
            integration_id=setup.integration.id,
        )
        with pytest.raises(IntegrationWebhookError, match="repair confirmation"):
            await service.repair_telnyx_phone_configuration(
                user_id="user-1",
                integration_id=setup.integration.id,
                confirm_provider_changes=False,
            )
        repaired = await service.repair_telnyx_phone_configuration(
            user_id="user-1",
            integration_id=setup.integration.id,
            confirm_provider_changes=True,
        )
        resource = await session.get(IntegrationResourceRecord, setup.resources[0].id)

    checks = {check.name: check for check in readiness.checks}
    assert checks["inbound_sms"].status == IntegrationReadinessStatus.READY
    assert checks["outbound_sms"].status == IntegrationReadinessStatus.READY
    assert checks["inbound_voice"].status == IntegrationReadinessStatus.READY
    assert checks["outbound_voice"].status == IntegrationReadinessStatus.READY
    assert checks["agent_run_processing"].code == "text_provider_not_configured"
    assert provider.inspected_configurations[0]["expected_voice_url"].startswith(
        f"{repaired_public_base_url}/api/integrations/webhooks/communications/telnyx/voice/",
    )
    assert provider.inspected_configurations[0]["expected_sms_url"].startswith(
        f"{repaired_public_base_url}/api/integrations/webhooks/communications/telnyx/sms/",
    )
    assert provider.repaired_configurations[0]["phone_number_id"] == "telnyx-number-id"
    assert repaired.repaired is True
    assert resource is not None
    current_webhooks = resource.retained_metadata_json["current_webhooks"]
    assert isinstance(current_webhooks, dict)
    assert current_webhooks["status_callback"].startswith(repaired_public_base_url)
    assert current_webhooks["media_stream_url"].startswith("wss://repaired.example.com/")


@pytest.mark.asyncio
async def test_telnyx_outbound_actions_use_telnyx_provider(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        current_public_base_url = "https://current.example.com"
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            public_base_url=current_public_base_url,
        )
        sms_result = await integration_service.send_sms(
            user_id="user-1",
            provider=IntegrationProviderType.TELNYX,
            request=outbound_sms_request(
                integration_id=setup.integration.id,
                resource_id=resource.id,
            ),
        )
        call_result = await integration_service.start_outbound_call(
            user_id="user-1",
            provider=IntegrationProviderType.TELNYX,
            request=outbound_call_request(
                integration_id=setup.integration.id,
                resource_id=resource.id,
            ),
        )
        audit_rows = (
            await session.exec(
                select(IntegrationAuditLogRecord).where(
                    col(IntegrationAuditLogRecord.action).in_(
                        (
                            IntegrationAuditAction.SMS_SENT.value,
                            IntegrationAuditAction.CALL_STARTED.value,
                        ),
                    ),
                ),
            )
        ).all()
        call = (
            await session.exec(
                select(IntegrationCallSessionRecord).where(
                    col(IntegrationCallSessionRecord.provider) == IntegrationProviderType.TELNYX.value,
                ),
            )
        ).one()

    assert sms_result.provider == IntegrationProviderType.TELNYX
    assert call_result.provider == IntegrationProviderType.TELNYX
    assert provider.sent_messages[0]["from"] == PHONE_NUMBER
    assert provider.inspected_configurations[0]["expected_voice_url"].startswith(current_public_base_url)
    assert provider.started_calls[0]["stream_url"].startswith("wss://current.example.com/")
    assert str(provider.started_calls[0]["status_callback_url"]).startswith(current_public_base_url)
    assert provider.started_calls[0]["command_id"] == call.id
    assert (
        outbound_call_session_id_from_payload(
            {"client_state": provider.started_calls[0]["client_state"]},
        )
        == call.id
    )
    assert parse_qs(
        urlsplit(str(provider.started_calls[0]["stream_url"])).query,
    )["voxos_call_id"] == [call.id]
    assert {row.provider for row in audit_rows} == {IntegrationProviderType.TELNYX.value}


@pytest.mark.asyncio
async def test_telnyx_finalized_message_uses_nested_status_and_safe_errors(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        result = await service.send_sms(
            user_id="user-1",
            provider=IntegrationProviderType.TELNYX,
            request=outbound_sms_request(
                integration_id=setup.integration.id,
                resource_id=resource.id,
            ),
        )
        route = await route_for(
            session=session,
            resource_id=resource.id,
            purpose=IntegrationWebhookPurpose.STATUS,
        )
        body = telnyx_message_status_body(
            event_id="message-status-failed",
            message_id=result.external_id or result.id,
            status="delivery_failed",
        )
        timestamp = str(int(time.time()))
        await TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        ).handle_status(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=body,
            ),
            timestamp=timestamp,
        )
        message = (await session.exec(select(IntegrationMessageRecord))).one()
        activity = await service.list_message_activity(
            user_id="user-1",
            integration_id=setup.integration.id,
            limit=20,
            offset=0,
        )

    assert message.status == "failed"
    assert message.payload_json["latest_status"] == {
        "statuses": ["delivery_failed"],
        "errors": [{"code": "40001", "title": "Destination blocked"}],
    }
    assert "detail" not in str(message.payload_json["latest_status"])
    assert activity.messages[0].remote_party == SENDER_NUMBER
    assert activity.messages[0].failure_code == "40001"
    assert activity.messages[0].failure_title == "Destination blocked"


@pytest.mark.asyncio
async def test_outbound_telnyx_call_preflights_voice_before_provider(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async def missing_livekit_preflight(*, session: AsyncSession, user_id: str) -> None:
        del session, user_id
        raise MissingLiveKitCredentialsError

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_call_preflight=missing_livekit_preflight,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )

        with pytest.raises(MissingLiveKitCredentialsError):
            await integration_service.start_outbound_call(
                user_id="user-1",
                provider=IntegrationProviderType.TELNYX,
                request=outbound_call_request(
                    integration_id=setup.integration.id,
                    resource_id=setup.resources[0].id,
                ),
            )

    assert provider.started_calls == []


@pytest.mark.asyncio
async def test_outbound_telnyx_call_rejects_unready_provider_routing_before_reservation(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider(
        inspected_configuration=ready_telnyx_configuration().model_copy(
            update={"voice_destinations": ()},
        ),
    )

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )

        with pytest.raises(IntegrationResourceError, match="outbound voice routing is not ready"):
            await integration_service.start_outbound_call(
                user_id="user-1",
                provider=IntegrationProviderType.TELNYX,
                request=outbound_call_request(
                    integration_id=setup.integration.id,
                    resource_id=setup.resources[0].id,
                ),
            )
        calls = (await session.exec(select(IntegrationCallSessionRecord))).all()
        demands = (await session.exec(select(VoiceCallDemandRecord))).all()

    assert provider.started_calls == []
    assert calls == []
    assert demands == []


@pytest.mark.asyncio
async def test_outbound_telnyx_waits_for_committed_worker_demand_before_provider(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    waiter_calls: list[str] = []

    async def request_worker(
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        session.add(
            VoiceCallDemandRecord(
                user_id=user_id,
                agent_id=agent_id,
                runtime_session_id=runtime_session_id,
                room_name=room_name,
                source=source,
                project_key="b" * 64,
                expires_at=utc_now() + timedelta(minutes=1),
            ),
        )
        await session.flush()

    async def wait_for_worker(*, user_id: str, runtime_session_id: str) -> None:
        async with tenant_scoped_session_factory(
            sqlite_session_factory,
            user_id,
        )() as independent_session:
            demand = (
                await independent_session.exec(
                    select(VoiceCallDemandRecord).where(
                        col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    ),
                )
            ).one()
        assert demand.user_id == user_id
        assert provider.started_calls == []
        waiter_calls.append(runtime_session_id)

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_worker_demand_requester=request_worker,
            voice_worker_readiness_waiter=wait_for_worker,
        )

        await integration_service.start_outbound_call(
            user_id="user-1",
            provider=IntegrationProviderType.TELNYX,
            request=outbound_call_request(
                integration_id=setup.integration.id,
                resource_id=setup.resources[0].id,
            ),
        )

    assert len(waiter_calls) == 1
    assert len(provider.started_calls) == 1


@pytest.mark.asyncio
async def test_outbound_telnyx_webhook_binds_reservation_before_provider_returns(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    outbound_started = asyncio.Event()
    outbound_release = asyncio.Event()
    provider = FakeTelnyxProvider(
        outbound_started=outbound_started,
        outbound_release=outbound_release,
    )

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        route = await route_for(
            session=session,
            resource_id=resource.id,
            purpose=IntegrationWebhookPurpose.STATUS,
        )
        call_task = asyncio.create_task(
            service.start_outbound_call(
                user_id="user-1",
                provider=IntegrationProviderType.TELNYX,
                request=outbound_call_request(
                    integration_id=setup.integration.id,
                    resource_id=resource.id,
                ),
            ),
        )
        await outbound_started.wait()
        client_state = provider.started_calls[0]["client_state"]
        assert isinstance(client_state, str)
        body = telnyx_outbound_voice_body(client_state=client_state)
        timestamp = str(int(time.time()))
        try:
            async with tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            )() as callback_session:
                await TelnyxWebhookProcessor(
                    session=callback_session,
                    secret_vault=user_secret_vault,
                    public_base_url=PUBLIC_BASE_URL,
                    provider_registry=IntegrationProviderRegistry(
                        telnyx=provider,
                    ),
                    session_factory=tenant_scoped_session_factory(
                        sqlite_session_factory,
                        "user-1",
                    ),
                    voice_worker_demand_requester=(noop_voice_worker_demand_requester),
                    voice_worker_readiness_waiter=(noop_voice_worker_readiness_waiter),
                ).handle_status(
                    route_context=route_context(route),
                    body=body,
                    payload=orjson.loads(body),
                    signature=signature(
                        private_key=private_key,
                        timestamp=timestamp,
                        body=body,
                    ),
                    timestamp=timestamp,
                )
        finally:
            outbound_release.set()
        result = await call_task

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as verification_session:
        calls = (
            await verification_session.exec(
                select(IntegrationCallSessionRecord),
            )
        ).all()

    assert result.external_id == "v3:call-control-id"
    assert len(calls) == 1
    assert calls[0].id == outbound_call_session_id_from_payload(
        {"client_state": client_state},
    )
    assert calls[0].external_call_id == "v3:call-control-id"
    assert calls[0].agent_id == "agent-1"
    assert calls[0].direction == IntegrationDirection.OUTBOUND.value


@pytest.mark.asyncio
async def test_outbound_telnyx_correlation_rejects_another_tenants_reservation(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    tenant_one_key = Ed25519PrivateKey.generate()
    tenant_two_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as tenant_one_session:
        tenant_one_setup = await telnyx_service(
            session=tenant_one_session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(tenant_one_key)),
        )
        tenant_one_route = await route_for(
            session=tenant_one_session,
            resource_id=tenant_one_setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.STATUS,
        )

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-2",
    )() as tenant_two_session:
        tenant_two_service = telnyx_service(
            session=tenant_two_session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-2",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        tenant_two_setup = await tenant_two_service.setup_telnyx_phone(
            user_id="user-2",
            request=telnyx_request(public_key=public_key_hex(tenant_two_key)),
        )
        tenant_two_call = await tenant_two_service.start_communication_call_session(
            user_id="user-2",
            integration_id=tenant_two_setup.integration.id,
            resource_id=tenant_two_setup.resources[0].id,
            provider=IntegrationProviderType.TELNYX,
            direction=IntegrationDirection.OUTBOUND,
            call_sid="reservation-tenant-two",
            from_number=PHONE_NUMBER,
            to_number=SENDER_NUMBER,
            payload_json={"reservation_id": "reservation-tenant-two"},
            agent_id="agent-2",
            initial_status=IntegrationCallStatus.INITIATING,
            answered=False,
        )
        await tenant_two_session.commit()

    body = telnyx_outbound_voice_body(
        client_state=telnyx_outbound_client_state(tenant_two_call.id),
    )
    timestamp = str(int(time.time()))
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as callback_session:
        processor = TelnyxWebhookProcessor(
            session=callback_session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            voice_worker_demand_requester=noop_voice_worker_demand_requester,
            voice_worker_readiness_waiter=noop_voice_worker_readiness_waiter,
        )
        with pytest.raises(
            IntegrationWebhookError,
            match="correlation is invalid",
        ):
            await processor.handle_status(
                route_context=route_context(tenant_one_route),
                body=body,
                payload=orjson.loads(body),
                signature=signature(
                    private_key=tenant_one_key,
                    timestamp=timestamp,
                    body=body,
                ),
                timestamp=timestamp,
            )
        await callback_session.rollback()
        tenant_one_calls = (
            await callback_session.exec(
                select(IntegrationCallSessionRecord).where(
                    col(IntegrationCallSessionRecord.user_id) == "user-1",
                ),
            )
        ).all()

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-2",
    )() as tenant_two_verification_session:
        persisted_tenant_two_call = await tenant_two_verification_session.get(
            IntegrationCallSessionRecord,
            tenant_two_call.id,
        )

    assert tenant_one_calls == []
    assert persisted_tenant_two_call is not None
    assert persisted_tenant_two_call.external_call_id == "reservation-tenant-two"


@pytest.mark.asyncio
async def test_telnyx_call_session_snapshots_inbound_contact_access(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        contact = await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Telnyx Admin Caller",
                phone_number=SENDER_NUMBER,
                is_admin=True,
            ),
        )

        call = await integration_service.start_telnyx_call_session(
            user_id="user-1",
            integration_id=setup.integration.id,
            resource_id=setup.resources[0].id,
            direction=IntegrationDirection.INBOUND,
            call_control_id="v3:contact-call",
            from_number=SENDER_NUMBER,
            to_number=PHONE_NUMBER,
            payload_json={},
        )

    assert call.contact_id == contact.id
    assert call.contact_display_name == "Telnyx Admin Caller"
    assert call.caller_is_admin is True


@pytest.mark.asyncio
async def test_telnyx_disconnect_releases_provider_resources_before_revoking_locally(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        await integration_service.disconnect_integration(
            user_id="user-1",
            integration_id=setup.integration.id,
        )
        resources = (await session.exec(select(IntegrationResourceRecord))).all()
        credentials = (await session.exec(select(IntegrationCredentialRecord))).all()
        active_routes = (
            await session.exec(
                select(IntegrationWebhookRouteRecord).where(
                    col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
                ),
            )
        ).all()
        audit = (
            await session.exec(
                select(IntegrationAuditLogRecord).where(
                    col(IntegrationAuditLogRecord.action) == IntegrationAuditAction.INTEGRATION_DISCONNECTED.value,
                ),
            )
        ).one()

    assert provider.released_numbers == [
        TelnyxPhoneNumberCleanup(
            phone_number_id="telnyx-number-id",
            connection_id=TELNYX_CONNECTION_ID,
            messaging_profile_id=TELNYX_MESSAGING_PROFILE_ID,
            outbound_voice_profile_id="outbound-profile-id",
            previous_connection_id="previous-connection-id",
            previous_messaging_profile_id="previous-messaging-profile-id",
        ),
    ]
    assert resources[0].status == IntegrationResourceStatus.DISABLED.value
    assert all(credential.status == IntegrationCredentialStatus.REVOKED.value for credential in credentials)
    assert active_routes == []
    assert audit.provider == IntegrationProviderType.TELNYX.value


@pytest.mark.asyncio
async def test_telnyx_disconnect_unassigns_voxos_routing_without_previous_routes(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider(
        previous_connection_id=None,
        previous_messaging_profile_id=None,
    )

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        await integration_service.disconnect_integration(
            user_id="user-1",
            integration_id=setup.integration.id,
        )

    assert provider.released_numbers[0].previous_connection_id is None
    assert provider.released_numbers[0].previous_messaging_profile_id is None


@pytest.mark.asyncio
async def test_telnyx_disconnect_keeps_credentials_when_provider_cleanup_fails(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider(fail_release=True)

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        await integration_service.disconnect_integration(
            user_id="user-1",
            integration_id=setup.integration.id,
        )
        integration = await session.get(UserIntegrationRecord, setup.integration.id)
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == setup.integration.id,
                ),
            )
        ).one()
        credentials = (
            await session.exec(
                select(IntegrationCredentialRecord).where(
                    col(IntegrationCredentialRecord.integration_id) == setup.integration.id,
                ),
            )
        ).all()

    assert integration is not None
    assert provider.released_numbers == []
    assert integration.status == IntegrationStatus.FAILED.value
    assert integration.deleted_at is None
    assert resource.status == IntegrationResourceStatus.FAILED_DISCONNECT_CLEANUP.value
    assert resource.deleted_at is None
    assert all(credential.status == IntegrationCredentialStatus.ACTIVE.value for credential in credentials)


@pytest.mark.asyncio
async def test_telnyx_signed_inbound_sms_records_message_and_enqueues_agent_run(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    scoped_session_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )
    authorization_service = cast(
        ToolAuthorizationService,
        StaticGrantReader(
            grants=(
                ToolGrant(
                    id="grant-search-contacts",
                    user_id="user-1",
                    agent_id="agent-1",
                    tool_name="search_contacts",
                    tool_source=ToolGrantScope.INTERNAL,
                    status=ToolGrantStatus.ALLOWED,
                    risk=ToolRisk.LOW,
                ),
            ),
        ),
    )

    async with scoped_session_factory() as session:
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Admin sender",
                phone_number=SENDER_NUMBER,
                is_admin=True,
            ),
        )
        setup = await telnyx_service(
            session=session,
            session_factory=scoped_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.SMS,
        )
        body = telnyx_sms_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=scoped_session_factory,
            authorization_service=authorization_service,
        )
        result = await processor.handle_sms(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=signature(private_key=private_key, timestamp=timestamp, body=body),
            timestamp=timestamp,
        )
        message = (
            await session.exec(
                select(IntegrationMessageRecord).where(
                    col(IntegrationMessageRecord.provider) == IntegrationProviderType.TELNYX.value,
                ),
            )
        ).one()
        run = (
            await session.exec(
                select(AgentRunRecord).where(col(AgentRunRecord.user_id) == "user-1"),
            )
        ).one()
        conversation = (await session.exec(select(IntegrationConversationRecord))).one()
        reply = (await session.exec(select(IntegrationReplyRecord))).one()

    assert result["ok"] is True
    assert message.from_number == SENDER_NUMBER
    assert message.to_number == PHONE_NUMBER
    assert run.payload_json["integration"]["external_message_id"] == "message-1"
    policy_entries = run.effective_tool_policy_json["entries"]
    assert isinstance(policy_entries, list)
    assert [entry["tool_name"] for entry in policy_entries] == ["search_contacts"]
    assert run.kind == AgentRunKind.COMMUNICATION_REPLY.value
    assert run.chat_id == conversation.runtime_session_id
    assert run.payload_json["conversation_id"] == conversation.id
    assert run.payload_json["inbound_message_id"] == message.id
    assert reply.agent_run_id == run.id
    assert reply.status == IntegrationReplyStatus.PENDING.value
    assert "from_number" not in run.payload_json["integration"]


@pytest.mark.asyncio
async def test_telnyx_verification_sms_is_captured_without_agent_reply(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    scoped_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )

    async with scoped_factory() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=scoped_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        source = setup.resources[0]
        registration = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).start(
            user_id="user-1",
            source_resource_id=source.id,
            channel=WhatsAppMobileRegistrationChannel.SMS,
            integration_service=WhatsAppIntegrationService(
                session=session,
                session_factory=scoped_factory,
                secret_vault=user_secret_vault,
                settings=WhatsAppWorkerSettings(),
            ),
        )
        route = await route_for(
            session=session,
            resource_id=source.id,
            purpose=IntegrationWebhookPurpose.SMS,
        )
        body = telnyx_sms_body(
            text="Your WhatsApp Business verification code is 123 456",
        )
        timestamp = str(int(time.time()))
        result = await TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=scoped_factory,
        ).handle_sms(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=body,
            ),
            timestamp=timestamp,
        )
        captured = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).get_status(
            user_id="user-1",
            registration_id=registration.registration_id,
        )
        messages = (await session.exec(select(IntegrationMessageRecord))).all()
        runs = (await session.exec(select(AgentRunRecord))).all()

    assert result == {
        "ok": True,
        "captured_for_whatsapp_registration": True,
    }
    assert messages == []
    assert runs == []
    assert captured.candidates[0].code == "123456"


@pytest.mark.asyncio
async def test_telnyx_verification_call_captures_code_without_agent_or_transcript(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    worker_requests: list[str] = []
    scoped_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )

    async def request_worker(
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        del session, user_id, agent_id, room_name, source
        worker_requests.append(runtime_session_id)

    async with scoped_factory() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=scoped_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        source = setup.resources[0]
        registration = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).start(
            user_id="user-1",
            source_resource_id=source.id,
            channel=WhatsAppMobileRegistrationChannel.VOICE,
            integration_service=WhatsAppIntegrationService(
                session=session,
                session_factory=scoped_factory,
                secret_vault=user_secret_vault,
                settings=WhatsAppWorkerSettings(),
            ),
        )
        route = await route_for(
            session=session,
            resource_id=source.id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=scoped_factory,
            voice_worker_demand_requester=request_worker,
        )
        timestamp = str(int(time.time()))
        call_body = telnyx_voice_body()
        await processor.handle_voice(
            route_context=route_context(route),
            body=call_body,
            payload=orjson.loads(call_body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=call_body,
            ),
            timestamp=timestamp,
        )
        first_transcription_body = telnyx_transcription_body(
            event_id="voice-transcription-event-1",
            transcript="To receive your WhatsApp verification code, press or.",
        )
        await processor.handle_voice(
            route_context=route_context(route),
            body=first_transcription_body,
            payload=orjson.loads(first_transcription_body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=first_transcription_body,
            ),
            timestamp=timestamp,
        )
        transcription_body = telnyx_transcription_body(
            event_id="voice-transcription-event-2",
            transcript="Your WhatsApp code is one two three four five six",
        )
        result = await processor.handle_voice(
            route_context=route_context(route),
            body=transcription_body,
            payload=orjson.loads(transcription_body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=transcription_body,
            ),
            timestamp=timestamp,
        )
        status = await WhatsAppMobileRegistrationService(
            session=session,
            secret_vault=user_secret_vault,
        ).get_status(
            user_id="user-1",
            registration_id=registration.registration_id,
        )
        call = (await session.exec(select(IntegrationCallSessionRecord))).one()
        events = (await session.exec(select(IntegrationEventRecord))).all()
        call_transcripts = (await session.exec(select(IntegrationCallTranscriptRecord))).all()
        agent_transcripts = (await session.exec(select(AgentTranscriptRecord))).all()

    assert result == {"ok": True}
    assert worker_requests == []
    assert provider.answered_calls == []
    assert len(provider.answered_verification_calls) == 1
    verification_call = provider.answered_verification_calls[0]
    assert verification_call["call_control_id"] == "v3:call-control-id"
    assert verification_call["command_id"] == "answer-voice-event-1"
    assert isinstance(verification_call["webhook_url"], str)
    assert verification_call["webhook_url"].startswith(
        f"{PUBLIC_BASE_URL}/api/integrations/webhooks/communications/telnyx/voice/",
    )
    assert provider.sent_call_dtmf == [
        {
            "call_control_id": "v3:call-control-id",
            "digits": "4",
            "command_id": f"verify-dtmf-{call.id[:24]}",
        },
    ]
    assert call.transcript_enabled is False
    assert call.payload_json["voxos_call_purpose"] == ("whatsapp_mobile_registration")
    assert status.state == "code_received"
    assert status.candidates[0].channel == WhatsAppMobileRegistrationChannel.VOICE
    assert status.candidates[0].code == "123456"
    assert call_transcripts == []
    assert agent_transcripts == []
    serialized_event_payloads = orjson.dumps(
        [event.payload_json for event in events],
    )
    assert b"one two three" not in serialized_event_payloads
    assert all("transcript" not in event.payload_json and "code" not in event.payload_json for event in events)


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("To receive your WhatsApp verification code, press 6.", "6"),
        ("To receive your WhatsApp verification code, press four.", "4"),
        ("To receive your WhatsApp verification code, press for.", "4"),
        ("To receive your WhatsApp verification code, press or...", "4"),
        ("Your WhatsApp verification code is 123456.", None),
    ],
)
def test_whatsapp_verification_prompt_digit(
    transcript: str,
    expected: str | None,
) -> None:
    assert whatsapp_verification_prompt_digit(transcript) == expected


@pytest.mark.asyncio
async def test_telnyx_inbound_sms_worker_sends_exactly_one_reply_to_verified_sender(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    scoped_session_factory = tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )

    async with scoped_session_factory() as session:
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Saved sender",
                phone_number=SENDER_NUMBER,
            ),
        )
        setup = await telnyx_service(
            session=session,
            session_factory=scoped_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.SMS,
        )
        body = telnyx_sms_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=scoped_session_factory,
        )
        webhook_arguments = {
            "route_context": route_context(route),
            "body": body,
            "payload": orjson.loads(body),
            "signature": signature(
                private_key=private_key,
                timestamp=timestamp,
                body=body,
            ),
            "timestamp": timestamp,
        }
        await processor.handle_sms(**webhook_arguments)
        await processor.handle_sms(**webhook_arguments)
        run = (
            await session.exec(
                select(AgentRunRecord).where(col(AgentRunRecord.user_id) == "user-1"),
            )
        ).one()
        policy_entries = run.effective_tool_policy_json["entries"]
        assert isinstance(policy_entries, list)
        assert policy_entries == []

    async with scoped_session_factory() as session:
        credential = await UserTextCredentialService(
            session=session,
            secret_vault=user_secret_vault,
        ).create_credential(
            user_id="user-1",
            provider=TextProvider.OPENAI,
            label="Test text provider",
            secret=SecretStr("test-text-provider-secret"),
        )
        await UserTextPreferenceService(session).set_preferences(
            UserTextPreferences(
                user_id="user-1",
                provider=TextProvider.OPENAI,
                credential_ref=credential.id,
            ),
        )

    text_provider = FakeSmsReplyTextProvider()
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code=404, request=request),
        ),
    )
    text_service = build_text_chat_service(
        session_factory=scoped_session_factory,
        secret_vault=user_secret_vault,
        http_client=http_client,
        public_base_url=PUBLIC_BASE_URL,
        text_provider_client=text_provider,
    )
    run_store = SqlModelAgentRunStore(session_factory=scoped_session_factory)
    worker = AgentRunWorker(
        run_store=run_store,
        claimable_kinds=(AgentRunKind.COMMUNICATION_REPLY,),
        executor=TextAgentRunExecutor(
            text_service=text_service,
            run_store=run_store,
            run_chat_service=text_service.run_chat_service,
            fallback_executor=UnexpectedRunExecutor(),
            reply_dispatcher=CommunicationReplyDispatcher(
                session_factory=scoped_session_factory,
                secret_vault=user_secret_vault,
                public_base_url=PUBLIC_BASE_URL,
                provider_registry=IntegrationProviderRegistry(telnyx=provider),
            ),
        ),
        settings=AgentRunWorkerSettings(
            AGENT_RUN_WORKER_ID="sms-worker-1",
            AGENT_RUN_QUEUES="agent-default",
            AGENT_RUN_MAX_CONCURRENCY=1,
            AGENT_RUN_CLAIM_BATCH_SIZE=1,
            AGENT_RUN_HEARTBEAT_INTERVAL_SECONDS=1,
            AGENT_RUN_LEASE_TIMEOUT_SECONDS=60,
            AGENT_RUN_POLL_INTERVAL_SECONDS=1,
            AGENT_RUN_RETRY_DELAY_SECONDS=1,
            AGENT_RUN_SHUTDOWN_GRACE_SECONDS=1,
        ),
    )

    try:
        claimed_count = await worker.run_once()
        await worker.wait_for_idle()
    finally:
        await http_client.aclose()

    completed = await run_store.get_run(user_id="user-1", run_id=run.id)
    assert claimed_count == 1
    assert completed is not None
    assert completed.status == AgentRunStatus.SUCCEEDED.value
    assert len(provider.sent_messages) == 1
    assert provider.sent_messages[0]["to"] == SENDER_NUMBER
    assert provider.sent_messages[0]["body"] == "Automated reply"
    assert len(text_provider.requests) == 1
    assert text_provider.requests[0].max_output_tokens is not None
    assert any("Response profile: sms:v1" in message.content for message in text_provider.requests[0].messages)

    async with scoped_session_factory() as session:
        reply = (await session.exec(select(IntegrationReplyRecord))).one()
        conversations = (await session.exec(select(IntegrationConversationRecord))).all()
        sessions = (await session.exec(select(AgentSessionRecord))).all()
        transcripts = (
            await session.exec(
                select(AgentTranscriptRecord).order_by(
                    col(AgentTranscriptRecord.created_at),
                ),
            )
        ).all()

    assert reply.status == IntegrationReplyStatus.PROVIDER_ACCEPTED.value
    assert reply.outbound_message_id is not None
    assert reply.response_profile == "sms:v1"
    assert reply.estimated_segments == 1
    assert len(conversations) == 1
    assert len(sessions) == 1
    assert [transcript.text for transcript in transcripts] == ["hello", "Automated reply"]


@pytest.mark.asyncio
async def test_telnyx_duplicate_inbound_sms_creates_one_message_event_and_run(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Saved sender",
                phone_number=SENDER_NUMBER,
            ),
        )
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.SMS,
        )
        body = telnyx_sms_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
        )
        request_signature = signature(
            private_key=private_key,
            timestamp=timestamp,
            body=body,
        )

        first = await processor.handle_sms(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=request_signature,
            timestamp=timestamp,
        )
        second = await processor.handle_sms(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=request_signature,
            timestamp=timestamp,
        )
        messages = (await session.exec(select(IntegrationMessageRecord))).all()
        events = (await session.exec(select(IntegrationEventRecord))).all()
        runs = (await session.exec(select(AgentRunRecord))).all()
        conversations = (await session.exec(select(IntegrationConversationRecord))).all()
        replies = (await session.exec(select(IntegrationReplyRecord))).all()

    assert first["agent_run_id"] == second["agent_run_id"]
    assert len(messages) == 1
    assert len(events) == 1
    assert len(runs) == 1
    assert len(conversations) == 1
    assert len(replies) == 1


@pytest.mark.asyncio
async def test_telnyx_failed_sms_enqueue_is_retryable_without_duplicate_reply(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    scoped_session_factory = tenant_scoped_session_factory(sqlite_session_factory, "user-1")
    original_enqueue = UserIntegrationService.enqueue_inbound_sms_agent_reply
    attempts = 0

    async def flaky_enqueue(
        service: UserIntegrationService,
        *,
        user_id: str,
        integration_id: str,
        resource_id: str,
        provider: IntegrationProviderType,
        inbound_message_id: str,
        external_message_id: str,
        from_number: str,
    ) -> InboundCommunicationReplyDisposition:
        nonlocal attempts
        attempts += 1
        disposition = await original_enqueue(
            service,
            user_id=user_id,
            integration_id=integration_id,
            resource_id=resource_id,
            provider=provider,
            inbound_message_id=inbound_message_id,
            external_message_id=external_message_id,
            from_number=from_number,
        )
        if attempts == 1:
            message = "temporary queue failure"
            raise RuntimeError(message)
        return disposition

    monkeypatch.setattr(
        UserIntegrationService,
        "enqueue_inbound_sms_agent_reply",
        flaky_enqueue,
    )

    async with scoped_session_factory() as session:
        await UserContactService(session).create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Saved sender",
                phone_number=SENDER_NUMBER,
            ),
        )
        setup = await telnyx_service(
            session=session,
            session_factory=scoped_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.SMS,
        )
        body = telnyx_sms_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=scoped_session_factory,
        )
        arguments = {
            "route_context": route_context(route),
            "body": body,
            "payload": orjson.loads(body),
            "signature": signature(private_key=private_key, timestamp=timestamp, body=body),
            "timestamp": timestamp,
        }

        with pytest.raises(RuntimeError, match="temporary queue failure"):
            await processor.handle_sms(**arguments)
        failed_event = (await session.exec(select(IntegrationEventRecord))).one()
        assert failed_event.status == IntegrationEventStatus.FAILED.value
        assert failed_event.error_message == "Inbound SMS reply processing failed."

        result = await processor.handle_sms(**arguments)
        event = (await session.exec(select(IntegrationEventRecord))).one()
        runs = (await session.exec(select(AgentRunRecord))).all()
        replies = (await session.exec(select(IntegrationReplyRecord))).all()

    assert result["agent_run_id"] == runs[0].id
    assert event.status == IntegrationEventStatus.PROCESSED.value
    assert attempts == 2
    assert len(runs) == 1
    assert len(replies) == 1


@pytest.mark.asyncio
async def test_telnyx_signed_inbound_voice_answers_and_starts_call_session(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    readiness_checks: list[str] = []
    worker_requests: list[str] = []
    completed_runtime_ids: list[str] = []

    async def request_worker(
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        worker_requests.append(runtime_session_id)
        session.add(
            VoiceCallDemandRecord(
                user_id=user_id,
                agent_id=agent_id,
                runtime_session_id=runtime_session_id,
                room_name=room_name,
                source=source,
                project_key="c" * 64,
                expires_at=utc_now() + timedelta(minutes=1),
            ),
        )
        await session.flush()

    async def wait_for_worker(*, user_id: str, runtime_session_id: str) -> None:
        async with tenant_scoped_session_factory(
            sqlite_session_factory,
            user_id,
        )() as independent_session:
            demand = (
                await independent_session.exec(
                    select(VoiceCallDemandRecord).where(
                        col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    ),
                )
            ).one()
        assert demand.user_id == user_id
        assert provider.answered_calls == []
        readiness_checks.append(runtime_session_id)

    async def complete_worker(
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
    ) -> None:
        del session
        assert user_id == "user-1"
        completed_runtime_ids.append(runtime_session_id)

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_requester=request_worker,
            voice_worker_readiness_waiter=wait_for_worker,
            voice_worker_demand_completer=complete_worker,
        )
        await processor.handle_voice(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=signature(private_key=private_key, timestamp=timestamp, body=body),
            timestamp=timestamp,
        )
        call = (
            await session.exec(
                select(IntegrationCallSessionRecord).where(
                    col(IntegrationCallSessionRecord.provider) == IntegrationProviderType.TELNYX.value,
                ),
            )
        ).one()
        hangup_body = telnyx_voice_body(
            event_id="voice-event-hangup",
            event_type="call.hangup",
        )
        await processor.handle_voice(
            route_context=route_context(route),
            body=hangup_body,
            payload=orjson.loads(hangup_body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=hangup_body,
            ),
            timestamp=timestamp,
        )

    assert call.external_call_id == "v3:call-control-id"
    assert call.direction == IntegrationDirection.INBOUND.value
    assert call.status == IntegrationCallStatus.COMPLETED.value
    assert call.answered_at is not None
    assert call.ended_at is not None
    assert readiness_checks == [call.runtime_session_id]
    assert worker_requests == [call.runtime_session_id]
    assert completed_runtime_ids == [call.runtime_session_id]
    assert provider.answered_calls[0]["call_control_id"] == "v3:call-control-id"
    assert provider.answered_calls[0]["stream_url"].startswith("wss://api.example.com/")


@pytest.mark.asyncio
async def test_telnyx_voice_missing_livekit_fails_before_answering_call(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async def missing_livekit_requester(
        *,
        session: AsyncSession,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
        source: str,
    ) -> None:
        del session, user_id, agent_id, runtime_session_id, room_name, source
        raise MissingLiveKitCredentialsError

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_requester=missing_livekit_requester,
        )

        with pytest.raises(MissingLiveKitCredentialsError):
            await processor.handle_voice(
                route_context=route_context(route),
                body=body,
                payload=orjson.loads(body),
                signature=signature(private_key=private_key, timestamp=timestamp, body=body),
                timestamp=timestamp,
            )
        await session.rollback()
        calls = (await session.exec(select(IntegrationCallSessionRecord))).all()

    assert provider.answered_calls == []
    assert calls == []


@pytest.mark.asyncio
async def test_telnyx_retries_failed_voice_admission_before_answering(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    wait_count = 0

    async def wait_for_worker(*, user_id: str, runtime_session_id: str) -> None:
        nonlocal wait_count
        del user_id, runtime_session_id
        wait_count += 1
        if wait_count == 1:
            raise MissingLiveKitCredentialsError

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            voice_worker_demand_requester=noop_voice_worker_demand_requester,
            voice_worker_readiness_waiter=wait_for_worker,
        )
        request_signature = signature(
            private_key=private_key,
            timestamp=timestamp,
            body=body,
        )

        with pytest.raises(MissingLiveKitCredentialsError):
            await processor.handle_voice(
                route_context=route_context(route),
                body=body,
                payload=orjson.loads(body),
                signature=request_signature,
                timestamp=timestamp,
            )
        call = (await session.exec(select(IntegrationCallSessionRecord))).one()
        assert call.status == IntegrationCallStatus.FAILED.value
        assert call.answered_at is None
        assert call.ended_at is not None
        await processor.handle_voice(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=request_signature,
            timestamp=timestamp,
        )
        await session.refresh(call)

    assert wait_count == 2
    assert len(provider.answered_calls) == 1
    assert call.status == IntegrationCallStatus.IN_PROGRESS.value
    assert call.answered_at is not None
    assert call.ended_at is None


@pytest.mark.asyncio
async def test_telnyx_duplicate_inbound_voice_does_not_answer_twice(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_requester=noop_voice_worker_demand_requester,
            voice_worker_readiness_waiter=noop_voice_worker_readiness_waiter,
        )

        for _ in range(2):
            await processor.handle_voice(
                route_context=route_context(route),
                body=body,
                payload=orjson.loads(body),
                signature=signature(private_key=private_key, timestamp=timestamp, body=body),
                timestamp=timestamp,
            )

        calls = (await session.exec(select(IntegrationCallSessionRecord))).all()
        events = (await session.exec(select(IntegrationEventRecord))).all()

    assert len(provider.answered_calls) == 1
    assert len(calls) == 1
    assert len(events) == 1


@pytest.mark.asyncio
async def test_telnyx_concurrent_inbound_duplicate_is_retryable_until_answered(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    admission_started = asyncio.Event()
    release_admission = asyncio.Event()

    async def wait_for_worker(*, user_id: str, runtime_session_id: str) -> None:
        del user_id, runtime_session_id
        admission_started.set()
        await release_admission.wait()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )

    body = telnyx_voice_body()
    timestamp = str(int(time.time()))
    request_signature = signature(
        private_key=private_key,
        timestamp=timestamp,
        body=body,
    )

    async def process_first_delivery() -> dict[str, object]:
        async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
            return await TelnyxWebhookProcessor(
                session=session,
                secret_vault=user_secret_vault,
                public_base_url=PUBLIC_BASE_URL,
                provider_registry=IntegrationProviderRegistry(telnyx=provider),
                session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
                voice_worker_demand_requester=noop_voice_worker_demand_requester,
                voice_worker_readiness_waiter=wait_for_worker,
            ).handle_voice(
                route_context=route_context(route),
                body=body,
                payload=orjson.loads(body),
                signature=request_signature,
                timestamp=timestamp,
            )

    first_delivery = asyncio.create_task(process_first_delivery())
    await admission_started.wait()
    try:
        async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
            duplicate_processor = TelnyxWebhookProcessor(
                session=session,
                secret_vault=user_secret_vault,
                public_base_url=PUBLIC_BASE_URL,
                provider_registry=IntegrationProviderRegistry(telnyx=provider),
                session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
                voice_worker_demand_requester=noop_voice_worker_demand_requester,
                voice_worker_readiness_waiter=noop_voice_worker_readiness_waiter,
            )
            with pytest.raises(IntegrationUpstreamError, match="still in progress"):
                await duplicate_processor.handle_voice(
                    route_context=route_context(route),
                    body=body,
                    payload=orjson.loads(body),
                    signature=request_signature,
                    timestamp=timestamp,
                )
            in_flight_event = (await session.exec(select(IntegrationEventRecord))).one()
            assert in_flight_event.status == IntegrationEventStatus.PROCESSING.value
            assert in_flight_event.processed_at is None
    finally:
        release_admission.set()

    assert (await first_delivery)["ok"] is True
    assert len(provider.answered_calls) == 1


@pytest.mark.asyncio
async def test_telnyx_stale_inbound_processing_event_is_reclaimed(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        route = await route_for(
            session=session,
            resource_id=resource.id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        stale_received_at = utc_now() - timedelta(
            seconds=TELNYX_EVENT_PROCESSING_LEASE_SECONDS + 1,
        )
        event = IntegrationEventRecord(
            user_id="user-1",
            integration_id=setup.integration.id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX.value,
            event_type="call.initiated",
            direction=IntegrationDirection.INBOUND.value,
            external_event_id="voice-event-1",
            idempotency_key=(f"telnyx:{TELNYX_CONNECTION_ID}:call.initiated:voice-event-1"),
            status=IntegrationEventStatus.PROCESSING.value,
            payload_json={},
            safe_summary_json={},
            received_at=stale_received_at,
            processed_at=None,
        )
        session.add(event)
        await session.commit()
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))

        await TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            voice_worker_demand_requester=noop_voice_worker_demand_requester,
            voice_worker_readiness_waiter=noop_voice_worker_readiness_waiter,
        ).handle_voice(
            route_context=route_context(route),
            body=body,
            payload=orjson.loads(body),
            signature=signature(
                private_key=private_key,
                timestamp=timestamp,
                body=body,
            ),
            timestamp=timestamp,
        )
        await session.refresh(event)
        call = (await session.exec(select(IntegrationCallSessionRecord))).one()

    assert event.status == IntegrationEventStatus.PROCESSED.value
    assert event.received_at > stale_received_at.replace(tzinfo=None)
    assert call.status == IntegrationCallStatus.IN_PROGRESS.value
    assert call.answered_at is not None
    assert len(provider.answered_calls) == 1


@pytest.mark.asyncio
async def test_telnyx_answer_failure_is_transient_and_cancels_voice_demand(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider(fail_answer=True)
    cancellations: list[tuple[str, str]] = []

    async def cancel_worker(
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        del session, user_id
        cancellations.append((runtime_session_id, failure_code))

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup = await telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        route = await route_for(
            session=session,
            resource_id=setup.resources[0].id,
            purpose=IntegrationWebhookPurpose.VOICE,
        )
        body = telnyx_voice_body()
        timestamp = str(int(time.time()))
        processor = TelnyxWebhookProcessor(
            session=session,
            secret_vault=user_secret_vault,
            public_base_url=PUBLIC_BASE_URL,
            provider_registry=IntegrationProviderRegistry(telnyx=provider),
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            voice_worker_demand_requester=noop_voice_worker_demand_requester,
            voice_worker_readiness_waiter=noop_voice_worker_readiness_waiter,
            voice_worker_demand_canceller=cancel_worker,
        )

        with pytest.raises(IntegrationUpstreamError, match=TELNYX_ANSWER_FAILED_ERROR):
            await processor.handle_voice(
                route_context=route_context(route),
                body=body,
                payload=orjson.loads(body),
                signature=signature(private_key=private_key, timestamp=timestamp, body=body),
                timestamp=timestamp,
            )
        call = (await session.exec(select(IntegrationCallSessionRecord))).one()
        event = (await session.exec(select(IntegrationEventRecord))).one()

    assert call.status == IntegrationCallStatus.FAILED.value
    assert call.answered_at is None
    assert call.ended_at is not None
    assert event.status == IntegrationEventStatus.FAILED.value
    assert cancellations == [(call.runtime_session_id, "provider_call_answer_failed")]


@pytest.mark.asyncio
async def test_telnyx_media_stream_waits_for_call_session(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    call_control_id = "v3:call-control-id"
    monkeypatch.setattr(telnyx_media_streams, "STREAM_CALL_SESSION_LOOKUP_ATTEMPTS", 5)
    monkeypatch.setattr(telnyx_media_streams, "STREAM_CALL_SESSION_LOOKUP_DELAY_SECONDS", 0.01)

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await integration_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        insert_task = asyncio.create_task(
            insert_telnyx_call_session_after_delay(
                session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
                integration_id=setup.integration.id,
                resource_id=resource.id,
                call_control_id=call_control_id,
            ),
        )
        bridge = TelnyxMediaStreamBridge(
            service=integration_service,
            audio_bridge_factory=missing_telnyx_audio_bridge,
        )

        call_session = await bridge._call_session(
            user_id="user-1",
            integration_id=setup.integration.id,
            resource_id=resource.id,
            call_control_id=call_control_id,
        )
        inserted = await insert_task

    assert call_session.id == inserted.id
    assert call_session.external_call_id == call_control_id


def test_telnyx_downlink_media_message_is_twenty_millisecond_pcmu() -> None:
    frame = rtc.AudioFrame.create(
        TELNYX_MEDIA_SAMPLE_RATE,
        TELNYX_MEDIA_CHANNELS,
        TELNYX_MEDIA_SAMPLES_PER_FRAME,
    )

    message = orjson.loads(telnyx_downlink_media_message(frame))

    assert message["event"] == "media"
    assert set(message) == {"event", "media"}
    media = message["media"]
    assert isinstance(media, dict)
    payload = media["payload"]
    assert isinstance(payload, str)
    assert len(base64.b64decode(payload)) == TELNYX_MEDIA_PCMU_BYTES_PER_FRAME
    assert TELNYX_MEDIA_FRAME_DURATION_MS == 20


@pytest.mark.parametrize(
    "samples_per_channel",
    [
        TELNYX_MEDIA_SAMPLES_PER_FRAME // 2,
        TELNYX_MEDIA_SAMPLES_PER_FRAME * 2,
    ],
)
def test_telnyx_downlink_rejects_unexpected_frame_size(
    samples_per_channel: int,
) -> None:
    frame = rtc.AudioFrame.create(
        TELNYX_MEDIA_SAMPLE_RATE,
        TELNYX_MEDIA_CHANNELS,
        samples_per_channel,
    )

    with pytest.raises(
        TelnyxMediaDownlinkError,
        match="Telnyx media downlink frame is invalid",
    ):
        telnyx_downlink_media_message(frame)


@pytest.mark.asyncio
async def test_telnyx_uplink_logs_first_valid_pcmu_frame_once_without_sensitive_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    websocket = FakeTelnyxWebSocket(headers={})
    session = livekit_telnyx_bridge_session(websocket)
    audio_source = session.audio_source
    encoded_audio = bytes([0xFF]) * TELNYX_MEDIA_PCMU_BYTES_PER_FRAME
    payload = base64.b64encode(encoded_audio).decode()
    caplog.set_level(
        logging.INFO,
        logger="voxos.integrations.providers.telnyx.media_streams",
    )

    await session.publish_telnyx_payload(payload)
    await session.publish_telnyx_payload(payload)

    assert isinstance(audio_source, FakeAudioSourceResource)
    assert len(audio_source.captured_frames) == 2
    records = [record for record in caplog.records if record.getMessage() == "Started Telnyx PCMU media uplink"]
    assert len(records) == 1
    record = records[0]
    assert record.call_session_id == "call-session-1"
    assert record.runtime_session_id == "runtime-session-1"
    assert record.stream_id == "stream-1"
    assert record.codec == TELNYX_MEDIA_CODEC
    assert record.sample_rate == TELNYX_MEDIA_SAMPLE_RATE
    assert record.encoded_frame_bytes == TELNYX_MEDIA_PCMU_BYTES_PER_FRAME
    assert payload not in caplog.text
    assert PHONE_NUMBER not in caplog.text
    assert SENDER_NUMBER not in caplog.text
    assert "route-key" not in caplog.text
    assert "auth-token" not in caplog.text


@pytest.mark.asyncio
async def test_telnyx_downlink_requests_twenty_millisecond_livekit_frames(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured_options: dict[str, object] = {}
    audio_stream = FakeRemoteAudioStream(frames=[telnyx_pcm_frame()])

    def create_audio_stream(**options: object) -> FakeRemoteAudioStream:
        captured_options.update(options)
        return audio_stream

    monkeypatch.setattr(rtc.AudioStream, "from_track", create_audio_stream)
    websocket = FakeTelnyxWebSocket(headers={})
    session = livekit_telnyx_bridge_session(websocket)
    caplog.set_level(
        logging.INFO,
        logger="voxos.integrations.providers.telnyx.media_streams",
    )

    await session._forward_remote_audio(cast(rtc.RemoteAudioTrack, object()))

    assert captured_options["sample_rate"] == TELNYX_MEDIA_SAMPLE_RATE
    assert captured_options["num_channels"] == TELNYX_MEDIA_CHANNELS
    assert captured_options["frame_size_ms"] == TELNYX_MEDIA_FRAME_DURATION_MS
    assert len(websocket.sent_texts) == 1
    message = orjson.loads(websocket.sent_texts[0])
    assert len(base64.b64decode(message["media"]["payload"])) == (TELNYX_MEDIA_PCMU_BYTES_PER_FRAME)
    assert audio_stream.closed is True
    assert "Started Telnyx PCMU media downlink" in caplog.text


@pytest.mark.asyncio
async def test_telnyx_downlink_treats_websocket_disconnect_as_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_stream = FakeRemoteAudioStream(frames=[telnyx_pcm_frame()])
    monkeypatch.setattr(
        rtc.AudioStream,
        "from_track",
        lambda **_options: audio_stream,
    )
    websocket = FakeTelnyxWebSocket(
        headers={},
        send_error=WebSocketDisconnect(),
    )
    session = livekit_telnyx_bridge_session(websocket)

    await session._forward_remote_audio(cast(rtc.RemoteAudioTrack, object()))

    assert audio_stream.closed is True
    assert session.downlink_failures.empty()


@pytest.mark.asyncio
async def test_telnyx_downlink_task_reports_unexpected_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_stream = FakeRemoteAudioStream(frames=[telnyx_pcm_frame()])
    monkeypatch.setattr(
        rtc.AudioStream,
        "from_track",
        lambda **_options: audio_stream,
    )
    websocket = FakeTelnyxWebSocket(
        headers={},
        send_error=RuntimeError("send failed"),
    )
    session = livekit_telnyx_bridge_session(websocket)
    task = asyncio.create_task(
        session._forward_remote_audio(cast(rtc.RemoteAudioTrack, object())),
    )
    session.outbound_tasks.add(task)
    task.add_done_callback(session._handle_outbound_task_done)

    with pytest.raises(
        TelnyxMediaDownlinkError,
        match="Telnyx media downlink failed",
    ):
        await session.wait_for_downlink_failure()

    assert task.done()
    assert task not in session.outbound_tasks
    assert audio_stream.closed is True


@pytest.mark.asyncio
async def test_telnyx_downlink_close_is_idempotent() -> None:
    websocket = FakeTelnyxWebSocket(headers={})
    session = livekit_telnyx_bridge_session(websocket)
    room = session.room
    audio_source = session.audio_source

    await session.close()
    await session.close()

    assert isinstance(room, FakeRoomResource)
    assert room.disconnect_calls == 1
    assert isinstance(audio_source, FakeAudioSourceResource)
    assert audio_source.close_calls == 1


@pytest.mark.asyncio
async def test_telnyx_downlink_disconnects_room_when_audio_source_close_fails() -> None:
    websocket = FakeTelnyxWebSocket(headers={})
    room = FakeRoomResource()
    session = LiveKitTelnyxAudioBridgeSession(
        room=cast(rtc.Room, room),
        audio_source=cast(
            rtc.AudioSource,
            FakeAudioSourceResource(close_error=RuntimeError("close failed")),
        ),
        websocket=cast(WebSocket, websocket),
        call_session_id="call-session-1",
        runtime_session_id="runtime-session-1",
        stream_id="stream-1",
    )

    with pytest.raises(RuntimeError, match="close failed"):
        await session.close()

    assert room.disconnect_calls == 1


@pytest.mark.asyncio
async def test_telnyx_media_stream_binds_outbound_reservation_before_dial_returns(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    bridge_session = RecordingTelnyxAudioBridgeSession()
    bridge_adapter = RecordingTelnyxAudioBridgeAdapter(
        session=bridge_session,
    )

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        route = await route_for(
            session=session,
            resource_id=resource.id,
            purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
        )
        credentials = await service.load_telnyx_credentials(
            user_id="user-1",
            integration_id=setup.integration.id,
        )
        call = await service.start_communication_call_session(
            user_id="user-1",
            integration_id=setup.integration.id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX,
            direction=IntegrationDirection.OUTBOUND,
            call_sid="reservation-outbound-call",
            from_number=PHONE_NUMBER,
            to_number=SENDER_NUMBER,
            payload_json={"reservation_id": "reservation-outbound-call"},
            agent_id="agent-1",
            initial_status=IntegrationCallStatus.INITIATING,
            answered=False,
        )
        await session.commit()
        websocket = FakeTelnyxWebSocket(
            headers={
                TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME: (credentials.stream_auth_token.get_secret_value()),
            },
            messages=[
                {
                    "event": "start",
                    "stream_id": "stream-outbound-1",
                    "start": {
                        "stream_id": "stream-outbound-1",
                        "call_control_id": "v3:call-control-id",
                    },
                },
                {
                    "event": "stop",
                    "stream_id": "stream-outbound-1",
                },
            ],
        )

        await TelnyxMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate_recording_bridge(
                bridge_adapter,
            ),
        ).handle_websocket(
            websocket=websocket,
            route_context=route_context(route),
            outbound_call_session_id=call.id,
        )
        await session.refresh(call)
        calls = (
            await session.exec(
                select(IntegrationCallSessionRecord).where(
                    col(IntegrationCallSessionRecord.user_id) == "user-1",
                ),
            )
        ).all()

    assert len(calls) == 1
    assert calls[0].id == call.id
    assert calls[0].external_call_id == "v3:call-control-id"
    assert calls[0].status == IntegrationCallStatus.INITIATING.value
    assert calls[0].answered_at is None
    assert calls[0].media_ready_at is not None
    assert calls[0].media_stream_id == "stream-outbound-1"
    assert bridge_adapter.connected_call_ids == [call.id]
    assert bridge_session.closed is True


@pytest.mark.asyncio
async def test_telnyx_media_bridge_start_failure_cancels_voice_demand(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    cancellations: list[tuple[str, str]] = []

    async def cancel_worker(
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        del session, user_id
        cancellations.append((runtime_session_id, failure_code))

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        setup_service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        setup = await setup_service.setup_telnyx_phone(
            user_id="user-1",
            request=telnyx_request(public_key=public_key_hex(private_key)),
        )
        resource = setup.resources[0]
        route = await route_for(
            session=session,
            resource_id=resource.id,
            purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
        )
        credentials = await setup_service.load_telnyx_credentials(
            user_id="user-1",
            integration_id=setup.integration.id,
        )
        call_session = IntegrationCallSessionRecord(
            user_id="user-1",
            integration_id=setup.integration.id,
            resource_id=resource.id,
            provider=IntegrationProviderType.TELNYX.value,
            direction=IntegrationDirection.INBOUND.value,
            external_call_id="v3:call-control-id",
            from_number=SENDER_NUMBER,
            to_number=PHONE_NUMBER,
            status=IntegrationCallStatus.IN_PROGRESS.value,
            agent_id="agent-1",
            runtime_session_id="runtime-session-1",
            livekit_room_name="telnyx-call",
            payload_json={},
        )
        session.add(call_session)
        await session.commit()
        service = telnyx_service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_worker_demand_canceller=cancel_worker,
        )
        websocket = FakeTelnyxWebSocket(
            headers={
                TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME: credentials.stream_auth_token.get_secret_value(),
            },
            messages=[
                {
                    "event": "start",
                    "stream_id": "stream-1",
                    "start": {
                        "stream_id": "stream-1",
                        "call_control_id": "v3:call-control-id",
                    },
                },
            ],
        )
        bridge = TelnyxMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate_telnyx_bridge(
                FailingTelnyxAudioBridgeAdapter(),
            ),
        )

        with pytest.raises(RuntimeError, match="bridge connection failed"):
            await bridge.handle_websocket(
                websocket=websocket,
                route_context=route_context(route),
            )
        await session.refresh(call_session)

    assert call_session.status == IntegrationCallStatus.FAILED.value
    assert call_session.ended_at is not None
    assert cancellations == [("runtime-session-1", "media_bridge_start_failed")]


@pytest.mark.parametrize(
    ("protocol_error_code", "protocol_error_title"),
    [
        (100004, "invalid_media"),
        (100005, "rate_limit_reached"),
    ],
)
@pytest.mark.asyncio
async def test_telnyx_media_protocol_error_fails_only_call_and_is_sanitized(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    caplog: pytest.LogCaptureFixture,
    protocol_error_code: int,
    protocol_error_title: str,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    cancellations: list[tuple[str, str]] = []

    async def cancel_worker(
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        del session, user_id
        cancellations.append((runtime_session_id, failure_code))

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        service, route, credentials, call_session = await prepared_telnyx_media_call(
            session=session,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
            private_key=private_key,
            cancel_worker=cancel_worker,
        )
        bridge_session = RecordingTelnyxAudioBridgeSession()
        websocket = FakeTelnyxWebSocket(
            headers={
                TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME: (credentials.stream_auth_token.get_secret_value()),
            },
            messages=[
                telnyx_start_frame(),
                {
                    "event": "error",
                    "stream_id": "stream-1",
                    "payload": {
                        "code": protocol_error_code,
                        "title": protocol_error_title,
                        "detail": PROTOCOL_DETAIL_MARKER,
                    },
                },
            ],
        )
        caplog.set_level(
            logging.WARNING,
            logger="voxos.integrations.providers.telnyx.media_streams",
        )

        await TelnyxMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate_recording_bridge(
                RecordingTelnyxAudioBridgeAdapter(session=bridge_session),
            ),
        ).handle_websocket(
            websocket=cast(WebSocket, websocket),
            route_context=route_context(route),
        )
        await session.refresh(call_session)
        error_event = (
            await session.exec(
                select(IntegrationEventRecord).where(
                    col(IntegrationEventRecord.event_type) == "media_stream_error",
                ),
            )
        ).one()

    assert call_session.status == IntegrationCallStatus.FAILED.value
    assert cancellations == [
        ("runtime-session-1", "media_stream_protocol_error"),
    ]
    assert websocket.closed_codes == [1011]
    assert bridge_session.closed is True
    assert error_event.payload_json == {
        "event": "error",
        "stream_id": "stream-1",
        "code": protocol_error_code,
        "title": protocol_error_title,
    }
    assert PROTOCOL_DETAIL_MARKER not in caplog.text
    protocol_records = [
        record for record in caplog.records if record.getMessage() == "Telnyx media stream reported a protocol error"
    ]
    assert len(protocol_records) == 1
    assert protocol_records[0].__dict__["protocol_error_code"] == protocol_error_code
    assert protocol_records[0].__dict__["protocol_error_title"] == protocol_error_title


@pytest.mark.asyncio
async def test_telnyx_media_downlink_failure_is_call_scoped(
    sqlite_session_factory: AsyncSessionFactory,
    user_secret_vault: UserSecretVaultService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    provider = FakeTelnyxProvider()
    cancellations: list[tuple[str, str]] = []

    async def cancel_worker(
        *,
        session: AsyncSession,
        user_id: str,
        runtime_session_id: str,
        failure_code: str,
    ) -> None:
        del session, user_id
        cancellations.append((runtime_session_id, failure_code))

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        service, route, credentials, call_session = await prepared_telnyx_media_call(
            session=session,
            session_factory=sqlite_session_factory,
            secret_vault=user_secret_vault,
            provider=provider,
            private_key=private_key,
            cancel_worker=cancel_worker,
        )
        bridge_session = RecordingTelnyxAudioBridgeSession(
            downlink_failure=TelnyxMediaDownlinkError(
                "Telnyx media downlink failed.",
            ),
        )
        websocket = FakeTelnyxWebSocket(
            headers={
                TELNYX_STREAM_AUTH_TOKEN_HEADER_NAME: (credentials.stream_auth_token.get_secret_value()),
            },
            messages=[telnyx_start_frame()],
            block_when_empty=True,
        )
        caplog.set_level(
            logging.ERROR,
            logger="voxos.integrations.providers.telnyx.media_streams",
        )

        await TelnyxMediaStreamBridge(
            service=service,
            audio_bridge_factory=lambda _user_id: immediate_recording_bridge(
                RecordingTelnyxAudioBridgeAdapter(session=bridge_session),
            ),
        ).handle_websocket(
            websocket=cast(WebSocket, websocket),
            route_context=route_context(route),
        )
        await session.refresh(call_session)

    assert call_session.status == IntegrationCallStatus.FAILED.value
    assert cancellations == [("runtime-session-1", "media_downlink_failed")]
    assert websocket.closed_codes == [1011]
    assert bridge_session.closed is True
    assert "Telnyx media downlink failed" in caplog.text


@pytest.mark.asyncio
async def test_telnyx_provider_rejects_invalid_webhook_public_key_before_api_call() -> None:
    with pytest.raises(TelnyxProviderError, match=TELNYX_PUBLIC_KEY_INVALID_ERROR):
        await TelnyxIntegrationProvider().validate_credentials(
            TelnyxCredentialBundle(
                api_key=SecretStr("telnyx-secret"),
                webhook_public_key="not-a-valid-public-key",
                stream_auth_token=SecretStr("stream-token"),
            ),
        )


def test_telnyx_provider_detects_number_capabilities_before_provisioning() -> None:
    voice_only_capabilities = telnyx_number_capabilities(
        number={"status": "active"},
        messaging_number={
            "features": {
                "sms": {
                    "domestic_two_way": False,
                    "international_inbound": False,
                    "international_outbound": False,
                },
            },
        },
    )
    two_way_capabilities = telnyx_number_capabilities(
        number={"status": "active"},
        messaging_number={"features": {"sms": {"domestic_two_way": True}}},
    )

    assert IntegrationCapability.SMS_READ not in voice_only_capabilities
    assert IntegrationCapability.SMS_SEND not in voice_only_capabilities
    with pytest.raises(TelnyxPhoneNumberCapabilityError):
        ensure_required_phone_number_capabilities(voice_only_capabilities)
    ensure_required_phone_number_capabilities(two_way_capabilities)


@pytest.mark.asyncio
async def test_telnyx_provider_routes_outbound_status_only_to_per_message_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send_options: list[dict[str, object]] = []

    class FakeMessages:
        async def send(self, **options: object) -> object:
            send_options.append(options)
            return {
                "data": {
                    "id": "message-id",
                    "to": [{"status": "queued"}],
                },
            }

    class FakeClient:
        messages = FakeMessages()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "_client",
        lambda _self, _credentials: FakeClient(),
    )

    await TelnyxIntegrationProvider().send_sms(
        credentials=TelnyxCredentialBundle(
            api_key=SecretStr("telnyx-secret"),
            webhook_public_key="unused-for-send",
            stream_auth_token=SecretStr("stream-token"),
            messaging_profile_id=TELNYX_MESSAGING_PROFILE_ID,
        ),
        from_number=PHONE_NUMBER,
        to_number=SENDER_NUMBER,
        body="hello",
        status_callback_url=f"{PUBLIC_BASE_URL}/status",
    )

    assert send_options[0]["webhook_url"] == f"{PUBLIC_BASE_URL}/status"
    assert send_options[0]["use_profile_webhooks"] is False


@pytest.mark.asyncio
async def test_telnyx_provider_repair_is_idempotent_when_routing_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_calls: list[tuple[str, str, dict[str, object]]] = []
    voice_url = f"{PUBLIC_BASE_URL}/voice"
    sms_url = f"{PUBLIC_BASE_URL}/sms"

    class FakeMessagingNumbers:
        async def retrieve(self, resource_id: str) -> object:
            assert resource_id == "telnyx-number-id"
            return {
                "data": {
                    "id": resource_id,
                    "messaging_profile_id": TELNYX_MESSAGING_PROFILE_ID,
                    "features": {
                        "sms": {
                            "domestic_two_way": True,
                            "international_inbound": False,
                            "international_outbound": False,
                        },
                    },
                },
            }

        async def update(self, resource_id: str, **options: object) -> object:
            update_calls.append(("number_messaging", resource_id, options))
            return {"data": {"id": resource_id, **options}}

    class FakePhoneNumbers:
        messaging = FakeMessagingNumbers()

        async def retrieve(self, resource_id: str) -> object:
            return {
                "data": {
                    "id": resource_id,
                    "phone_number": PHONE_NUMBER,
                    "status": "active",
                    "connection_id": TELNYX_CONNECTION_ID,
                },
            }

        async def update(self, resource_id: str, **options: object) -> object:
            update_calls.append(("number", resource_id, options))
            return {"data": {"id": resource_id, **options}}

    class FakeVoiceApplications:
        async def retrieve(self, resource_id: str) -> object:
            return {
                "data": {
                    "id": resource_id,
                    "active": True,
                    "application_name": "Voxos Telnyx 6704",
                    "outbound": {"outbound_voice_profile_id": "outbound-profile-id"},
                    "webhook_api_version": "2",
                    "webhook_event_url": voice_url,
                },
            }

        async def update(self, resource_id: str, **options: object) -> object:
            update_calls.append(("voice_application", resource_id, options))
            return {"data": {"id": resource_id, **options}}

    class FakeMessagingProfiles:
        async def retrieve(self, resource_id: str) -> object:
            return {
                "data": {
                    "id": resource_id,
                    "enabled": True,
                    "name": "Voxos Telnyx 6704",
                    "webhook_api_version": "2",
                    "webhook_url": sms_url,
                    "whitelisted_destinations": ["GB"],
                },
            }

        async def update(self, resource_id: str, **options: object) -> object:
            update_calls.append(("messaging_profile", resource_id, options))
            return {"data": {"id": resource_id, **options}}

    class FakeOutboundProfiles:
        async def retrieve(self, resource_id: str) -> object:
            return {
                "data": {
                    "id": resource_id,
                    "enabled": True,
                    "name": "Voxos Telnyx 6704",
                    "whitelisted_destinations": ["GB"],
                },
            }

        async def update(self, resource_id: str, **options: object) -> object:
            update_calls.append(("outbound_profile", resource_id, options))
            return {"data": {"id": resource_id, **options}}

    class FakeClient:
        phone_numbers = FakePhoneNumbers()
        call_control_applications = FakeVoiceApplications()
        messaging_profiles = FakeMessagingProfiles()
        outbound_voice_profiles = FakeOutboundProfiles()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "_client",
        lambda _self, _credentials: FakeClient(),
    )
    credentials = TelnyxCredentialBundle(
        api_key=SecretStr("telnyx-secret"),
        webhook_public_key="unused-for-repair",
        stream_auth_token=SecretStr("stream-token"),
        connection_id=TELNYX_CONNECTION_ID,
        messaging_profile_id=TELNYX_MESSAGING_PROFILE_ID,
    )

    configuration = await TelnyxIntegrationProvider().repair_phone_number_configuration(
        credentials=credentials,
        phone_number_id="telnyx-number-id",
        outbound_voice_profile_id="outbound-profile-id",
        label="Telnyx",
        expected_voice_url=voice_url,
        expected_sms_url=sms_url,
    )

    assert configuration.connection_assignment_matches is True
    assert configuration.messaging_assignment_matches is True
    assert configuration.outbound_voice_profile_assignment_matches is True
    assert update_calls == []


@pytest.mark.asyncio
async def test_telnyx_provider_starts_bidirectional_pcmu_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_calls: list[tuple[str, dict[str, object]]] = []
    streaming_calls: list[tuple[str, dict[str, object]]] = []

    class FakeActions:
        async def answer(self, call_control_id: str, **options: object) -> object:
            answer_calls.append((call_control_id, options))
            return {"data": {"result": "answered"}}

        async def start_streaming(
            self,
            call_control_id: str,
            **options: object,
        ) -> object:
            streaming_calls.append((call_control_id, options))
            return {"data": {"result": "streaming"}}

    class FakeCalls:
        actions = FakeActions()

    class FakeClient:
        calls = FakeCalls()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_client = FakeClient()
    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "_client",
        lambda _self, _credentials: fake_client,
    )

    response = await TelnyxIntegrationProvider().answer_call(
        credentials=TelnyxCredentialBundle(
            api_key=SecretStr("telnyx-secret"),
            webhook_public_key="unused-for-answer",
            stream_auth_token=SecretStr("stream-token"),
        ),
        call_control_id="call-control-id",
        stream_url="wss://api.example.com/telnyx/media",
        webhook_url="https://api.example.com/telnyx/status",
        command_id="command-id",
    )

    assert answer_calls == [
        (
            "call-control-id",
            {
                "command_id": "command-id",
                "send_silence_when_idle": True,
                "webhook_url": "https://api.example.com/telnyx/status",
                "webhook_url_method": "POST",
            },
        ),
    ]
    assert streaming_calls == [
        (
            "call-control-id",
            {
                "command_id": "stream-command-id",
                "stream_auth_token": "stream-token",
                "stream_bidirectional_codec": "PCMU",
                "stream_bidirectional_mode": "rtp",
                "stream_bidirectional_sampling_rate": 8000,
                "stream_bidirectional_target_legs": "self",
                "stream_codec": "PCMU",
                "stream_track": "inbound_track",
                "stream_url": "wss://api.example.com/telnyx/media",
            },
        ),
    ]
    assert response.status == "streaming"


@pytest.mark.asyncio
async def test_telnyx_provider_answers_and_transcribes_verification_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer_calls: list[tuple[str, dict[str, object]]] = []
    transcription_calls: list[tuple[str, dict[str, object]]] = []

    class FakeActions:
        async def answer(self, call_control_id: str, **options: object) -> object:
            answer_calls.append((call_control_id, options))
            return {"data": {"result": "answered"}}

        async def start_transcription(
            self,
            call_control_id: str,
            **options: object,
        ) -> object:
            transcription_calls.append((call_control_id, options))
            return {"data": {"result": "transcribing"}}

    class FakeCalls:
        actions = FakeActions()

    class FakeClient:
        calls = FakeCalls()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "_client",
        lambda _self, _credentials: FakeClient(),
    )

    response = await TelnyxIntegrationProvider().answer_verification_call(
        credentials=TelnyxCredentialBundle(
            api_key=SecretStr("telnyx-secret"),
            webhook_public_key="unused-for-answer",
            stream_auth_token=SecretStr("stream-token"),
        ),
        call_control_id="call-control-id",
        webhook_url="https://api.example.com/telnyx/status",
        command_id="command-id",
    )

    assert answer_calls == [
        (
            "call-control-id",
            {
                "command_id": "command-id",
                "send_silence_when_idle": True,
                "webhook_url": "https://api.example.com/telnyx/status",
                "webhook_url_method": "POST",
            },
        ),
    ]
    assert transcription_calls == [
        (
            "call-control-id",
            {
                "command_id": "transcribe-command-id",
                "transcription_engine": "Telnyx",
                "transcription_tracks": "inbound",
            },
        ),
    ]
    assert response.status == "transcribing"


@pytest.mark.asyncio
async def test_telnyx_provider_sends_call_dtmf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dtmf_calls: list[tuple[str, dict[str, object]]] = []

    class FakeActions:
        async def send_dtmf(
            self,
            call_control_id: str,
            **options: object,
        ) -> object:
            dtmf_calls.append((call_control_id, options))
            return {"data": {"result": "ok"}}

    class FakeCalls:
        actions = FakeActions()

    class FakeClient:
        calls = FakeCalls()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        TelnyxIntegrationProvider,
        "_client",
        lambda _self, _credentials: FakeClient(),
    )

    response = await TelnyxIntegrationProvider().send_call_dtmf(
        credentials=TelnyxCredentialBundle(
            api_key=SecretStr("telnyx-secret"),
            webhook_public_key="unused-for-dtmf",
            stream_auth_token=SecretStr("stream-token"),
        ),
        call_control_id="call-control-id",
        digits="6",
        command_id="command-id",
    )

    assert dtmf_calls == [
        (
            "call-control-id",
            {"digits": "6", "command_id": "command-id"},
        ),
    ]
    assert response.status == "ok"


@pytest.mark.asyncio
async def test_telnyx_setup_maps_invalid_webhook_public_key_to_credential_error(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        with pytest.raises(IntegrationCredentialError):
            await UserIntegrationService(
                session=session,
                secret_vault=user_secret_vault,
                public_base_url=PUBLIC_BASE_URL,
                provider_registry=IntegrationProviderRegistry(telnyx=TelnyxIntegrationProvider()),
                session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            ).setup_telnyx_phone(
                user_id="user-1",
                request=telnyx_request(public_key="not-a-valid-public-key"),
            )


def telnyx_service(
    *,
    session,
    session_factory,
    secret_vault: UserSecretVaultService,
    provider: FakeTelnyxProvider,
    public_base_url: str = PUBLIC_BASE_URL,
    voice_call_preflight: VoiceCallPreflight | None = None,
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None,
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None,
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None,
) -> UserIntegrationService:
    return UserIntegrationService(
        session=session,
        secret_vault=secret_vault,
        public_base_url=public_base_url,
        provider_registry=IntegrationProviderRegistry(telnyx=provider),
        session_factory=session_factory,
        voice_call_preflight=voice_call_preflight or noop_voice_call_preflight,
        voice_worker_demand_requester=(voice_worker_demand_requester or noop_voice_worker_demand_requester),
        voice_worker_readiness_waiter=(voice_worker_readiness_waiter or noop_voice_worker_readiness_waiter),
        voice_agent_dispatcher=noop_voice_agent_dispatcher,
        voice_agent_readiness_waiter=noop_voice_worker_readiness_waiter,
        voice_worker_demand_canceller=voice_worker_demand_canceller,
    )


def telnyx_request(public_key: str) -> TelnyxPhoneSetupRequest:
    return TelnyxPhoneSetupRequest(
        label="Telnyx",
        phone_number=PHONE_NUMBER,
        api_key=SecretStr("telnyx-secret"),
        webhook_public_key=public_key,
        confirm_number_assignment=True,
    )


def ready_telnyx_configuration(
    *,
    phone_number_id: str = "telnyx-number-id",
) -> TelnyxPhoneNumberConfiguration:
    return TelnyxPhoneNumberConfiguration(
        phone_number_id=phone_number_id,
        phone_number_status="active",
        capabilities=(
            IntegrationCapability.INBOUND_VOICE,
            IntegrationCapability.OUTBOUND_VOICE,
            IntegrationCapability.SMS_READ,
            IntegrationCapability.SMS_SEND,
            IntegrationCapability.MEDIA_STREAM,
            IntegrationCapability.TOOL_ACTIONS,
        ),
        connection_id=TELNYX_CONNECTION_ID,
        messaging_profile_id=TELNYX_MESSAGING_PROFILE_ID,
        outbound_voice_profile_id="outbound-profile-id",
        connection_assignment_matches=True,
        messaging_assignment_matches=True,
        outbound_voice_profile_assignment_matches=True,
        voice_application_found=True,
        voice_application_active=True,
        voice_webhook_matches=True,
        voice_webhook_api_version="2",
        messaging_profile_found=True,
        messaging_profile_enabled=True,
        messaging_webhook_matches=True,
        messaging_webhook_api_version="2",
        outbound_voice_profile_found=True,
        outbound_voice_profile_enabled=True,
        messaging_destinations=("GB",),
        voice_destinations=("GB",),
    )


def outbound_sms_request(
    *,
    integration_id: str,
    resource_id: str,
) -> CommunicationOutboundSmsRequest:
    return CommunicationOutboundSmsRequest(
        integration_id=integration_id,
        resource_id=resource_id,
        agent_id="agent-1",
        to_number=SENDER_NUMBER,
        body="Hello",
    )


def outbound_call_request(
    *,
    integration_id: str,
    resource_id: str,
) -> CommunicationOutboundCallRequest:
    return CommunicationOutboundCallRequest(
        integration_id=integration_id,
        resource_id=resource_id,
        agent_id="agent-1",
        to_number=SENDER_NUMBER,
        objective="Introduce yourself and share the update.",
    )


async def route_for(
    *,
    session,
    resource_id: str,
    purpose: IntegrationWebhookPurpose,
) -> IntegrationWebhookRouteRecord:
    result = await session.exec(
        select(IntegrationWebhookRouteRecord).where(
            col(IntegrationWebhookRouteRecord.resource_id) == resource_id,
            col(IntegrationWebhookRouteRecord.purpose) == purpose.value,
            col(IntegrationWebhookRouteRecord.status) == IntegrationRouteStatus.ACTIVE.value,
        ),
    )
    return result.one()


def route_context(route: IntegrationWebhookRouteRecord) -> IntegrationWebhookRouteContext:
    return IntegrationWebhookRouteContext(
        route=route,
        route_key_hash=route.route_key_hash,
    )


def telnyx_sms_body(*, text: str = "hello") -> bytes:
    return orjson.dumps(
        {
            "data": {
                "id": "event-1",
                "event_type": "message.received",
                "payload": {
                    "id": "message-1",
                    "from": {"phone_number": SENDER_NUMBER},
                    "to": [{"phone_number": PHONE_NUMBER}],
                    "text": text,
                },
            },
        },
    )


def telnyx_message_status_body(
    *,
    event_id: str,
    message_id: str,
    status: str,
) -> bytes:
    return orjson.dumps(
        {
            "data": {
                "id": event_id,
                "event_type": "message.finalized",
                "payload": {
                    "id": message_id,
                    "from": {"phone_number": PHONE_NUMBER},
                    "to": [
                        {
                            "phone_number": SENDER_NUMBER,
                            "status": status,
                        },
                    ],
                    "errors": [
                        {
                            "code": 40001,
                            "title": "Destination blocked",
                            "detail": f"Sensitive detail for {SENDER_NUMBER}",
                        },
                    ],
                },
            },
        },
    )


def telnyx_voice_body(
    *,
    event_id: str = "voice-event-1",
    event_type: str = "call.initiated",
) -> bytes:
    return orjson.dumps(
        {
            "data": {
                "id": event_id,
                "event_type": event_type,
                "payload": {
                    "call_control_id": "v3:call-control-id",
                    "call_session_id": "call-session-id",
                    "direction": "incoming",
                    "from": SENDER_NUMBER,
                    "to": PHONE_NUMBER,
                },
            },
        },
    )


def telnyx_transcription_body(*, event_id: str, transcript: str) -> bytes:
    return orjson.dumps(
        {
            "data": {
                "id": event_id,
                "event_type": "call.transcription",
                "payload": {
                    "call_control_id": "v3:call-control-id",
                    "call_session_id": "call-session-id",
                    "transcription_data": {
                        "confidence": 0.99,
                        "is_final": True,
                        "transcript": transcript,
                    },
                },
            },
        },
    )


def telnyx_outbound_voice_body(*, client_state: str) -> bytes:
    return orjson.dumps(
        {
            "data": {
                "id": "outbound-voice-event-1",
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "v3:call-control-id",
                    "call_session_id": "outbound-call-session-id",
                    "client_state": client_state,
                    "direction": "outgoing",
                    "from": PHONE_NUMBER,
                    "to": SENDER_NUMBER,
                },
            },
        },
    )


def signature(
    *,
    private_key: Ed25519PrivateKey,
    timestamp: str,
    body: bytes,
) -> str:
    signed = private_key.sign(timestamp.encode("utf-8") + b"|" + body)
    return base64.b64encode(signed).decode("ascii")


def public_key_hex(private_key: Ed25519PrivateKey) -> str:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return public_key.hex()


async def insert_telnyx_call_session_after_delay(
    *,
    session_factory,
    integration_id: str,
    resource_id: str,
    call_control_id: str,
) -> IntegrationCallSessionRecord:
    await asyncio.sleep(0.005)
    async with session_factory() as session:
        call_session = IntegrationCallSessionRecord(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource_id,
            provider=IntegrationProviderType.TELNYX.value,
            direction=IntegrationDirection.OUTBOUND.value,
            external_call_id=call_control_id,
            from_number=PHONE_NUMBER,
            to_number=SENDER_NUMBER,
            status=IntegrationCallStatus.INITIATING.value,
            agent_id="agent-1",
            runtime_session_id="runtime-session-1",
            livekit_room_name="telnyx-call",
            payload_json={},
        )
        session.add(call_session)
        await session.commit()
        await session.refresh(call_session)
        return call_session


async def prepared_telnyx_media_call(
    *,
    session: AsyncSession,
    session_factory: AsyncSessionFactory,
    secret_vault: UserSecretVaultService,
    provider: FakeTelnyxProvider,
    private_key: Ed25519PrivateKey,
    cancel_worker: VoiceWorkerDemandCanceller,
) -> tuple[
    UserIntegrationService,
    IntegrationWebhookRouteRecord,
    TelnyxCredentialBundle,
    IntegrationCallSessionRecord,
]:
    tenant_factory = tenant_scoped_session_factory(session_factory, "user-1")
    setup_service = telnyx_service(
        session=session,
        session_factory=tenant_factory,
        secret_vault=secret_vault,
        provider=provider,
    )
    setup = await setup_service.setup_telnyx_phone(
        user_id="user-1",
        request=telnyx_request(public_key=public_key_hex(private_key)),
    )
    resource = setup.resources[0]
    route = await route_for(
        session=session,
        resource_id=resource.id,
        purpose=IntegrationWebhookPurpose.MEDIA_STREAM,
    )
    credentials = await setup_service.load_telnyx_credentials(
        user_id="user-1",
        integration_id=setup.integration.id,
    )
    call_session = IntegrationCallSessionRecord(
        user_id="user-1",
        integration_id=setup.integration.id,
        resource_id=resource.id,
        provider=IntegrationProviderType.TELNYX.value,
        direction=IntegrationDirection.INBOUND.value,
        external_call_id="v3:call-control-id",
        from_number=SENDER_NUMBER,
        to_number=PHONE_NUMBER,
        status=IntegrationCallStatus.IN_PROGRESS.value,
        agent_id="agent-1",
        runtime_session_id="runtime-session-1",
        livekit_room_name="telnyx-call",
        payload_json={},
    )
    session.add(call_session)
    await session.commit()
    return (
        telnyx_service(
            session=session,
            session_factory=tenant_factory,
            secret_vault=secret_vault,
            provider=provider,
            voice_worker_demand_canceller=cancel_worker,
        ),
        route,
        credentials,
        call_session,
    )


def telnyx_start_frame() -> dict[str, object]:
    return {
        "event": "start",
        "stream_id": "stream-1",
        "start": {
            "stream_id": "stream-1",
            "call_control_id": "v3:call-control-id",
        },
    }


async def missing_telnyx_audio_bridge(_: str) -> None:
    return None


async def noop_voice_worker_demand_requester(
    *,
    session: AsyncSession,
    user_id: str,
    agent_id: str,
    runtime_session_id: str,
    room_name: str,
    source: str,
) -> None:
    del session, user_id, agent_id, runtime_session_id, room_name, source


async def noop_voice_call_preflight(*, session: AsyncSession, user_id: str) -> None:
    del session, user_id


async def noop_voice_worker_readiness_waiter(
    *,
    user_id: str,
    runtime_session_id: str,
) -> None:
    del user_id, runtime_session_id


async def noop_voice_agent_dispatcher(
    *,
    user_id: str,
    agent_id: str,
    runtime_session_id: str,
    room_name: str,
) -> None:
    del user_id, agent_id, runtime_session_id, room_name


@dataclass(slots=True)
class FakeTelnyxWebSocket:
    headers: dict[str, str]
    messages: list[dict[str, object]] = field(default_factory=list)
    accepted: bool = False
    closed_codes: list[int] = field(default_factory=list)
    sent_texts: list[str] = field(default_factory=list)
    send_error: BaseException | None = None
    block_when_empty: bool = False

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        self.closed_codes.append(code)

    async def receive_text(self) -> str:
        if not self.messages and self.block_when_empty:
            await asyncio.Future()
        return orjson.dumps(self.messages.pop(0)).decode()

    async def send_text(self, value: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent_texts.append(value)


@dataclass(slots=True)
class FakeRemoteAudioEvent:
    frame: rtc.AudioFrame


@dataclass(slots=True)
class FakeRemoteAudioStream:
    frames: list[rtc.AudioFrame]
    closed: bool = False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> FakeRemoteAudioEvent:
        if not self.frames:
            raise StopAsyncIteration
        return FakeRemoteAudioEvent(frame=self.frames.pop(0))

    async def aclose(self) -> None:
        self.closed = True


@dataclass(slots=True)
class FakeRoomResource:
    disconnect_calls: int = 0

    async def disconnect(self) -> None:
        self.disconnect_calls += 1


@dataclass(slots=True)
class FakeAudioSourceResource:
    close_calls: int = 0
    close_error: BaseException | None = None
    captured_frames: list[rtc.AudioFrame] = field(default_factory=list)

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        self.captured_frames.append(frame)

    async def aclose(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


def telnyx_pcm_frame() -> rtc.AudioFrame:
    return rtc.AudioFrame.create(
        TELNYX_MEDIA_SAMPLE_RATE,
        TELNYX_MEDIA_CHANNELS,
        TELNYX_MEDIA_SAMPLES_PER_FRAME,
    )


def livekit_telnyx_bridge_session(
    websocket: FakeTelnyxWebSocket,
) -> LiveKitTelnyxAudioBridgeSession:
    return LiveKitTelnyxAudioBridgeSession(
        room=cast(rtc.Room, FakeRoomResource()),
        audio_source=cast(rtc.AudioSource, FakeAudioSourceResource()),
        websocket=cast(WebSocket, websocket),
        call_session_id="call-session-1",
        runtime_session_id="runtime-session-1",
        stream_id="stream-1",
    )


@dataclass(slots=True)
class FailingTelnyxAudioBridgeAdapter:
    async def connect(
        self,
        *,
        websocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TelnyxAudioBridgeSession:
        del websocket, call_session, user_id
        raise RuntimeError(BRIDGE_CONNECTION_FAILED_ERROR)


@dataclass(slots=True)
class RecordingTelnyxAudioBridgeSession:
    closed: bool = False
    published_payloads: list[str] = field(default_factory=list)
    downlink_failure: BaseException | None = None

    async def publish_telnyx_payload(self, payload: str) -> None:
        self.published_payloads.append(payload)

    async def close(self) -> None:
        self.closed = True

    async def wait_for_downlink_failure(self) -> None:
        if self.downlink_failure is not None:
            raise self.downlink_failure
        await asyncio.Future()


@dataclass(slots=True)
class RecordingTelnyxAudioBridgeAdapter:
    session: RecordingTelnyxAudioBridgeSession
    connected_call_ids: list[str] = field(default_factory=list)

    async def connect(
        self,
        *,
        websocket,
        call_session: IntegrationCallSessionRecord,
        user_id: str,
    ) -> TelnyxAudioBridgeSession:
        del websocket, user_id
        self.connected_call_ids.append(call_session.id)
        return self.session


async def immediate_telnyx_bridge(
    adapter: FailingTelnyxAudioBridgeAdapter,
) -> FailingTelnyxAudioBridgeAdapter:
    return adapter


async def immediate_recording_bridge(
    adapter: RecordingTelnyxAudioBridgeAdapter,
) -> RecordingTelnyxAudioBridgeAdapter:
    return adapter

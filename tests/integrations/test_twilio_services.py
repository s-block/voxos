from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.agent_identity.services import AgentIdentityService
from voxos.agent_runtime.records import AgentToolGrantRecord
from voxos.agent_runtime.tool_authorization import SqlModelToolAuthorizationService
from voxos.agent_runtime.tool_policy import (
    ToolGrantScope,
    ToolGrantStatus,
    ToolGrantUpdate,
    ToolRisk,
)
from voxos.contacts.models import ContactCreateRequest, ContactUpdateRequest
from voxos.contacts.services import UserContactService
from voxos.credentials.models import SecretPurpose
from voxos.credentials.records import UserSecretRecord
from voxos.credentials.vault import UserSecretVaultService
from voxos.db.models import utc_now
from voxos.db.tenant import tenant_scoped_session_factory
from voxos.integrations.errors import IntegrationCredentialError, IntegrationResourceError
from voxos.integrations.models import (
    IntegrationActionResponse,
    IntegrationAuthType,
    IntegrationCapability,
    IntegrationCredentialStatus,
    IntegrationDirection,
    IntegrationProviderType,
    IntegrationRouteStatus,
    IntegrationStatus,
    TwilioCredentialSetupRequest,
    TwilioOutboundCallRequest,
    TwilioOutboundSmsRequest,
    TwilioPhoneNumberCandidate,
    TwilioResourceConnectRequest,
    TwilioResourceSelection,
)
from voxos.integrations.provider_registry import IntegrationProviderRegistry
from voxos.integrations.providers.base import (
    IntegrationCredentialValidation,
    IntegrationProviderDefinition,
    TwilioCredentialBundle,
)
from voxos.integrations.providers.twilio.provider import TwilioProviderError
from voxos.integrations.records import (
    IntegrationCallSessionRecord,
    IntegrationCallTranscriptRecord,
    IntegrationCredentialRecord,
    IntegrationMessageRecord,
    IntegrationResourceRecord,
    IntegrationWebhookRouteRecord,
    UserIntegrationRecord,
)
from voxos.integrations.services import (
    INTEGRATION_LABEL_EXISTS_ERROR,
    TWILIO_CAPABILITY_UNSUPPORTED_ERROR,
    UserIntegrationService,
    VoiceAgentDispatcher,
    VoiceAgentReadinessWaiter,
    VoiceCallPreflight,
    VoiceWorkerDemandCanceller,
    VoiceWorkerDemandRequester,
    VoiceWorkerReadinessWaiter,
)
from voxos.integrations.tool_names import (
    TWILIO_PLACE_CALL_TOOL_NAME,
    TWILIO_SEND_SMS_TOOL_NAME,
    provider_integration_tool_name,
)
from voxos.voice.demand import VoiceCallDemandRecord
from voxos.voice.errors import MissingLiveKitCredentialsError

ACCOUNT_SID = "AC" + "0" * 32
API_KEY_SID = "SK" + "1" * 32  # pragma: allowlist secret
PHONE_NUMBER_SID = "PN" + "2" * 32
PHONE_NUMBER = "+15551234567"
VALID_WEBHOOK_SIGNING_VALUE = "auth-value"
SEEDED_TWILIO_NUMBER_MISSING = "expected seeded Twilio phone number"
TWILIO_TEST_SEND_FAILURE = "Twilio test send failure."
TWILIO_TEST_CALL_FAILURE = "Twilio test call failure."


async def noop_voice_preflight(*, session: AsyncSession, user_id: str) -> None:
    del session, user_id


async def noop_voice_request(
    *,
    session: AsyncSession,
    user_id: str,
    agent_id: str,
    runtime_session_id: str,
    room_name: str,
    source: str,
) -> None:
    del session, user_id, agent_id, runtime_session_id, room_name, source


async def noop_voice_wait(*, user_id: str, runtime_session_id: str) -> None:
    del user_id, runtime_session_id


async def noop_voice_dispatch(
    *,
    user_id: str,
    agent_id: str,
    runtime_session_id: str,
    room_name: str,
) -> None:
    del user_id, agent_id, runtime_session_id, room_name


@dataclass(slots=True)
class TransactionalVoiceDemandRequester:
    session_ids: list[int] = field(default_factory=list)

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
        self.session_ids.append(id(session))
        session.add(
            VoiceCallDemandRecord(
                user_id=user_id,
                agent_id=agent_id,
                runtime_session_id=runtime_session_id,
                room_name=room_name,
                source=source,
                project_key="a" * 64,
                expires_at=utc_now() + timedelta(minutes=1),
            ),
        )
        await session.flush()


@dataclass(slots=True)
class FakeTwilioProvider:
    initial_voice_url: str | None = "https://old.example.com/voice?token=secret"
    initial_sms_url: str | None = "https://old.example.com/sms"
    initial_status_callback: str | None = "https://old.example.com/status"
    valid_auth_token: str = VALID_WEBHOOK_SIGNING_VALUE
    fail_send: bool = False
    fail_call: bool = False
    start_call_count: int = 0
    validate_calls: int = 0
    list_calls: int = 0
    captured_payloads: list[dict[str, list[str]]] = field(default_factory=list)
    started_call_urls: list[tuple[str, str | None]] = field(default_factory=list)
    current_number: TwilioPhoneNumberCandidate | None = None

    def __post_init__(self) -> None:
        self.current_number = self._candidate(
            voice_url=self.initial_voice_url,
            voice_method="POST" if self.initial_voice_url is not None else None,
            sms_url=self.initial_sms_url,
            sms_method="POST" if self.initial_sms_url is not None else None,
            status_callback=self.initial_status_callback,
            status_callback_method="POST" if self.initial_status_callback is not None else None,
        )

    def definition(self) -> IntegrationProviderDefinition:
        return IntegrationProviderDefinition(
            provider=IntegrationProviderType.TWILIO,
            label="Twilio",
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
        credentials: TwilioCredentialBundle,
    ) -> IntegrationCredentialValidation:
        self.validate_calls += 1
        if credentials.auth_token.get_secret_value() != self.valid_auth_token:
            return IntegrationCredentialValidation(
                valid=False,
                status_message="Twilio Auth Token did not validate for the configured account.",
                safe_metadata={
                    "account_sid": credentials.account_sid,
                    "api_key_sid": credentials.api_key_sid,
                },
            )
        return IntegrationCredentialValidation(
            valid=True,
            safe_metadata={
                "account_sid": credentials.account_sid,
                "api_key_sid": credentials.api_key_sid,
                "account_friendly_name": "Test",
                "account_status": "active",
            },
        )

    async def list_phone_numbers(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        self.list_calls += 1
        if self.current_number is None:
            raise AssertionError(SEEDED_TWILIO_NUMBER_MISSING)
        return (self.current_number,)

    async def list_resources(
        self,
        credentials: TwilioCredentialBundle,
    ) -> tuple[TwilioPhoneNumberCandidate, ...]:
        return await self.list_phone_numbers(credentials)

    async def configure_phone_number_webhooks(
        self,
        *,
        credentials: TwilioCredentialBundle,
        phone_number_id: str,
        voice_url: str | None,
        voice_method: str | None,
        sms_url: str | None,
        sms_method: str | None,
        status_callback: str | None,
        status_callback_method: str | None,
        clear_missing_urls: bool = False,
    ) -> TwilioPhoneNumberCandidate:
        payload = twilio_update_payload(
            voice_url=voice_url,
            voice_method=voice_method,
            sms_url=sms_url,
            sms_method=sms_method,
            status_callback=status_callback,
            status_callback_method=status_callback_method,
            clear_missing_urls=clear_missing_urls,
        )
        self.captured_payloads.append(payload)
        self.current_number = self._candidate(
            voice_url=payload.get("VoiceUrl", [voice_url or ""])[0],
            voice_method=payload.get("VoiceMethod", [voice_method])[0],
            sms_url=payload.get("SmsUrl", [sms_url or ""])[0],
            sms_method=payload.get("SmsMethod", [sms_method])[0],
            status_callback=payload.get("StatusCallback", [status_callback or ""])[0],
            status_callback_method=payload.get(
                "StatusCallbackMethod",
                [status_callback_method],
            )[0],
        )
        return self.current_number

    async def update_phone_number_webhooks(
        self,
        *,
        credentials: TwilioCredentialBundle,
        phone_number_sid: str,
        voice_url: str | None,
        voice_method: str | None,
        sms_url: str | None,
        sms_method: str | None,
        status_callback: str | None,
        status_callback_method: str | None,
        clear_missing_urls: bool = False,
    ) -> TwilioPhoneNumberCandidate:
        return await self.configure_phone_number_webhooks(
            credentials=credentials,
            phone_number_id=phone_number_sid,
            voice_url=voice_url,
            voice_method=voice_method,
            sms_url=sms_url,
            sms_method=sms_method,
            status_callback=status_callback,
            status_callback_method=status_callback_method,
            clear_missing_urls=clear_missing_urls,
        )

    async def send_sms(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        if self.fail_send:
            raise TwilioProviderError(TWILIO_TEST_SEND_FAILURE)
        return IntegrationActionResponse(
            id="SM" + "5" * 32,
            provider=IntegrationProviderType.TWILIO,
            action="send_sms",
            external_id="SM" + "5" * 32,
            status="queued",
            safe_metadata={},
        )

    async def send_message(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        body: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        return await self.send_sms(
            credentials=credentials,
            from_number=from_number,
            to_number=to_number,
            body=body,
            status_callback_url=status_callback_url,
        )

    async def start_outbound_call(
        self,
        *,
        credentials: TwilioCredentialBundle,
        from_number: str,
        to_number: str,
        twiml_url: str,
        status_callback_url: str | None,
    ) -> IntegrationActionResponse:
        self.start_call_count += 1
        self.started_call_urls.append((twiml_url, status_callback_url))
        if self.fail_call:
            raise TwilioProviderError(TWILIO_TEST_CALL_FAILURE)
        return IntegrationActionResponse(
            id="CA" + "6" * 32,
            provider=IntegrationProviderType.TWILIO,
            action="place_call",
            external_id="CA" + "6" * 32,
            status="queued",
            safe_metadata={},
        )

    def _candidate(
        self,
        *,
        voice_url: str | None,
        voice_method: str | None,
        sms_url: str | None,
        sms_method: str | None,
        status_callback: str | None,
        status_callback_method: str | None,
    ) -> TwilioPhoneNumberCandidate:
        return TwilioPhoneNumberCandidate(
            sid=PHONE_NUMBER_SID,
            phone_number=PHONE_NUMBER,
            friendly_name="Support",
            capabilities=(
                IntegrationCapability.INBOUND_VOICE,
                IntegrationCapability.OUTBOUND_VOICE,
                IntegrationCapability.SMS_READ,
                IntegrationCapability.SMS_SEND,
            ),
            voice_url=voice_url,
            voice_method=voice_method,
            sms_url=sms_url,
            sms_method=sms_method,
            status_callback=status_callback,
            status_callback_method=status_callback_method,
        )


@pytest.mark.asyncio
async def test_twilio_setup_seals_secrets_and_lists_numbers(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )

        secrets = (
            await session.exec(
                select(UserSecretRecord).where(
                    col(UserSecretRecord.user_id) == "user-1",
                    col(UserSecretRecord.purpose) == SecretPurpose.INTEGRATION_CREDENTIAL.value,
                ),
            )
        ).all()
    assert response.integration.status == IntegrationStatus.UNTESTED
    assert response.integration.logo.domain == "twilio.com"
    assert response.integration.logo.label == "Twilio"
    assert response.credentials[0].last_four == "cret"
    assert response.phone_numbers[0].phone_number == PHONE_NUMBER
    assert len(secrets) == 2


@pytest.mark.asyncio
async def test_twilio_setup_rejects_invalid_auth_token(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        with pytest.raises(IntegrationCredentialError):
            await service(
                session=session,
                session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
                secret_vault=user_secret_vault,
                provider=provider,
            ).setup_twilio_credentials(
                user_id="user-1",
                request=setup_request(webhook_signing_value="wrong-value"),
            )
        integrations = (await session.exec(select(UserIntegrationRecord))).all()
        credentials = (await session.exec(select(IntegrationCredentialRecord))).all()
        secrets = (await session.exec(select(UserSecretRecord))).all()
        retry_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    assert integrations == []
    assert credentials == []
    assert secrets == []
    assert retry_response.integration.status == IntegrationStatus.UNTESTED


@pytest.mark.asyncio
async def test_twilio_setup_rejects_duplicate_label_before_provider_calls(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        await integration_service.setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
        validate_calls = provider.validate_calls
        list_calls = provider.list_calls
        with pytest.raises(
            IntegrationCredentialError,
            match=INTEGRATION_LABEL_EXISTS_ERROR,
        ):
            await integration_service.setup_twilio_credentials(
                user_id="user-1",
                request=setup_request(),
            )

    assert provider.validate_calls == validate_calls
    assert provider.list_calls == list_calls


def test_twilio_models_validate_phone_number_sids() -> None:
    selection = TwilioResourceSelection(phone_number_sid=f" {PHONE_NUMBER_SID} ")
    assert selection.phone_number_sid == PHONE_NUMBER_SID

    with pytest.raises(ValidationError, match="phone_number_sid must be a Twilio phone number SID"):
        TwilioResourceSelection(phone_number_sid="not-a-phone-number-sid")

    with pytest.raises(ValidationError, match="sid must be a Twilio phone number SID"):
        TwilioPhoneNumberCandidate(
            sid="not-a-phone-number-sid",
            phone_number=PHONE_NUMBER,
        )


def test_provider_registry_resolves_by_provider_key() -> None:
    provider = FakeTwilioProvider()
    registry = IntegrationProviderRegistry(
        providers={IntegrationProviderType.TWILIO: provider},
    )

    assert registry.provider_for(IntegrationProviderType.TWILIO) is provider
    with pytest.raises(ValueError, match=IntegrationProviderType.TELNYX.value):
        registry.provider_for(IntegrationProviderType.TELNYX)


@pytest.mark.asyncio
async def test_twilio_resource_connect_stores_number_and_webhook_routes(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        connect_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_response.integration.id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(
                    TwilioResourceSelection(
                        phone_number_sid=PHONE_NUMBER_SID,
                        transcript_storage_enabled=False,
                    ),
                ),
            ),
        )
        route_count = len((await session.exec(select(IntegrationWebhookRouteRecord))).all())
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.user_id) == "user-1",
                ),
            )
        ).one()
    assert connect_response.integration.status == IntegrationStatus.CONNECTED
    assert connect_response.resources[0].display_name == PHONE_NUMBER
    assert resource.safe_metadata["phone_number"] == "+15551234567"
    assert resource.safe_metadata["transcript_storage_enabled"] is False
    retained_metadata = connect_response.resources[0].retained_metadata_json
    previous_webhooks = retained_metadata["previous_webhooks"]
    current_webhooks = retained_metadata["current_webhooks"]
    assert isinstance(previous_webhooks, dict)
    assert isinstance(current_webhooks, dict)
    assert previous_webhooks["voice_url"] == "[redacted]"
    assert current_webhooks["media_stream_url"] == "[redacted]"
    stored_previous = resource.retained_metadata_json["previous_webhooks"]
    stored_current = resource.retained_metadata_json["current_webhooks"]
    assert isinstance(stored_previous, dict)
    assert isinstance(stored_current, dict)
    assert stored_previous["voice_url"] == "https://old.example.com/voice?token=secret"
    sms_url = stored_current["sms_url"]
    media_stream_url = stored_current["media_stream_url"]
    assert isinstance(sms_url, str)
    assert isinstance(media_stream_url, str)
    assert (
        sms_url == "https://api.example.com/api/integrations/webhooks/communications/twilio/sms/"
        f"{sms_url.rsplit('/', 1)[-1]}"
    )
    assert "/api/integrations/streams/communications/twilio/media/" in media_stream_url
    assert route_count == 4


@pytest.mark.asyncio
async def test_twilio_disconnect_clears_previously_empty_webhook_urls(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider(
        initial_voice_url=None,
        initial_sms_url=None,
        initial_status_callback=None,
    )
    captured_payloads = provider.captured_payloads
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).disconnect_integration(user_id="user-1", integration_id=integration_id)
    assert captured_payloads[-1]["VoiceUrl"] == [""]
    assert captured_payloads[-1]["SmsUrl"] == [""]
    assert captured_payloads[-1]["StatusCallback"] == [""]


@pytest.mark.asyncio
async def test_twilio_resource_reconnect_replaces_active_webhook_routes(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(TwilioResourceSelection(phone_number_sid=PHONE_NUMBER_SID),),
            ),
        )
        routes = (await session.exec(select(IntegrationWebhookRouteRecord))).all()
    active_routes = [route for route in routes if route.status == IntegrationRouteStatus.ACTIVE.value]
    disabled_routes = [route for route in routes if route.status == IntegrationRouteStatus.DISABLED.value]
    assert len(active_routes) == 4
    assert len(disabled_routes) == 4


@pytest.mark.asyncio
async def test_twilio_resource_reconnect_preserves_original_webhook_backup(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    captured_payloads = provider.captured_payloads
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(TwilioResourceSelection(phone_number_sid=PHONE_NUMBER_SID),),
            ),
        )
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.user_id) == "user-1",
                ),
            )
        ).one()
    stored_previous = resource.retained_metadata_json["previous_webhooks"]
    assert isinstance(stored_previous, dict)
    assert stored_previous["voice_url"] == "https://old.example.com/voice?token=secret"
    assert stored_previous["sms_url"] == "https://old.example.com/sms"
    assert stored_previous["status_callback"] == "https://old.example.com/status"
    assert len(captured_payloads) == 2


@pytest.mark.asyncio
async def test_active_integration_labels_are_unique_with_null_deleted_at(
    sqlite_session_factory,
) -> None:
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        session.add(
            UserIntegrationRecord(
                user_id="user-1",
                provider=IntegrationProviderType.TWILIO.value,
                label="Twilio",
                status=IntegrationStatus.CONNECTED.value,
                enabled=True,
                auth_type=IntegrationAuthType.API_KEY.value,
                setup_state="connected",
            ),
        )
        session.add(
            UserIntegrationRecord(
                user_id="user-1",
                provider=IntegrationProviderType.TWILIO.value,
                label="Twilio",
                status=IntegrationStatus.CONNECTED.value,
                enabled=True,
                auth_type=IntegrationAuthType.API_KEY.value,
                setup_state="connected",
            ),
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


@pytest.mark.asyncio
async def test_twilio_resource_connect_preapproves_default_agent_tools(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            authorization_service=SqlModelToolAuthorizationService(session_factory=sqlite_session_factory),
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        connect_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            authorization_service=SqlModelToolAuthorizationService(session_factory=sqlite_session_factory),
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_response.integration.id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(TwilioResourceSelection(phone_number_sid=PHONE_NUMBER_SID),),
            ),
        )
    default_agent = await AgentIdentityService(
        session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
    ).get_or_create_default_agent("user-1")
    async with sqlite_session_factory() as session:
        grants = (
            await session.exec(
                select(AgentToolGrantRecord).where(
                    col(AgentToolGrantRecord.user_id) == "user-1",
                    col(AgentToolGrantRecord.agent_id) == default_agent.id,
                    col(AgentToolGrantRecord.tool_source) == ToolGrantScope.INTEGRATION.value,
                    col(AgentToolGrantRecord.integration_id) == integration_response.integration.id,
                    col(AgentToolGrantRecord.status) == ToolGrantStatus.ALLOWED.value,
                ),
            )
        ).all()
        contact_search_grant = (
            await session.exec(
                select(AgentToolGrantRecord).where(
                    col(AgentToolGrantRecord.user_id) == "user-1",
                    col(AgentToolGrantRecord.agent_id) == default_agent.id,
                    col(AgentToolGrantRecord.tool_source) == ToolGrantScope.INTERNAL.value,
                    col(AgentToolGrantRecord.tool_name) == "search_contacts",
                ),
            )
        ).one()
    resource_id = connect_response.resources[0].id
    assert {grant.tool_name for grant in grants} == {
        provider_integration_tool_name(
            integration_id=integration_response.integration.id,
            resource_id=resource_id,
            action=TWILIO_SEND_SMS_TOOL_NAME,
        ),
        provider_integration_tool_name(
            integration_id=integration_response.integration.id,
            resource_id=resource_id,
            action=TWILIO_PLACE_CALL_TOOL_NAME,
        ),
    }
    assert contact_search_grant.status == ToolGrantStatus.ALLOWED.value
    assert contact_search_grant.risk == ToolRisk.LOW.value


@pytest.mark.asyncio
async def test_twilio_resource_connect_preserves_contact_search_denial(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    authorization_service = SqlModelToolAuthorizationService(
        session_factory=sqlite_session_factory,
    )
    default_agent = await AgentIdentityService(
        session_factory=tenant_scoped_session_factory(
            sqlite_session_factory,
            "user-1",
        ),
    ).get_or_create_default_agent("user-1")
    await authorization_service.set_grants(
        user_id="user-1",
        agent_id=default_agent.id,
        grants=(
            ToolGrantUpdate(
                tool_name="search_contacts",
                tool_source=ToolGrantScope.INTERNAL,
                status=ToolGrantStatus.DENIED,
                risk=ToolRisk.LOW,
            ),
        ),
    )
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        integration_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
            authorization_service=authorization_service,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
            authorization_service=authorization_service,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_response.integration.id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(
                    TwilioResourceSelection(
                        phone_number_sid=PHONE_NUMBER_SID,
                    ),
                ),
            ),
        )
    async with sqlite_session_factory() as session:
        contact_grant = (
            await session.exec(
                select(AgentToolGrantRecord).where(
                    col(AgentToolGrantRecord.user_id) == "user-1",
                    col(AgentToolGrantRecord.agent_id) == default_agent.id,
                    col(AgentToolGrantRecord.tool_source) == ToolGrantScope.INTERNAL.value,
                    col(AgentToolGrantRecord.tool_name) == "search_contacts",
                ),
            )
        ).one()
    assert contact_grant.status == ToolGrantStatus.DENIED.value


@pytest.mark.asyncio
async def test_twilio_disconnect_revokes_credentials_and_deletes_secrets(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).disconnect_integration(user_id="user-1", integration_id=integration_id)
        credentials = (
            await session.exec(
                select(IntegrationCredentialRecord).where(
                    col(IntegrationCredentialRecord.integration_id) == integration_id,
                ),
            )
        ).all()
        secrets = (
            await session.exec(
                select(UserSecretRecord).where(
                    col(UserSecretRecord.user_id) == "user-1",
                    col(UserSecretRecord.purpose) == SecretPurpose.INTEGRATION_CREDENTIAL.value,
                ),
            )
        ).all()
    assert {credential.status for credential in credentials} == {IntegrationCredentialStatus.REVOKED.value}
    assert all(credential.revoked_at is not None for credential in credentials)
    assert secrets == []


@pytest.mark.asyncio
async def test_transcript_storage_defaults_off_and_persists_only_when_enabled(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        call = await integration_service.start_twilio_call_session(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource.id,
            direction=IntegrationDirection.INBOUND,
            call_sid="CA" + "3" * 32,
            from_number="+15550000000",
            to_number=PHONE_NUMBER,
            payload_json={},
        )
        assert call.transcript_enabled is False
        assert (
            await integration_service.persist_call_transcript(
                user_id="user-1",
                call_session_id=call.id,
                source="test",
                speaker="caller",
                text="should not persist",
                is_final=True,
            )
            is None
        )
        resource.safe_metadata = {
            **resource.safe_metadata,
            "transcript_storage_enabled": True,
        }
        second_call = await integration_service.start_twilio_call_session(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource.id,
            direction=IntegrationDirection.INBOUND,
            call_sid="CA" + "4" * 32,
            from_number="+15550000000",
            to_number=PHONE_NUMBER,
            payload_json={},
        )
        transcript = await integration_service.persist_call_transcript(
            user_id="user-1",
            call_session_id=second_call.id,
            source="test",
            speaker="caller",
            text="persist me",
            is_final=True,
        )
        count = len((await session.exec(select(IntegrationCallTranscriptRecord))).all())
    assert second_call.transcript_enabled is True
    assert transcript is not None
    assert count == 1


@pytest.mark.asyncio
async def test_call_session_and_voice_demand_share_the_caller_transaction(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    requester = TransactionalVoiceDemandRequester()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_worker_demand_requester=requester,
        ).start_twilio_call_session(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource.id,
            direction=IntegrationDirection.INBOUND,
            call_sid="CA" + "5" * 32,
            from_number="+15550000000",
            to_number=PHONE_NUMBER,
            payload_json={},
        )
        assert requester.session_ids == [id(session)]
        assert len((await session.exec(select(IntegrationCallSessionRecord))).all()) == 1
        assert len((await session.exec(select(VoiceCallDemandRecord))).all()) == 1
        await session.rollback()

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        assert (await session.exec(select(IntegrationCallSessionRecord))).all() == []
        assert (await session.exec(select(VoiceCallDemandRecord))).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "from_number", "to_number"),
    (
        (IntegrationDirection.INBOUND, "+15550000000", PHONE_NUMBER),
        (IntegrationDirection.OUTBOUND, PHONE_NUMBER, "+15550000000"),
    ),
)
async def test_call_session_snapshots_remote_contact_access_once(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
    direction: IntegrationDirection,
    from_number: str,
    to_number: str,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        contact_service = UserContactService(session=session)
        contact = await contact_service.create_contact(
            user_id="user-1",
            request=ContactCreateRequest(
                display_name="Admin Caller",
                phone_number="+15550000000",
                is_admin=True,
            ),
        )
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        call = await integration_service.start_twilio_call_session(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource.id,
            direction=direction,
            call_sid="CA" + "6" * 32,
            from_number=from_number,
            to_number=to_number,
            payload_json={},
        )
        await contact_service.update_contact(
            user_id="user-1",
            contact_id=contact.id,
            request=ContactUpdateRequest(is_admin=False),
        )
        repeated = await integration_service.start_twilio_call_session(
            user_id="user-1",
            integration_id=integration_id,
            resource_id=resource.id,
            direction=direction,
            call_sid="CA" + "6" * 32,
            from_number=from_number,
            to_number=to_number,
            payload_json={"repeat": True},
        )

    assert call.contact_id == contact.id
    assert call.contact_display_name == "Admin Caller"
    assert call.caller_is_admin is True
    assert repeated.contact_id == contact.id
    assert repeated.caller_is_admin is True


@pytest.mark.asyncio
async def test_outbound_twilio_sms_and_calls_are_recorded(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        sms_result = await integration_service.send_twilio_sms(
            user_id="user-1",
            request=TwilioOutboundSmsRequest(
                integration_id=integration_id,
                resource_id=resource.id,
                agent_id="agent-1",
                to_number="+15557654321",
                body="Full body is retained.",
            ),
        )
        call_result = await integration_service.start_twilio_outbound_call(
            user_id="user-1",
            request=TwilioOutboundCallRequest(
                integration_id=integration_id,
                resource_id=resource.id,
                agent_id="agent-1",
                to_number="+15557654321",
                objective="Introduce yourself and share the update.",
            ),
        )
        message = (await session.exec(select(IntegrationMessageRecord))).one()
        call = (await session.exec(select(IntegrationCallSessionRecord))).one()
    assert sms_result.external_id == "SM" + "5" * 32
    assert call_result.external_id == "CA" + "6" * 32
    assert message.body == "Full body is retained."
    assert message.direction == IntegrationDirection.OUTBOUND.value
    assert call.direction == IntegrationDirection.OUTBOUND.value
    assert call.to_number == "+15557654321"
    assert call.outbound_objective == "Introduce yourself and share the update."
    assert call.media_participant_identity == f"call-{call.id}"
    assert call.worker_registered_at is not None
    assert call.provider_dial_requested_at is not None
    twiml_url, status_callback_url = provider.started_call_urls[0]
    assert parse_qs(urlsplit(twiml_url).query)["voxos_call_id"] == [call.id]
    assert status_callback_url is not None
    assert parse_qs(urlsplit(status_callback_url).query)["voxos_call_id"] == [
        call.id,
    ]


@pytest.mark.asyncio
async def test_outbound_twilio_call_preflights_voice_before_provider(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )

    async def missing_livekit_preflight(*, session: AsyncSession, user_id: str) -> None:
        del session, user_id
        raise MissingLiveKitCredentialsError

    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_call_preflight=missing_livekit_preflight,
        )

        with pytest.raises(MissingLiveKitCredentialsError):
            await integration_service.start_twilio_outbound_call(
                user_id="user-1",
                request=TwilioOutboundCallRequest(
                    integration_id=integration_id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    objective="Introduce yourself and share the update.",
                ),
            )

    assert provider.start_call_count == 0


@pytest.mark.asyncio
async def test_outbound_twilio_waits_for_committed_worker_demand_before_provider(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    requester = TransactionalVoiceDemandRequester()
    lifecycle_events: list[str] = []

    async def wait_for_worker(*, user_id: str, runtime_session_id: str) -> None:
        async with tenant_scoped_session_factory(
            sqlite_session_factory,
            user_id,
        )() as independent_session:
            call = (
                await independent_session.exec(
                    select(IntegrationCallSessionRecord).where(
                        col(IntegrationCallSessionRecord.runtime_session_id) == runtime_session_id,
                    ),
                )
            ).one()
            demand = (
                await independent_session.exec(
                    select(VoiceCallDemandRecord).where(
                        col(VoiceCallDemandRecord.runtime_session_id) == runtime_session_id,
                    ),
                )
            ).one()
        assert call.user_id == user_id
        assert demand.user_id == user_id
        assert provider.start_call_count == 0
        lifecycle_events.append("worker_ready")

    async def dispatch_agent(
        *,
        user_id: str,
        agent_id: str,
        runtime_session_id: str,
        room_name: str,
    ) -> None:
        del user_id, agent_id, runtime_session_id, room_name
        assert provider.start_call_count == 0
        lifecycle_events.append("agent_dispatched")

    async def wait_for_agent(*, user_id: str, runtime_session_id: str) -> None:
        del user_id, runtime_session_id
        assert provider.start_call_count == 0
        lifecycle_events.append("agent_ready")

    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
            voice_worker_demand_requester=requester,
            voice_worker_readiness_waiter=wait_for_worker,
            voice_agent_dispatcher=dispatch_agent,
            voice_agent_readiness_waiter=wait_for_agent,
        )

        await integration_service.start_twilio_outbound_call(
            user_id="user-1",
            request=TwilioOutboundCallRequest(
                integration_id=integration_id,
                resource_id=resource.id,
                agent_id="agent-1",
                to_number="+15557654321",
                objective="Introduce yourself and share the update.",
            ),
        )

    assert lifecycle_events == [
        "worker_ready",
        "agent_dispatched",
        "agent_ready",
    ]
    assert provider.start_call_count == 1


@pytest.mark.asyncio
async def test_outbound_twilio_provider_failures_are_domain_errors(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )
        provider.fail_send = True
        with pytest.raises(IntegrationResourceError):
            await integration_service.send_twilio_sms(
                user_id="user-1",
                request=TwilioOutboundSmsRequest(
                    integration_id=integration_id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    body="blocked",
                ),
            )
        provider.fail_send = False
        provider.fail_call = True
        with pytest.raises(IntegrationResourceError):
            await integration_service.start_twilio_outbound_call(
                user_id="user-1",
                request=TwilioOutboundCallRequest(
                    integration_id=integration_id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    objective="Introduce yourself and share the update.",
                ),
            )


@pytest.mark.asyncio
async def test_outbound_call_latency_report_returns_safe_phase_percentiles(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    now = utc_now()
    async with tenant_scoped_session_factory(
        sqlite_session_factory,
        "user-1",
    )() as session:
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        for index, answer_to_speech_ms in enumerate((100, 300), start=1):
            answered_at = now + timedelta(seconds=index)
            session.add(
                IntegrationCallSessionRecord(
                    user_id="user-1",
                    integration_id=integration_id,
                    resource_id=resource.id,
                    provider=IntegrationProviderType.TWILIO.value,
                    direction=IntegrationDirection.OUTBOUND.value,
                    external_call_id=f"latency-call-{index}",
                    outbound_objective="Caller-safe task.",
                    worker_registered_at=now,
                    agent_ready_at=now + timedelta(milliseconds=50 * index),
                    provider_dial_requested_at=now + timedelta(milliseconds=100 * index),
                    answered_at=answered_at,
                    media_ready_at=answered_at + timedelta(milliseconds=20),
                    opening_scheduled_at=answered_at + timedelta(milliseconds=50),
                    first_agent_speech_at=answered_at + timedelta(milliseconds=answer_to_speech_ms),
                    opening_completed_at=answered_at + timedelta(milliseconds=200 * index),
                ),
            )
        await session.commit()
        report = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(
                sqlite_session_factory,
                "user-1",
            ),
            secret_vault=user_secret_vault,
            provider=provider,
        ).call_latency_report(
            user_id="user-1",
            integration_id=integration_id,
            provider=IntegrationProviderType.TWILIO,
        )

    assert report.answer_to_first_speech.sample_count == 2
    assert report.answer_to_first_speech.p50_ms == 100
    assert report.answer_to_first_speech.p95_ms == 300
    assert report.worker_registration_to_agent_ready.p50_ms == 50
    assert report.worker_registration_to_agent_ready.p95_ms == 100
    assert report.opening_scheduled_to_playout_complete.p50_ms == 150
    assert report.opening_scheduled_to_playout_complete.p95_ms == 350
    assert "objective" not in report.model_dump(mode="json")


@pytest.mark.asyncio
async def test_outbound_actions_require_connected_integration(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    integration_id = await connected_integration(
        sqlite_session_factory=sqlite_session_factory,
        user_secret_vault=user_secret_vault,
        provider=provider,
    )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration = await session.get(UserIntegrationRecord, integration_id)
        assert integration is not None
        integration.enabled = False
        integration.status = IntegrationStatus.FAILED.value
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_id,
                ),
            )
        ).one()
        await session.commit()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )

        with pytest.raises(IntegrationResourceError):
            await integration_service.send_twilio_sms(
                user_id="user-1",
                request=TwilioOutboundSmsRequest(
                    integration_id=integration_id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    body="blocked",
                ),
            )


@pytest.mark.asyncio
async def test_outbound_actions_require_resource_capabilities(
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
) -> None:
    provider = FakeTwilioProvider()
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_response.integration.id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(
                    TwilioResourceSelection(
                        phone_number_sid=PHONE_NUMBER_SID,
                        capabilities=(IntegrationCapability.SMS_READ,),
                    ),
                ),
            ),
        )
        resource = (
            await session.exec(
                select(IntegrationResourceRecord).where(
                    col(IntegrationResourceRecord.integration_id) == integration_response.integration.id,
                ),
            )
        ).one()
        integration_service = service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        )

        with pytest.raises(IntegrationResourceError, match=TWILIO_CAPABILITY_UNSUPPORTED_ERROR):
            await integration_service.send_twilio_sms(
                user_id="user-1",
                request=TwilioOutboundSmsRequest(
                    integration_id=integration_response.integration.id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    body="blocked",
                ),
            )
        with pytest.raises(IntegrationResourceError, match=TWILIO_CAPABILITY_UNSUPPORTED_ERROR):
            await integration_service.start_twilio_outbound_call(
                user_id="user-1",
                request=TwilioOutboundCallRequest(
                    integration_id=integration_response.integration.id,
                    resource_id=resource.id,
                    agent_id="agent-1",
                    to_number="+15557654321",
                    objective="Introduce yourself and share the update.",
                ),
            )


async def connected_integration(
    *,
    sqlite_session_factory,
    user_secret_vault: UserSecretVaultService,
    provider: FakeTwilioProvider,
) -> str:
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        integration_response = await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).setup_twilio_credentials(
            user_id="user-1",
            request=setup_request(),
        )
    async with tenant_scoped_session_factory(sqlite_session_factory, "user-1")() as session:
        await service(
            session=session,
            session_factory=tenant_scoped_session_factory(sqlite_session_factory, "user-1"),
            secret_vault=user_secret_vault,
            provider=provider,
        ).connect_twilio_resources(
            user_id="user-1",
            integration_id=integration_response.integration.id,
            request=TwilioResourceConnectRequest(
                overwrite_existing_webhooks=True,
                resources=(TwilioResourceSelection(phone_number_sid=PHONE_NUMBER_SID),),
            ),
        )
    return integration_response.integration.id


def setup_request(webhook_signing_value: str = VALID_WEBHOOK_SIGNING_VALUE) -> TwilioCredentialSetupRequest:
    return TwilioCredentialSetupRequest(
        label="Twilio",
        account_sid=ACCOUNT_SID,
        api_key_sid=API_KEY_SID,
        api_key_secret=SecretStr("api-key-secret"),
        auth_token=SecretStr(webhook_signing_value),
    )


def service(
    *,
    session,
    session_factory,
    secret_vault: UserSecretVaultService,
    provider: FakeTwilioProvider,
    authorization_service=None,
    voice_worker_demand_requester: VoiceWorkerDemandRequester | None = None,
    voice_worker_readiness_waiter: VoiceWorkerReadinessWaiter | None = None,
    voice_agent_dispatcher: VoiceAgentDispatcher | None = None,
    voice_agent_readiness_waiter: VoiceAgentReadinessWaiter | None = None,
    voice_worker_demand_canceller: VoiceWorkerDemandCanceller | None = None,
    voice_call_preflight: VoiceCallPreflight | None = None,
) -> UserIntegrationService:
    return UserIntegrationService(
        session=session,
        secret_vault=secret_vault,
        public_base_url="https://api.example.com",
        provider_registry=IntegrationProviderRegistry(
            twilio=provider,
        ),
        session_factory=session_factory,
        authorization_service=authorization_service,
        voice_worker_demand_requester=(voice_worker_demand_requester or noop_voice_request),
        voice_worker_readiness_waiter=(voice_worker_readiness_waiter or noop_voice_wait),
        voice_agent_dispatcher=voice_agent_dispatcher or noop_voice_dispatch,
        voice_agent_readiness_waiter=voice_agent_readiness_waiter or noop_voice_wait,
        voice_worker_demand_canceller=voice_worker_demand_canceller,
        voice_call_preflight=voice_call_preflight or noop_voice_preflight,
    )


def twilio_update_payload(
    *,
    voice_url: str | None,
    voice_method: str | None,
    sms_url: str | None,
    sms_method: str | None,
    status_callback: str | None,
    status_callback_method: str | None,
    clear_missing_urls: bool,
) -> dict[str, list[str]]:
    payload: dict[str, list[str]] = {}
    update_url_payload(payload, "VoiceUrl", voice_url, clear_missing_urls=clear_missing_urls)
    update_url_payload(payload, "SmsUrl", sms_url, clear_missing_urls=clear_missing_urls)
    update_url_payload(
        payload,
        "StatusCallback",
        status_callback,
        clear_missing_urls=clear_missing_urls,
    )
    update_optional_payload(payload, "VoiceMethod", voice_method)
    update_optional_payload(payload, "SmsMethod", sms_method)
    update_optional_payload(payload, "StatusCallbackMethod", status_callback_method)
    return payload


def update_url_payload(
    payload: dict[str, list[str]],
    key: str,
    value: str | None,
    *,
    clear_missing_urls: bool,
) -> None:
    if value is not None:
        payload[key] = [value]
    elif clear_missing_urls:
        payload[key] = [""]


def update_optional_payload(
    payload: dict[str, list[str]],
    key: str,
    value: str | None,
) -> None:
    if value is not None:
        payload[key] = [value]
